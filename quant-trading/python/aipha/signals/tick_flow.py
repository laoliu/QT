"""Tick-level large order flow tracking and sweep detection.

Classifies each tick as buy/sell, tracks cumulative main-force
(large order) net flow, and detects sweep patterns near daily limits.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

__all__ = ["TickFlowTracker", "TickFlowResult", "classify_tick_direction"]


@dataclass
class TickFlowResult:
    """Result of processing a single tick update."""

    is_buy: bool = True
    delta_volume: int = 0
    delta_amount: float = 0.0
    main_buy_amount: float = 0.0
    main_sell_amount: float = 0.0
    main_net: float = 0.0
    is_sweep_tick: bool = False
    sweep_triggered: bool = False
    change_pct: float = 0.0


class _StockTickState:
    __slots__ = (
        "prev_volume", "prev_amount", "prev_price", "date",
        "main_buy", "main_sell", "sweep_triggered",
    )

    def __init__(self, date: str = "") -> None:
        self.prev_volume: int = 0
        self.prev_amount: float = 0.0
        self.prev_price: float = 0.0
        self.date = date
        self.main_buy: float = 0.0
        self.main_sell: float = 0.0
        self.sweep_triggered: bool = False


def classify_tick_direction(
    cur_price: float, bid_price: float, ask_price: float, prev_price: float,
) -> bool:
    """Classify tick as buy (True) or sell (False).

    Uses bid/ask proximity first, then falls back to price comparison.
    """
    if ask_price > 0 and cur_price >= ask_price:
        return True
    if bid_price > 0 and cur_price <= bid_price:
        return False
    if cur_price > prev_price:
        return True
    if cur_price < prev_price:
        return False
    return True  # tie defaults to buy


class TickFlowTracker:
    """Track tick-level large order flow per stock.

    Parameters
    ----------
    large_order_threshold : float
        Amount threshold to classify as large order (yuan).
    sweep_volume_threshold : float
        Volume threshold for sweep detection.
    sweep_change_low : float
        Lower bound of change_pct range for sweep check (e.g. 0.08).
    sweep_change_high : float
        Upper bound of change_pct range for sweep check (e.g. 0.09).
    """

    def __init__(
        self,
        large_order_threshold: float = 1_000_000,
        sweep_volume_threshold: float = 1_000_000,
        sweep_change_low: float = 0.08,
        sweep_change_high: float = 0.09,
    ) -> None:
        self.large_order_threshold = large_order_threshold
        self.sweep_volume_threshold = sweep_volume_threshold
        self.sweep_change_low = sweep_change_low
        self.sweep_change_high = sweep_change_high
        self._states: Dict[str, _StockTickState] = {}

    def reset(self, stock: str, date: str = "") -> None:
        self._states[stock] = _StockTickState(date)

    def reset_all(self, date: str = "") -> None:
        for k in list(self._states):
            self._states[k] = _StockTickState(date)

    def get_state(self, stock: str) -> Optional[_StockTickState]:
        return self._states.get(stock)

    def get_main_net(self, stock: str) -> float:
        s = self._states.get(stock)
        return (s.main_buy - s.main_sell) if s else 0.0

    def is_sweep_triggered(self, stock: str) -> bool:
        s = self._states.get(stock)
        return s.sweep_triggered if s else False

    def update(
        self,
        stock: str,
        cur_price: float,
        cur_volume: int,
        cur_amount: float,
        pre_close: float,
        bid_price: float,
        ask_price: float,
        date: str = "",
    ) -> Optional[TickFlowResult]:
        """Process a tick and return flow result."""
        s = self._states.get(stock)
        if s is None or (date and s.date != date):
            s = _StockTickState(date)
            self._states[stock] = s
        dv = cur_volume - s.prev_volume
        da = cur_amount - s.prev_amount
        if dv <= 0:
            s.prev_volume = cur_volume
            s.prev_amount = cur_amount
            s.prev_price = cur_price
            return None
        is_buy = classify_tick_direction(cur_price, bid_price, ask_price, s.prev_price)
        is_large = da >= self.large_order_threshold
        if is_large:
            if is_buy:
                s.main_buy += da
            else:
                s.main_sell += da
        change_pct = (cur_price / pre_close - 1.0) if pre_close > 0 else 0.0
        is_sweep = (
            is_buy
            and is_large
            and self.sweep_change_low <= change_pct <= self.sweep_change_high
        )
        if is_sweep:
            s.sweep_triggered = True
        s.prev_volume = cur_volume
        s.prev_amount = cur_amount
        s.prev_price = cur_price
        return TickFlowResult(
            is_buy=is_buy, delta_volume=dv, delta_amount=da,
            main_buy_amount=s.main_buy, main_sell_amount=s.main_sell,
            main_net=s.main_buy - s.main_sell,
            is_sweep_tick=is_sweep, sweep_triggered=s.sweep_triggered,
            change_pct=change_pct,
        )
