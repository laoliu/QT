"""CB 日内 T+0 策略基类。

可转债 T+0 交易允许同日买卖，日内策略利用此特性在
分钟级别捕捉价格偏离（VWAP、跳空、均值回归等）。

架构设计：
- CBIntradayBase 继承 Strategy，重写 on_bar 以处理分钟级 bar
- 子类实现 on_minute_bar() 产生日内交易信号
- 日内持仓到尾盘必须平仓（T+0 约束）
- 配合 MinuteBarProvider 提供数据
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Any

from aipha.domain.models import Side
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext
from aipha.data.stream import Bar, BarFrequency

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 枚举 & 配置
# ------------------------------------------------------------------

class IntradaySignal(Enum):
    """日内信号类型。"""
    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    FLAT = "flat"      # 强制平仓


@dataclass
class CBIntradayConfig:
    """CB 日内策略通用配置。

    Attributes:
        symbols: 可交易标的列表（空=全市场）
        max_position_pct: 单票最大仓位占比
        flat_before_close_min: 收盘前N分钟强制平仓
        max_intraday_trades: 单日单票最大交易次数
        slippage_bps: 日内滑点 (bps)
        min_tick: 最小价格变动
        lot_size: 最小交易单位（CB=10张）
    """
    symbols: list[str] = field(default_factory=list)
    max_position_pct: float = 0.05
    flat_before_close_min: int = 5
    max_intraday_trades: int = 10
    slippage_bps: float = 3.0  # 注意：低于§13.1 CB默认5.0，日内策略有意为之（滑点更小）
    min_tick: float = 0.001
    lot_size: int = 10


# ------------------------------------------------------------------
# 日内状态追踪
# ------------------------------------------------------------------

@dataclass
class IntradayState:
    """单日单票日内交易状态。"""
    symbol: str
    trade_date: datetime
    entry_price: float = 0.0
    qty: int = 0
    pnl: float = 0.0
    trade_count: int = 0
    vwap: float = 0.0
    cumulative_volume: float = 0.0
    cumulative_amount: float = 0.0
    high_of_day: float = 0.0
    low_of_day: float = float("inf")

    def update_vwap(self, price: float, volume: float) -> None:
        """增量更新 VWAP。"""
        amount = price * volume
        self.cumulative_volume += volume
        self.cumulative_amount += amount
        if self.cumulative_volume > 0:
            self.vwap = self.cumulative_amount / self.cumulative_volume
        self.high_of_day = max(self.high_of_day, price)
        if price > 0:
            self.low_of_day = min(self.low_of_day, price)


# ------------------------------------------------------------------
# 基类
# ------------------------------------------------------------------

class CBIntradayBase(Strategy):
    """CB 日内 T+0 策略基类。

    子类须实现 ``on_minute_bar()`` 返回 IntradaySignal。
    基类处理：
    - 日内状态管理（VWAP、高低点、交易计数）
    - 尾盘强制平仓
    - 最大交易次数限制
    - lot_size 对齐
    """

    name = "cb_intraday_base"

    def __init__(self, config: CBIntradayConfig | None = None) -> None:
        self._config = config or CBIntradayConfig()
        # symbol -> IntradayState
        self._states: dict[str, IntradayState] = {}
        self._current_date: datetime | None = None

    @property
    def config(self) -> CBIntradayConfig:
        return self._config

    @abstractmethod
    def on_minute_bar(
        self,
        bar: Bar,
        state: IntradayState,
        context: StrategyContext,
    ) -> IntradaySignal:
        """子类实现：根据分钟 bar + 日内状态返回信号。"""
        ...

    # -- Strategy 接口 --

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """处理分钟级 bar，生成日内订单。"""
        orders: list[OrderIntent] = []
        now = context.current_time
        cfg = self._config

        # 日期切换：重置状态
        if self._current_date is None or now.date() != self._current_date.date():
            self._reset_daily(now)

        for symbol, bar in bars.items():
            if cfg.symbols and symbol not in cfg.symbols:
                continue

            state = self._get_or_create_state(symbol, now)
            state.update_vwap(bar.close, bar.volume)

            # 尾盘强制平仓
            flat_time = time(14, 60 - cfg.flat_before_close_min)
            if now.time() >= flat_time and state.qty != 0:
                orders.extend(self._flat_position(symbol, bar, state))
                continue

            # 交易次数限制
            if state.trade_count >= cfg.max_intraday_trades:
                continue

            signal = self.on_minute_bar(bar, state, context)
            order = self._signal_to_order(signal, symbol, bar, state, context)
            if order:
                orders.append(order)

        return orders

    # -- 辅助方法 --

    def _reset_daily(self, now: datetime) -> None:
        """日期切换时清空日内状态。"""
        self._states.clear()
        self._current_date = now

    def _get_or_create_state(
        self, symbol: str, now: datetime,
    ) -> IntradayState:
        """获取或创建日内状态。"""
        if symbol not in self._states:
            self._states[symbol] = IntradayState(
                symbol=symbol, trade_date=now,
            )
        return self._states[symbol]

    def _flat_position(
        self, symbol: str, bar: Bar, state: IntradayState,
    ) -> list[OrderIntent]:
        """强制平仓。"""
        if state.qty == 0:
            return []
        side = Side.SELL if state.qty > 0 else Side.BUY
        qty = abs(state.qty)
        state.trade_count += 1
        state.qty = 0
        logger.debug("Flat %s: %s %d @ %.3f", symbol, side.value, qty, bar.close)
        return [OrderIntent(symbol=symbol, side=side, qty=qty, limit_price=bar.close)]

    def _signal_to_order(
        self,
        signal: IntradaySignal,
        symbol: str,
        bar: Bar,
        state: IntradayState,
        context: StrategyContext,
    ) -> OrderIntent | None:
        """将信号转换为 OrderIntent。"""
        cfg = self._config
        if signal == IntradaySignal.HOLD:
            return None

        if signal == IntradaySignal.FLAT:
            orders = self._flat_position(symbol, bar, state)
            return orders[0] if orders else None

        if signal == IntradaySignal.BUY and state.qty <= 0:
            qty = self._calc_qty(bar.close, context.nav, cfg)
            if qty <= 0:
                return None
            state.qty += qty
            state.entry_price = bar.close
            state.trade_count += 1
            return OrderIntent(
                symbol=symbol, side=Side.BUY, qty=qty, limit_price=bar.close,
            )

        if signal == IntradaySignal.SELL and state.qty > 0:
            qty = state.qty
            state.pnl += (bar.close - state.entry_price) * qty
            state.qty = 0
            state.trade_count += 1
            return OrderIntent(
                symbol=symbol, side=Side.SELL, qty=qty, limit_price=bar.close,
            )

        return None

    @staticmethod
    def _calc_qty(
        price: float, nav: float, cfg: CBIntradayConfig,
    ) -> int:
        """计算符合 lot_size 的买入数量。"""
        if price <= 0 or nav <= 0:
            return 0
        max_amount = nav * cfg.max_position_pct
        raw_qty = int(max_amount / price)
        # 对齐 lot_size
        return (raw_qty // cfg.lot_size) * cfg.lot_size
