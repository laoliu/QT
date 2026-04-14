"""事件驱动回测引擎

核心回测引擎，依赖注入设计。

事件流:
  每个交易日:
    1. on_bar → strategy.on_bar(context) → OrderIntent[]
    2. 风控校验 (market rules + risk constraints)
    3. 下单 → 成交撮合 (fill_model)
    4. 费用 + 滑点
    5. 结算持仓 (T+N)
    6. 更新 NAV + 快照
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from aipha.domain.models import Bar, Fill, Order, OrderType, Side
from aipha.execution.fees import FeeModel, TradeFees
from aipha.execution.fill_models import FillModel, create_fill_model
from aipha.execution.slippage import SlippageModel, create_slippage_model
from aipha.risk.constraints import CompositeRiskManager, RiskCheckResult
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class BacktestConfig:
    """回测引擎参数"""

    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    commission_rate: float = 8.54e-05
    commission_min: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 10.0
    t_plus_n: int = 1


@dataclass
class TradeRecord:
    """交易记录"""

    trade_id: str
    order_id: str
    trade_date: date
    trade_time: Optional[datetime]
    symbol: str
    side: Side
    qty: int
    price: float
    notional: float
    fees: TradeFees
    reason: str = ""

    @property
    def total_cost(self) -> float:
        return self.notional + self.fees.total if self.side == Side.BUY else self.notional - self.fees.total


@dataclass
class DailySnapshot:
    """每日快照"""

    trade_date: date
    nav: float
    cash: float
    frozen_cash: float
    daily_return: float
    cumulative_return: float
    positions: dict[str, int]
    position_values: dict[str, float]


@dataclass
class TotalFees:
    """累计费用"""
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0

    @property
    def total(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee


@dataclass
class BacktestResult:
    """回测结果"""

    strategy_name: str
    start_date: date
    end_date: date
    initial_capital: float
    final_nav: float
    final_cash: float
    final_positions: dict[str, int]
    daily_snapshots: list[DailySnapshot]
    trades: list[TradeRecord]
    total_trades: int
    total_fees: TotalFees


class BacktestEngine:
    """事件驱动回测引擎"""

    def __init__(
        self,
        config: BacktestConfig,
        strategy: Strategy,
        fill_model: FillModel | None = None,
        slippage_model: SlippageModel | None = None,
        fee_model: FeeModel | None = None,
        risk_manager: CompositeRiskManager | None = None,
    ):
        self._config = config
        self._strategy = strategy
        self._fill_model = fill_model or create_fill_model("next_bar_open")
        self._slippage = slippage_model or create_slippage_model("fixed", bps=config.slippage_bps)
        self._fee_model = fee_model or FeeModel(
            commission_rate=config.commission_rate,
            commission_min=config.commission_min,
            stamp_tax_rate=config.stamp_tax_rate,
            transfer_fee_rate=config.transfer_fee_rate,
        )
        self._risk_manager = risk_manager

        # 状态
        self._cash = config.initial_capital
        self._frozen_cash = 0.0
        self._positions: dict[str, int] = {}
        self._pending_settlements: list[dict] = []
        self._trades: list[TradeRecord] = []
        self._snapshots: list[DailySnapshot] = []
        self._total_fees = TotalFees()
        self._prev_nav = config.initial_capital

    def run(self, bars_by_date: dict[date, dict[str, Bar]]) -> BacktestResult:
        """运行回测。

        Args:
            bars_by_date: {交易日: {symbol: Bar}} 时间有序
        """
        self._strategy.on_start()
        sorted_dates = sorted(bars_by_date.keys())

        if not sorted_dates:
            return self._build_result(sorted_dates)

        for i, trading_date in enumerate(sorted_dates):
            bars = bars_by_date[trading_date]
            prices = {s: b.close for s, b in bars.items()}

            # 结算 T+N
            self._settle(trading_date)

            # 计算 NAV
            nav = self._compute_nav(prices)

            # 构建策略上下文
            context = StrategyContext(
                current_time=datetime(trading_date.year, trading_date.month, trading_date.day),
                positions=dict(self._positions),
                cash=self._cash,
                nav=nav,
            )

            # 策略生成订单意图
            intents = self._strategy.on_bar(bars, context)

            # 处理下一个 bar 的成交（如果不是最后一天）
            if i + 1 < len(sorted_dates):
                next_date = sorted_dates[i + 1]
                next_bars = bars_by_date[next_date]
                self._process_intents(intents, next_bars, next_date, prices, nav)

            # 每日快照
            nav_after = self._compute_nav(prices)
            daily_ret = (nav_after / self._prev_nav - 1) if self._prev_nav > 0 else 0.0
            cum_ret = (nav_after / self._config.initial_capital - 1)

            self._snapshots.append(DailySnapshot(
                trade_date=trading_date,
                nav=nav_after,
                cash=self._cash,
                frozen_cash=self._frozen_cash,
                daily_return=daily_ret,
                cumulative_return=cum_ret,
                positions=dict(self._positions),
                position_values={s: q * prices.get(s, 0) for s, q in self._positions.items()},
            ))
            self._prev_nav = nav_after

        self._strategy.on_end()
        return self._build_result(sorted_dates)

    def _process_intents(
        self,
        intents: list[OrderIntent],
        next_bars: dict[str, Bar],
        next_date: date,
        current_prices: dict[str, float],
        nav: float,
    ) -> None:
        for intent in intents:
            # 风控检查
            if self._risk_manager:
                result = self._risk_manager.check(
                    intent.symbol, intent.qty, intent.side,
                    self._positions, nav, current_prices,
                )
                if not result.passed:
                    if result.adjusted_qty and result.adjusted_qty > 0:
                        qty = result.adjusted_qty
                    else:
                        continue
                else:
                    qty = intent.qty
            else:
                qty = intent.qty

            # 手数取整
            qty = (qty // self._config.lot_size) * self._config.lot_size
            if qty <= 0:
                continue

            # 获取成交价
            next_bar = next_bars.get(intent.symbol)
            if not next_bar:
                continue

            fill_price = self._fill_model.get_fill_price(next_bar, intent.side.value)
            if fill_price is None:
                continue

            # 应用滑点
            fill_price = self._slippage.apply(
                fill_price, qty, intent.side, next_bar.volume,
            )

            # 计算费用
            notional = qty * fill_price
            trade_date_int = int(next_date.strftime("%Y%m%d"))
            fees = self._fee_model.compute(notional, intent.side, trade_date_int)

            # 检查现金够不够
            if intent.side == Side.BUY:
                total_cost = notional + fees.total
                if total_cost > self._cash:
                    # 缩减到可承受
                    affordable = (self._cash - fees.commission - fees.transfer_fee) / (fill_price * (1 + self._fee_model._stamp_tax_rate))
                    qty = (int(affordable) // self._config.lot_size) * self._config.lot_size
                    if qty <= 0:
                        continue
                    notional = qty * fill_price
                    fees = self._fee_model.compute(notional, intent.side, trade_date_int)
                    total_cost = notional + fees.total

            # 执行交易
            trade_id = uuid.uuid4().hex[:12]
            order_id = uuid.uuid4().hex[:12]

            if intent.side == Side.BUY:
                self._cash -= (notional + fees.total)
                if self._config.t_plus_n > 0:
                    self._pending_settlements.append({
                        "symbol": intent.symbol,
                        "qty": qty,
                        "settle_date": next_date,
                        "t_plus": self._config.t_plus_n,
                        "days_remaining": self._config.t_plus_n,
                    })
                else:
                    self._positions[intent.symbol] = self._positions.get(intent.symbol, 0) + qty
            else:
                current = self._positions.get(intent.symbol, 0)
                sell_qty = min(qty, current)
                if sell_qty <= 0:
                    continue
                qty = sell_qty
                notional = qty * fill_price
                fees = self._fee_model.compute(notional, intent.side, trade_date_int)
                self._cash += (notional - fees.total)
                self._positions[intent.symbol] = current - qty
                if self._positions[intent.symbol] <= 0:
                    del self._positions[intent.symbol]

            # 记录
            self._trades.append(TradeRecord(
                trade_id=trade_id,
                order_id=order_id,
                trade_date=next_date,
                trade_time=datetime(next_date.year, next_date.month, next_date.day, 9, 30),
                symbol=intent.symbol,
                side=intent.side,
                qty=qty,
                price=fill_price,
                notional=notional,
                fees=fees,
                reason=intent.reason or "",
            ))
            self._total_fees.commission += fees.commission
            self._total_fees.stamp_tax += fees.stamp_tax
            self._total_fees.transfer_fee += fees.transfer_fee

            # 回调
            self._strategy.on_fill(intent.symbol, intent.side, qty, fill_price)

    def _settle(self, current_date: date) -> None:
        """处理 T+N 结算"""
        settled = []
        for s in self._pending_settlements:
            s["days_remaining"] -= 1
            if s["days_remaining"] <= 0:
                self._positions[s["symbol"]] = self._positions.get(s["symbol"], 0) + s["qty"]
                settled.append(s)
        for s in settled:
            self._pending_settlements.remove(s)

    def _compute_nav(self, prices: dict[str, float]) -> float:
        position_value = sum(
            qty * prices.get(symbol, 0)
            for symbol, qty in self._positions.items()
        )
        pending_value = sum(
            s["qty"] * prices.get(s["symbol"], 0)
            for s in self._pending_settlements
        )
        return self._cash + position_value + pending_value

    def _build_result(self, sorted_dates: list[date]) -> BacktestResult:
        start = sorted_dates[0] if sorted_dates else date.today()
        end = sorted_dates[-1] if sorted_dates else date.today()
        prices = {}
        if self._snapshots:
            prices = {s: v for s, v in self._snapshots[-1].position_values.items()}
        nav = self._compute_nav({
            s: (prices.get(s, 0) / q if q > 0 else 0)
            for s, q in self._positions.items()
        }) if self._positions else self._cash

        return BacktestResult(
            strategy_name=self._strategy.name,
            start_date=start,
            end_date=end,
            initial_capital=self._config.initial_capital,
            final_nav=self._snapshots[-1].nav if self._snapshots else self._cash,
            final_cash=self._cash,
            final_positions=dict(self._positions),
            daily_snapshots=self._snapshots,
            trades=self._trades,
            total_trades=len(self._trades),
            total_fees=self._total_fees,
        )
