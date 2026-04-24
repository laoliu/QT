"""Tick 级别回测引擎 — 基于逐笔数据回放的事件驱动引擎。

与 bar 级别引擎 (engine.py) 并行，复用相同的费用/滑点/风控基础设施。

事件流:
  每个交易日:
    对该日每个 tick（按时间排序）:
      1. 更新 tick 特征 (TickFeatureComputer)
      2. strategy.on_tick(TickContext) → OrderIntent[]
      3. tick 级别撮合 (即时成交 / BBO 成交)
      4. 风控 + 费用 + 滑点
    5. 日终结算 T+N
    6. 每日快照
"""
from __future__ import annotations

import logging
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from aipha.domain.models import DepthLevel, Side, Tick
from aipha.execution.fees import FeeModel, TradeFees
from aipha.execution.slippage import SlippageModel, create_slippage_model
from aipha.risk.constraints import CompositeRiskManager
from aipha.strategy.base import (
    OrderIntent,
    TickContext,
    TickFeatures,
    TickStrategy,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Tick 特征计算器（Python 侧，对应 TS TickFeatureStore）
# ============================================================================


class TickFeatureComputer:
    """从 tick 流中计算实时特征。"""

    def __init__(self, buffer_size: int = 256):
        self._buffer_size = buffer_size
        self._buffers: dict[str, list[Tick]] = defaultdict(list)

    def update(self, tick: Tick) -> TickFeatures:
        """输入一个 tick，返回当前特征。"""
        buf = self._buffers[tick.symbol]
        buf.append(tick)
        if len(buf) > self._buffer_size:
            buf.pop(0)

        return TickFeatures(
            symbol=tick.symbol,
            timestamp=tick.timestamp,
            last_price=tick.price,
            micro_vwap=self._micro_vwap(buf),
            short_momentum_pct=self._momentum(buf, 5),
            medium_momentum_pct=self._momentum(buf, 20),
            spread_bps=self._spread_bps(tick),
            order_book_imbalance=self._book_imbalance(tick),
            realized_vol_bps=self._realized_vol(buf),
            volume_delta=tick.volume - buf[-2].volume if len(buf) >= 2 else 0,
            amount_delta=tick.amount - buf[-2].amount if len(buf) >= 2 else 0.0,
        )

    def get_recent_ticks(self, symbol: str, n: int = 50) -> list[Tick]:
        return self._buffers.get(symbol, [])[-n:]

    def _micro_vwap(self, buf: list[Tick]) -> float:
        recent = buf[-20:]
        total_amount = sum(t.amount for t in recent if t.amount > 0)
        total_volume = sum(t.volume for t in recent if t.volume > 0)
        if total_volume > 0:
            return total_amount / total_volume
        return recent[-1].price if recent else 0.0

    def _momentum(self, buf: list[Tick], n: int) -> float:
        if len(buf) < n + 1:
            return 0.0
        old_price = buf[-n - 1].price
        if old_price <= 0:
            return 0.0
        return (buf[-1].price / old_price - 1) * 100

    def _spread_bps(self, tick: Tick) -> float:
        if tick.bid_levels and tick.ask_levels:
            mid = (tick.bid_levels[0].price + tick.ask_levels[0].price) / 2
            if mid > 0:
                return (tick.ask_levels[0].price - tick.bid_levels[0].price) / mid * 10000
        return 0.0

    def _book_imbalance(self, tick: Tick) -> float:
        bid_vol = sum(l.volume for l in tick.bid_levels) if tick.bid_levels else 0
        ask_vol = sum(l.volume for l in tick.ask_levels) if tick.ask_levels else 0
        total = bid_vol + ask_vol
        if total > 0:
            return (bid_vol - ask_vol) / total
        return 0.0

    def _realized_vol(self, buf: list[Tick]) -> float:
        if len(buf) < 3:
            return 0.0
        recent = buf[-20:]
        returns = []
        for i in range(1, len(recent)):
            if recent[i - 1].price > 0:
                returns.append(recent[i].price / recent[i - 1].price - 1)
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        return math.sqrt(variance) * 10000  # bps


# ============================================================================
# Tick 成交模型
# ============================================================================


class TickFillModel:
    """Tick 级别成交模型。

    支持三种模式:
      - "immediate": 以当前 tick 价格即时成交
      - "bbo": 按 BBO（最优买卖价）成交
      - "next_tick": 延迟到下一个 tick 成交
    """

    def __init__(self, mode: str = "bbo"):
        self._mode = mode
        self._pending: list[dict] = []  # next_tick 模式下的待成交订单

    def get_fill_price(self, tick: Tick, side: Side) -> float | None:
        """根据当前 tick 返回成交价。"""
        if self._mode == "immediate":
            return tick.price

        if self._mode == "bbo":
            if side == Side.BUY:
                # 买入以卖一价成交
                if tick.ask_levels:
                    return tick.ask_levels[0].price
                return tick.price
            else:
                # 卖出以买一价成交
                if tick.bid_levels:
                    return tick.bid_levels[0].price
                return tick.price

        # next_tick 模式在外部处理
        return tick.price


# ============================================================================
# Tick Aggregator（Python 侧，对应 TS TickAggregator）
# ============================================================================


class TickAggregator:
    """将 tick 聚合为指定间隔的 bar。"""

    def __init__(self, interval_seconds: int = 60):
        self._interval_ms = interval_seconds * 1000
        self._current_bars: dict[str, dict] = {}

    def add_tick(self, tick: Tick) -> dict | None:
        """添加 tick，返回已完成的 bar（如果时间桶切换），否则 None。"""
        ts_ms = int(tick.timestamp.timestamp() * 1000)
        bucket = (ts_ms // self._interval_ms) * self._interval_ms
        key = tick.symbol

        current = self._current_bars.get(key)
        completed = None

        if current and current["bucket"] != bucket:
            completed = current
            current = None

        if current is None:
            self._current_bars[key] = {
                "bucket": bucket,
                "symbol": tick.symbol,
                "open": tick.price,
                "high": tick.price,
                "low": tick.price,
                "close": tick.price,
                "volume": tick.volume,
                "amount": tick.amount,
                "count": 1,
            }
        else:
            current["high"] = max(current["high"], tick.price)
            current["low"] = min(current["low"], tick.price)
            current["close"] = tick.price
            current["volume"] += tick.volume
            current["amount"] += tick.amount
            current["count"] += 1

        return completed

    def flush(self) -> list[dict]:
        """刷出所有未完成的 bar。"""
        bars = list(self._current_bars.values())
        self._current_bars.clear()
        return bars


# ============================================================================
# 主引擎
# ============================================================================


@dataclass
class TickBacktestConfig:
    """Tick 回测引擎参数。"""

    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    commission_rate: float = 8.54e-05
    commission_min: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 5.0  # tick 级别默认 5bps（比 bar 更小）
    t_plus_n: int = 1
    fill_mode: str = "bbo"  # "immediate" | "bbo" | "next_tick"
    aggregate_interval: int = 60  # 聚合间隔（秒）


@dataclass
class TickTradeRecord:
    """Tick 级别交易记录。"""

    trade_id: str
    order_id: str
    trade_date: date
    trade_time: datetime
    symbol: str
    side: Side
    qty: int
    price: float
    notional: float
    fees: TradeFees
    tick_timestamp: datetime  # 实际成交的 tick 时间
    reason: str = ""

    @property
    def total_cost(self) -> float:
        return self.notional + self.fees.total if self.side == Side.BUY else self.notional - self.fees.total


@dataclass
class TickDailySnapshot:
    """每日快照（与 bar 引擎格式一致）。"""

    trade_date: date
    nav: float
    cash: float
    daily_return: float
    cumulative_return: float
    positions: dict[str, int]
    position_values: dict[str, float]
    tick_count: int = 0
    trade_count: int = 0


@dataclass
class TickBacktestResult:
    """Tick 回测结果。"""

    strategy_name: str
    start_date: date
    end_date: date
    initial_capital: float
    final_nav: float
    final_cash: float
    final_positions: dict[str, int]
    daily_snapshots: list[TickDailySnapshot]
    trades: list[TickTradeRecord]
    total_trades: int
    total_ticks_processed: int
    total_fees_commission: float = 0.0
    total_fees_stamp_tax: float = 0.0
    total_fees_transfer_fee: float = 0.0

    @property
    def total_fees(self) -> float:
        return self.total_fees_commission + self.total_fees_stamp_tax + self.total_fees_transfer_fee


class TickBacktestEngine:
    """Tick 级别事件驱动回测引擎。"""

    def __init__(
        self,
        config: TickBacktestConfig,
        strategy: TickStrategy,
        slippage_model: SlippageModel | None = None,
        fee_model: FeeModel | None = None,
        risk_manager: CompositeRiskManager | None = None,
    ):
        self._config = config
        self._strategy = strategy
        self._fill_model = TickFillModel(mode=config.fill_mode)
        self._slippage = slippage_model or create_slippage_model("fixed", bps=config.slippage_bps)
        self._fee_model = fee_model or FeeModel(
            commission_rate=config.commission_rate,
            commission_min=config.commission_min,
            stamp_tax_rate=config.stamp_tax_rate,
            transfer_fee_rate=config.transfer_fee_rate,
        )
        self._risk_manager = risk_manager
        self._feature_computer = TickFeatureComputer()
        self._aggregator = TickAggregator(interval_seconds=config.aggregate_interval)

        # 状态
        self._cash = config.initial_capital
        self._positions: dict[str, int] = {}
        self._pending_settlements: list[dict] = []
        self._trades: list[TickTradeRecord] = []
        self._snapshots: list[TickDailySnapshot] = []
        self._total_fees_commission = 0.0
        self._total_fees_stamp_tax = 0.0
        self._total_fees_transfer = 0.0
        self._prev_nav = config.initial_capital
        self._total_ticks = 0
        self._last_prices: dict[str, float] = {}

    def run(
        self,
        ticks_by_date: dict[date, dict[str, list[Tick]]],
    ) -> TickBacktestResult:
        """运行 tick 回测。

        Args:
            ticks_by_date: {交易日: {symbol: [Tick, ...]}} 时间有序
        """
        self._strategy.on_start()
        sorted_dates = sorted(ticks_by_date.keys())

        if not sorted_dates:
            return self._build_result(sorted_dates)

        for trading_date in sorted_dates:
            day_ticks = ticks_by_date[trading_date]

            # 日初结算 T+N
            self._settle(trading_date)

            # 合并所有标的的 tick，按时间排序
            all_ticks: list[Tick] = []
            for sym, ticks in day_ticks.items():
                all_ticks.extend(ticks)
            all_ticks.sort(key=lambda t: t.timestamp)

            day_trade_count = 0

            # 逐 tick 回放
            for tick in all_ticks:
                self._total_ticks += 1
                self._last_prices[tick.symbol] = tick.price

                # 计算特征
                features = self._feature_computer.update(tick)
                recent = self._feature_computer.get_recent_ticks(tick.symbol, 50)

                # 构建 tick 上下文
                nav = self._compute_nav()
                context = TickContext(
                    current_time=tick.timestamp,
                    positions=dict(self._positions),
                    cash=self._cash,
                    nav=nav,
                    tick=tick,
                    features=features,
                    recent_ticks=recent,
                )

                # 策略决策
                intents = self._strategy.on_tick(context)

                # 处理订单意图
                for intent in intents:
                    self._process_intent(intent, tick, trading_date, nav)
                    day_trade_count += 1

            # 日终快照
            nav_after = self._compute_nav()
            daily_ret = (nav_after / self._prev_nav - 1) if self._prev_nav > 0 else 0.0
            cum_ret = nav_after / self._config.initial_capital - 1

            self._snapshots.append(TickDailySnapshot(
                trade_date=trading_date,
                nav=nav_after,
                cash=self._cash,
                daily_return=daily_ret,
                cumulative_return=cum_ret,
                positions=dict(self._positions),
                position_values={
                    s: q * self._last_prices.get(s, 0)
                    for s, q in self._positions.items()
                },
                tick_count=len(all_ticks),
                trade_count=day_trade_count,
            ))
            self._prev_nav = nav_after

        self._strategy.on_end()
        return self._build_result(sorted_dates)

    def _process_intent(
        self,
        intent: OrderIntent,
        tick: Tick,
        trading_date: date,
        nav: float,
    ) -> None:
        """处理单个订单意图。"""
        qty = intent.qty

        # 风控
        if self._risk_manager:
            result = self._risk_manager.check(
                intent.symbol, qty, intent.side,
                self._positions, nav, self._last_prices,
            )
            if not result.passed:
                if result.adjusted_qty and result.adjusted_qty > 0:
                    qty = result.adjusted_qty
                else:
                    return

        # 手数取整
        qty = (qty // self._config.lot_size) * self._config.lot_size
        if qty <= 0:
            return

        # 获取成交价
        fill_price = self._fill_model.get_fill_price(tick, intent.side)
        if fill_price is None:
            return

        # 应用滑点
        fill_price = self._slippage.apply(
            fill_price, qty, intent.side, tick.volume,
        )

        # 计算费用
        notional = qty * fill_price
        trade_date_int = int(trading_date.strftime("%Y%m%d"))
        fees = self._fee_model.compute(notional, intent.side, trade_date_int)

        # 现金检查 + 执行
        if intent.side == Side.BUY:
            total_cost = notional + fees.total
            if total_cost > self._cash:
                affordable = (self._cash - fees.commission - fees.transfer_fee) / (
                    fill_price * (1 + self._fee_model._stamp_tax_rate)
                )
                qty = (int(affordable) // self._config.lot_size) * self._config.lot_size
                if qty <= 0:
                    return
                notional = qty * fill_price
                fees = self._fee_model.compute(notional, intent.side, trade_date_int)
                total_cost = notional + fees.total

            self._cash -= total_cost
            if self._config.t_plus_n > 0:
                self._pending_settlements.append({
                    "symbol": intent.symbol,
                    "qty": qty,
                    "settle_date": trading_date,
                    "days_remaining": self._config.t_plus_n,
                })
            else:
                self._positions[intent.symbol] = self._positions.get(intent.symbol, 0) + qty
        else:
            current = self._positions.get(intent.symbol, 0)
            sell_qty = min(qty, current)
            if sell_qty <= 0:
                return
            qty = sell_qty
            notional = qty * fill_price
            fees = self._fee_model.compute(notional, intent.side, trade_date_int)
            self._cash += notional - fees.total
            self._positions[intent.symbol] = current - qty
            if self._positions[intent.symbol] <= 0:
                del self._positions[intent.symbol]

        # 记录交易
        trade_id = uuid.uuid4().hex[:12]
        order_id = uuid.uuid4().hex[:12]
        self._trades.append(TickTradeRecord(
            trade_id=trade_id,
            order_id=order_id,
            trade_date=trading_date,
            trade_time=tick.timestamp,
            symbol=intent.symbol,
            side=intent.side,
            qty=qty,
            price=fill_price,
            notional=notional,
            fees=fees,
            tick_timestamp=tick.timestamp,
            reason=intent.reason or "",
        ))
        self._total_fees_commission += fees.commission
        self._total_fees_stamp_tax += fees.stamp_tax
        self._total_fees_transfer += fees.transfer_fee

        # 策略回调
        self._strategy.on_fill(intent.symbol, intent.side, qty, fill_price)

    def _settle(self, current_date: date) -> None:
        """T+N 结算。"""
        settled = []
        for s in self._pending_settlements:
            s["days_remaining"] -= 1
            if s["days_remaining"] <= 0:
                self._positions[s["symbol"]] = self._positions.get(s["symbol"], 0) + s["qty"]
                settled.append(s)
        for s in settled:
            self._pending_settlements.remove(s)

    def _compute_nav(self) -> float:
        position_value = sum(
            qty * self._last_prices.get(symbol, 0)
            for symbol, qty in self._positions.items()
        )
        pending_value = sum(
            s["qty"] * self._last_prices.get(s["symbol"], 0)
            for s in self._pending_settlements
        )
        return self._cash + position_value + pending_value

    def _build_result(self, sorted_dates: list[date]) -> TickBacktestResult:
        start = sorted_dates[0] if sorted_dates else date.today()
        end = sorted_dates[-1] if sorted_dates else date.today()
        final_nav = self._snapshots[-1].nav if self._snapshots else self._cash

        return TickBacktestResult(
            strategy_name=self._strategy.name,
            start_date=start,
            end_date=end,
            initial_capital=self._config.initial_capital,
            final_nav=final_nav,
            final_cash=self._cash,
            final_positions=dict(self._positions),
            daily_snapshots=self._snapshots,
            trades=self._trades,
            total_trades=len(self._trades),
            total_ticks_processed=self._total_ticks,
            total_fees_commission=self._total_fees_commission,
            total_fees_stamp_tax=self._total_fees_stamp_tax,
            total_fees_transfer_fee=self._total_fees_transfer,
        )
