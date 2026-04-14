"""
Volatility Scaling — Target-Volatility position sizing.

Dynamically adjusts position sizes to maintain a target portfolio volatility.
When realized vol is high, scale down; when low, scale up.

Inspired by R113 portfolio evaluation v19_hgro vol_scaling (target=18%).

Usage:
    scaler = VolScaler(target_vol=0.18, lookback=60, max_leverage=1.5)

    # In your backtest loop:
    scale = scaler.update(daily_return)
    position_size = base_position_size * scale
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class VolScalingConfig:
    """Configuration for volatility-targeting position scaling."""

    target_vol: float = 0.18         # annualized target volatility (18%)
    lookback: int = 60               # rolling window for realized vol (trading days)
    max_leverage: float = 1.5        # cap scaling factor to prevent over-leverage
    min_scale: float = 0.10          # floor to prevent near-zero positions
    annualization: float = 252.0     # trading days per year
    ewm_halflife: Optional[int] = None  # optional: use EWM instead of simple rolling
    vol_floor: float = 0.05          # minimum vol estimate to avoid division by near-zero

    def __post_init__(self):
        if self.target_vol <= 0:
            raise ValueError(f"target_vol must be positive, got {self.target_vol}")
        if self.lookback < 5:
            raise ValueError(f"lookback must be >= 5, got {self.lookback}")


class VolScaler:
    """Real-time volatility-targeting position scaler.

    Computes realized volatility from daily returns and outputs a scaling
    factor = target_vol / realized_vol, capped at [min_scale, max_leverage].
    """

    def __init__(self, config: Optional[VolScalingConfig] = None, **kwargs):
        if config is None:
            config = VolScalingConfig(**kwargs)
        self.config = config

        self._returns: deque[float] = deque(maxlen=config.lookback)
        self._ewm_var: Optional[float] = None
        self._current_scale: float = 1.0
        self._current_vol: float = 0.0

    @property
    def current_scale(self) -> float:
        return self._current_scale

    @property
    def realized_vol(self) -> float:
        return self._current_vol

    def reset(self) -> None:
        self._returns.clear()
        self._ewm_var = None
        self._current_scale = 1.0
        self._current_vol = 0.0

    def update(self, daily_return: float) -> float:
        """Feed a daily return and get the updated position scale factor.

        Args:
            daily_return: Today's portfolio return (e.g., 0.02 for +2%).

        Returns:
            Scale factor [min_scale, max_leverage].
        """
        cfg = self.config
        self._returns.append(daily_return)

        # Not enough data yet — use full scale
        if len(self._returns) < max(10, cfg.lookback // 3):
            self._current_scale = 1.0
            return 1.0

        # Compute realized volatility
        if cfg.ewm_halflife is not None:
            vol = self._calc_ewm_vol(daily_return)
        else:
            vol = self._calc_simple_vol()

        # Apply floor
        vol = max(vol, cfg.vol_floor)
        self._current_vol = vol

        # Scale = target / realized, capped
        raw_scale = cfg.target_vol / vol
        scale = max(cfg.min_scale, min(raw_scale, cfg.max_leverage))
        self._current_scale = scale
        return scale

    def _calc_simple_vol(self) -> float:
        """Simple rolling standard deviation, annualized."""
        n = len(self._returns)
        if n < 2:
            return self.config.vol_floor

        mean = sum(self._returns) / n
        variance = sum((r - mean) ** 2 for r in self._returns) / (n - 1)
        daily_vol = math.sqrt(variance)
        return daily_vol * math.sqrt(self.config.annualization)

    def _calc_ewm_vol(self, daily_return: float) -> float:
        """Exponentially weighted moving volatility, annualized."""
        cfg = self.config
        alpha = 1 - math.exp(-math.log(2) / (cfg.ewm_halflife or cfg.lookback))

        if self._ewm_var is None:
            # Bootstrap with simple variance
            if len(self._returns) >= 10:
                mean = sum(self._returns) / len(self._returns)
                self._ewm_var = sum((r - mean) ** 2 for r in self._returns) / len(self._returns)
            else:
                return self.config.vol_floor
        else:
            self._ewm_var = alpha * daily_return ** 2 + (1 - alpha) * self._ewm_var

        daily_vol = math.sqrt(self._ewm_var)
        return daily_vol * math.sqrt(cfg.annualization)

    def get_diagnostics(self) -> dict:
        return {
            "realized_vol": round(self._current_vol, 4),
            "target_vol": self.config.target_vol,
            "current_scale": round(self._current_scale, 4),
            "buffer_size": len(self._returns),
            "lookback": self.config.lookback,
        }
