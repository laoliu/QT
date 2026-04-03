"""
tick_flow — Tick-level large order flow tracking engine.

Provides real-time tick-by-tick analysis of:
  - 大单净额 (main force net amount): cumulative buy/sell amounts for large orders
  - 扫单检测 (sweep detection): detect large aggressive buy orders near limit-up
  - 成交方向判定 (trade direction classification): buy vs sell tick based on bid/ask

Designed for QMT tick-driven strategies. Each stock maintains independent state.

Terminology:
  - 大单 (large order): single tick amount >= threshold (default 100万)
  - 主力净额 (main net): cumulative large buy - cumulative large sell
  - 扫单 (sweep): single tick volume >= threshold in high-change zone (aggressive buy)

Usage:
  tracker = TickFlowTracker()
  tracker.reset("600519")  # daily reset
  result = tracker.update("600519", tick_data)
  # result.main_net, result.sweep_triggered, etc.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TickFlowResult:
    """Result from a single tick update."""

    # Current tick
    is_buy: bool = True
    delta_volume: int = 0
    delta_amount: float = 0.0

    # Cumulative large-order flow
    main_buy_amount: float = 0.0
    main_sell_amount: float = 0.0
    main_net: float = 0.0

    # Sweep signal
    is_sweep_tick: bool = False       # this tick qualifies as sweep
    sweep_triggered: bool = False     # any sweep detected today

    # Change pct (vs pre_close)
    change_pct: float = 0.0


@dataclass
class _StockTickState:
    """Internal per-stock tick tracking state."""

    prev_volume: int = 0
    prev_amount: float = 0.0
    prev_price: float = 0.0
    main_buy_amount: float = 0.0
    main_sell_amount: float = 0.0
    sweep_triggered: bool = False
    first_tick: bool = True
    last_date: str = ""


class TickFlowTracker:
    """Track tick-level large order flow for multiple stocks.

    Parameters:
        large_order_threshold: minimum tick amount (元) to classify as large order.
            Default 1_000_000 (100万).
        sweep_volume_threshold: minimum tick volume (股) for sweep detection.
            Default 1_000_000 (100万股 = 1万手 for A-share lots).
        sweep_change_low: minimum change% for sweep zone. Default 0.08 (8%).
        sweep_change_high: maximum change% for sweep zone. Default 0.09 (9%).
    """

    def __init__(
        self,
        large_order_threshold: float = 1_000_000,
        sweep_volume_threshold: int = 1_000_000,
        sweep_change_low: float = 0.08,
        sweep_change_high: float = 0.09,
    ):
        self.large_order_threshold = large_order_threshold
        self.sweep_volume_threshold = sweep_volume_threshold
        self.sweep_change_low = sweep_change_low
        self.sweep_change_high = sweep_change_high
        self._states: dict[str, _StockTickState] = {}

    def reset(self, stock: str, date: str = "") -> None:
        """Reset a stock's tick state (call at start of each trading day)."""
        self._states[stock] = _StockTickState(last_date=date)

    def reset_all(self, date: str = "") -> None:
        """Reset all stocks."""
        for stock in list(self._states):
            self.reset(stock, date)

    def get_state(self, stock: str) -> _StockTickState | None:
        """Get internal state for debugging/inspection."""
        return self._states.get(stock)

    def update(
        self,
        stock: str,
        cur_price: float,
        cur_volume: int,
        cur_amount: float,
        pre_close: float,
        bid_price: float = 0.0,
        ask_price: float = 0.0,
        date: str = "",
    ) -> TickFlowResult | None:
        """Process a new tick for a stock.

        Args:
            stock: stock code (e.g. "600519.SH")
            cur_price: latest price
            cur_volume: cumulative volume (股) up to this tick
            cur_amount: cumulative amount (元) up to this tick
            pre_close: previous close price
            bid_price: best bid (买一价), 0 if unavailable
            ask_price: best ask (卖一价), 0 if unavailable
            date: trading date string for daily reset detection

        Returns:
            TickFlowResult with flow metrics, or None if tick is invalid/first-tick.
        """
        if cur_price <= 0 or pre_close <= 0:
            return None

        state = self._states.get(stock)
        if state is None:
            state = _StockTickState(last_date=date)
            self._states[stock] = state

        # Daily reset
        if date and date != state.last_date:
            self.reset(stock, date)
            state = self._states[stock]

        # First tick: record baseline
        if state.first_tick:
            state.prev_volume = cur_volume
            state.prev_amount = cur_amount
            state.prev_price = cur_price
            state.first_tick = False
            return None

        # Compute tick delta
        delta_vol = cur_volume - state.prev_volume
        delta_amount = cur_amount - state.prev_amount
        prev_price = state.prev_price

        state.prev_volume = cur_volume
        state.prev_amount = cur_amount
        state.prev_price = cur_price

        if delta_vol <= 0:
            return TickFlowResult(
                main_buy_amount=state.main_buy_amount,
                main_sell_amount=state.main_sell_amount,
                main_net=state.main_buy_amount - state.main_sell_amount,
                sweep_triggered=state.sweep_triggered,
                change_pct=(cur_price - pre_close) / pre_close,
            )

        # Classify direction
        is_buy = classify_tick_direction(cur_price, bid_price, ask_price, prev_price)

        # Accumulate large orders
        if delta_amount >= self.large_order_threshold:
            if is_buy:
                state.main_buy_amount += delta_amount
            else:
                state.main_sell_amount += delta_amount

        main_net = state.main_buy_amount - state.main_sell_amount
        change_pct = (cur_price - pre_close) / pre_close

        # Sweep detection
        is_sweep_tick = False
        if (
            self.sweep_change_low <= change_pct <= self.sweep_change_high
            and delta_vol >= self.sweep_volume_threshold
            and is_buy
        ):
            is_sweep_tick = True
            state.sweep_triggered = True

        return TickFlowResult(
            is_buy=is_buy,
            delta_volume=delta_vol,
            delta_amount=delta_amount,
            main_buy_amount=state.main_buy_amount,
            main_sell_amount=state.main_sell_amount,
            main_net=main_net,
            is_sweep_tick=is_sweep_tick,
            sweep_triggered=state.sweep_triggered,
            change_pct=change_pct,
        )

    def get_main_net(self, stock: str) -> float:
        """Get current main force net amount for a stock."""
        state = self._states.get(stock)
        if state is None:
            return 0.0
        return state.main_buy_amount - state.main_sell_amount

    def is_sweep_triggered(self, stock: str) -> bool:
        """Check if sweep signal has been triggered today for a stock."""
        state = self._states.get(stock)
        return state.sweep_triggered if state else False


def classify_tick_direction(
    cur_price: float,
    bid_price: float,
    ask_price: float,
    prev_price: float,
) -> bool:
    """Classify tick direction as buy (True) or sell (False).

    Logic:
      1. Price >= ask (卖一) → aggressive buy
      2. Price <= bid (买一) → aggressive sell
      3. Price vs previous tick → infer direction
      4. Tie → assume buy (market convention)
    """
    if ask_price > 0 and cur_price >= ask_price:
        return True
    if bid_price > 0 and cur_price <= bid_price:
        return False
    if cur_price > prev_price:
        return True
    if cur_price < prev_price:
        return False
    return True  # tie → buy by convention
