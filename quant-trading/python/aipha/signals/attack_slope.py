"""Attack slope calculator — measures price attack angle in degrees.

Tracks real-time price ticks and computes the slope (angle) of price
movement over a configurable window, useful for detecting aggressive
buying/selling pressure.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

__all__ = ["AttackSlopeCalculator", "SlopeResult"]


@dataclass
class SlopeResult:
    """Result of attack slope computation."""

    angle: float = 0.0
    slope: float = 0.0
    price_now: float = 0.0
    price_ref: float = 0.0
    time_gap_sec: float = 0.0
    valid: bool = False


class AttackSlopeCalculator:
    """Calculate the attack angle of a stock's price movement.

    Parameters
    ----------
    window_sec : int
        Lookback window in seconds (default 300 = 5 min).
    scale_factor : float
        Slope scaling factor for angle conversion.
    max_history_sec : int
        Maximum history to retain per stock.
    tolerance_ratio : float
        Minimum ratio of window covered to consider valid.
    """

    def __init__(
        self,
        window_sec: int = 300,
        scale_factor: float = 300.0,
        max_history_sec: int = 1200,
        tolerance_ratio: float = 0.5,
    ) -> None:
        self.window_sec = window_sec
        self.scale_factor = scale_factor
        self.max_history_sec = max_history_sec
        self.tolerance_ratio = tolerance_ratio
        self._data: Dict[str, List[Tuple[float, float]]] = {}

    # -- public API --

    def reset(self, stock: str) -> None:
        self._data.pop(stock, None)

    def reset_all(self) -> None:
        self._data.clear()

    def record(self, stock: str, timestamp_sec: float, price: float) -> None:
        buf = self._data.setdefault(stock, [])
        buf.append((timestamp_sec, price))
        cutoff = timestamp_sec - self.max_history_sec
        while buf and buf[0][0] < cutoff:
            buf.pop(0)

    def has_data(self, stock: str) -> bool:
        return bool(self._data.get(stock))

    def get_angle(self, stock: str, current_ts: float) -> float:
        return self.compute(stock, current_ts).angle

    def compute(self, stock: str, current_ts: float) -> SlopeResult:
        buf = self._data.get(stock)
        if not buf:
            return SlopeResult()
        price_now = buf[-1][1]
        ref_ts = current_ts - self.window_sec
        best: Tuple[float, float] | None = None
        for ts, px in buf:
            if ts <= ref_ts:
                if best is None or ts > best[0]:
                    best = (ts, px)
        if best is None:
            if len(buf) >= 2:
                best = buf[0]
            else:
                return SlopeResult(price_now=price_now)
        price_ref = best[1]
        gap = current_ts - best[0]
        if gap <= 0 or price_ref <= 0:
            return SlopeResult(price_now=price_now, price_ref=price_ref)
        min_gap = self.window_sec * self.tolerance_ratio
        valid = gap >= min_gap
        window_min = gap / 60.0
        slope = (price_now - price_ref) / (price_ref * window_min) if window_min > 0 else 0.0
        angle = math.degrees(math.atan(slope * self.scale_factor))
        return SlopeResult(
            angle=angle, slope=slope, price_now=price_now,
            price_ref=price_ref, time_gap_sec=gap, valid=valid,
        )
