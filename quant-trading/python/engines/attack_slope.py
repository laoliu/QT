"""
attack_slope — Short-period price attack slope (攻击斜率) engine.

Computes the "attack angle" of price movement over a configurable window,
used to confirm aggressive upward momentum in short-term strategies.

Formula:
  Slope = (Price_t - Price_{t-window}) / (Price_{t-window} × window_minutes)
  Angle = atan(Slope × scale_factor) × 180 / π

The scale_factor maps the raw slope into a meaningful angular range:
  - Default scale_factor=300: ~2.9% rise in 5 minutes → 60°
  - Angles > 60° indicate strong attack momentum
  - Angles > 75° indicate extremely aggressive buying

Usage:
  calc = AttackSlopeCalculator()
  calc.record(stock, timestamp_seconds, price)
  angle = calc.get_angle(stock, current_timestamp)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class SlopeResult:
    """Result of slope calculation."""

    angle: float = 0.0              # angle in degrees
    slope: float = 0.0              # raw slope value
    price_now: float = 0.0         # current price
    price_ref: float = 0.0         # reference price (window ago)
    time_gap_sec: float = 0.0      # actual time gap used (seconds)
    valid: bool = False             # whether calculation is valid


class AttackSlopeCalculator:
    """Track price history and compute attack slope angles.

    Parameters:
        window_sec: slope calculation window in seconds. Default 300 (5 minutes).
        scale_factor: multiplier for atan angle mapping. Default 300.
            At scale=300, a 5-min slope of ~0.0033 (0.33%/min) → 45°.
        max_history_sec: maximum history to retain. Default 1200 (20 min).
        tolerance_ratio: how much time tolerance when finding the reference
            price point. E.g. 0.5 means accept a point within 50% of window_sec.
    """

    def __init__(
        self,
        window_sec: int = 300,
        scale_factor: float = 300.0,
        max_history_sec: int = 1200,
        tolerance_ratio: float = 0.5,
    ):
        self.window_sec = window_sec
        self.scale_factor = scale_factor
        self.max_history_sec = max_history_sec
        self.tolerance_ratio = tolerance_ratio
        self._histories: dict[str, list[tuple[float, float]]] = {}  # stock → [(ts, price)]

    def reset(self, stock: str) -> None:
        """Clear price history for a stock."""
        self._histories[stock] = []

    def reset_all(self) -> None:
        """Clear all price histories."""
        self._histories.clear()

    def record(self, stock: str, timestamp_sec: float, price: float) -> None:
        """Record a price point.

        Args:
            stock: stock code
            timestamp_sec: seconds since midnight (or epoch seconds)
            price: current price
        """
        if price <= 0:
            return

        history = self._histories.get(stock)
        if history is None:
            history = []
            self._histories[stock] = history

        history.append((timestamp_sec, price))

        # Trim old data
        cutoff = timestamp_sec - self.max_history_sec
        self._histories[stock] = [(t, p) for t, p in history if t >= cutoff]

    def get_angle(self, stock: str, current_ts: float | None = None) -> float:
        """Get the current attack angle in degrees.

        Returns 0.0 if insufficient data.
        """
        return self.compute(stock, current_ts).angle

    def compute(self, stock: str, current_ts: float | None = None) -> SlopeResult:
        """Compute full slope result for a stock.

        Args:
            stock: stock code
            current_ts: current timestamp in seconds. If None, uses the
                        latest recorded timestamp.

        Returns:
            SlopeResult with angle, slope, and reference price info.
        """
        history = self._histories.get(stock)
        if not history or len(history) < 2:
            return SlopeResult()

        cur_ts, cur_price = history[-1]
        if current_ts is not None:
            cur_ts = current_ts
            cur_price = history[-1][1]

        target_ts = cur_ts - self.window_sec
        max_allowed_diff = self.window_sec * self.tolerance_ratio

        # Find the closest recorded price to the target timestamp
        best_price = 0.0
        best_diff = float("inf")
        for t, p in history:
            diff = abs(t - target_ts)
            if diff < best_diff:
                best_diff = diff
                best_price = p

        if best_price <= 0 or best_diff > max_allowed_diff:
            return SlopeResult(price_now=cur_price)

        # Compute slope: normalized by price and window duration (minutes)
        window_minutes = self.window_sec / 60.0
        slope = (cur_price - best_price) / (best_price * window_minutes)

        # Scale and compute angle
        angle = math.atan(slope * self.scale_factor) * 180.0 / math.pi

        return SlopeResult(
            angle=angle,
            slope=slope,
            price_now=cur_price,
            price_ref=best_price,
            time_gap_sec=best_diff,
            valid=True,
        )

    def has_data(self, stock: str) -> bool:
        """Check if there is any recorded history for a stock."""
        history = self._histories.get(stock)
        return bool(history and len(history) >= 2)
