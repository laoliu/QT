"""可转债日内策略基类"""
from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import TYPE_CHECKING, Any

from aipha.domain.models import Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


class IntradaySignal(Enum):
    HOLD = "hold"
    BUY = "buy"
    SELL = "sell"
    FLAT = "flat"


@dataclass
class CBIntradayConfig:
    symbols: list[str] = field(default_factory=list)
    max_position_pct: float = 0.05
    flat_before_close_min: int = 5
    max_intraday_trades: int = 10
    slippage_bps: float = 3.0
    min_tick: float = 0.001
    lot_size: int = 10


@dataclass
class IntradayState:
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
        self.cumulative_volume += volume
        self.cumulative_amount += price * volume
        if self.cumulative_volume > 0:
            self.vwap = self.cumulative_amount / self.cumulative_volume
        self.high_of_day = max(self.high_of_day, price)
        self.low_of_day = min(self.low_of_day, price)


class CBIntradayBase(Strategy):
    """可转债日内策略抽象基类

    子类实现 on_minute_bar() 返回信号, 基类处理订单管理 / 尾盘平仓 / 交易计数等。
    """
    name = "cb_intraday_base"

    def __init__(self, config: CBIntradayConfig | None = None) -> None:
        self._config = config or CBIntradayConfig()
        self._states: dict[str, IntradayState] = {}
        self._current_date: datetime | None = None

    @property
    def config(self) -> CBIntradayConfig:
        return self._config

    @abstractmethod
    def on_minute_bar(
        self, bar: "Bar", state: IntradayState, context: StrategyContext,
    ) -> IntradaySignal:
        ...

    def on_bar(self, bars: dict[str, "Bar"], context: StrategyContext) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        for sym, bar in bars.items():
            if self._config.symbols and sym not in self._config.symbols:
                continue
            now = getattr(bar, "datetime", datetime.now())
            self._maybe_reset_daily(now)
            state = self._get_or_create_state(sym, now)
            state.update_vwap(bar.close, getattr(bar, "volume", 0))

            # 尾盘平仓
            close_time = time(14, 55)
            if hasattr(now, "time") and now.time() >= close_time:
                intents.extend(self._flat_position(sym, bar, state))
                continue

            if state.trade_count >= self._config.max_intraday_trades:
                continue

            signal = self.on_minute_bar(bar, state, context)
            if signal == IntradaySignal.BUY and state.qty == 0:
                intents.append(OrderIntent(
                    symbol=sym, intent_type=IntentType.ENTRY,
                    side=Side.BUY, reason="intraday_buy",
                ))
                state.entry_price = bar.close
                state.trade_count += 1
            elif signal == IntradaySignal.SELL and state.qty > 0:
                intents.append(OrderIntent(
                    symbol=sym, intent_type=IntentType.EXIT,
                    side=Side.SELL, reason="intraday_sell",
                ))
                state.pnl += (bar.close - state.entry_price) * state.qty
                state.qty = 0
                state.trade_count += 1
            elif signal == IntradaySignal.FLAT:
                intents.extend(self._flat_position(sym, bar, state))

        return intents

    def _maybe_reset_daily(self, now: datetime) -> None:
        if self._current_date is None or (hasattr(now, "date") and now.date() != self._current_date.date()):
            self._states.clear()
            self._current_date = now

    def _get_or_create_state(self, symbol: str, now: datetime) -> IntradayState:
        if symbol not in self._states:
            self._states[symbol] = IntradayState(symbol=symbol, trade_date=now)
        return self._states[symbol]

    def _flat_position(
        self, symbol: str, bar: "Bar", state: IntradayState,
    ) -> list[OrderIntent]:
        if state.qty > 0:
            state.pnl += (bar.close - state.entry_price) * state.qty
            state.qty = 0
            return [OrderIntent(
                symbol=symbol, intent_type=IntentType.EXIT,
                side=Side.SELL, reason="flat_eod",
            )]
        return []
