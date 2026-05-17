"""
MDD Gradient Risk Controller — Multi-level drawdown-based risk management.

Implements tiered drawdown response inspired by R113 portfolio evaluation:
  - L1 (warning): Reduce position to half when drawdown exceeds threshold
  - L2 (critical): Force full liquidation when drawdown is severe
  - Recovery: Gradually restore positions only after drawdown recovers

Usage:
    controller = MddGradientController(
        l1_threshold=0.12,   # 12% drawdown → reduce to 50%
        l2_threshold=0.18,   # 18% drawdown → full exit
        recovery_threshold=0.06,  # resume when drawdown < 6%
    )

    # In your backtest loop:
    scale = controller.update(current_nav)
    position_size = base_position_size * scale
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class RiskState(Enum):
    NORMAL = "normal"
    L1_REDUCED = "l1_reduced"
    L2_LIQUIDATED = "l2_liquidated"
    RECOVERING = "recovering"


@dataclass
class MddGradientConfig:
    """Configuration for MDD gradient risk controller."""

    l1_threshold: float = 0.12       # 12% drawdown triggers L1
    l2_threshold: float = 0.18       # 18% drawdown triggers L2
    recovery_threshold: float = 0.06  # resume below 6% drawdown

    l1_position_scale: float = 0.50  # scale to 50% at L1
    l2_position_scale: float = 0.00  # scale to 0% at L2
    recovery_ramp_days: int = 5      # gradual re-entry over N days

    def __post_init__(self):
        if not (0 < self.recovery_threshold < self.l1_threshold < self.l2_threshold < 1):
            raise ValueError(
                f"Must satisfy: 0 < recovery({self.recovery_threshold}) "
                f"< l1({self.l1_threshold}) < l2({self.l2_threshold}) < 1"
            )


class MddGradientController:
    """Real-time MDD gradient risk controller.

    Tracks portfolio NAV, computes current drawdown from peak,
    and returns a position scaling factor [0.0, 1.0].
    """

    def __init__(self, config: Optional[MddGradientConfig] = None, **kwargs):
        if config is None:
            config = MddGradientConfig(**kwargs)
        self.config = config

        self._peak: float = 0.0
        self._state: RiskState = RiskState.NORMAL
        self._recovery_day: int = 0
        self._history: list[dict] = []

    @property
    def state(self) -> RiskState:
        return self._state

    @property
    def current_drawdown(self) -> float:
        return self._current_dd

    def reset(self) -> None:
        self._peak = 0.0
        self._state = RiskState.NORMAL
        self._recovery_day = 0
        self._current_dd = 0.0
        self._history.clear()

    def update(self, nav: float, dt: Optional[int] = None) -> float:
        """Update with current NAV and return position scale factor [0.0, 1.0].

        Args:
            nav: Current portfolio net asset value.
            dt: Optional date (YYYYMMDD) for logging.

        Returns:
            Position scale factor: 1.0 = full, 0.5 = half, 0.0 = liquidated.
        """
        if nav <= 0:
            return 0.0

        # Update peak
        if nav > self._peak:
            self._peak = nav

        # Compute current drawdown from peak
        dd = (self._peak - nav) / self._peak if self._peak > 0 else 0.0
        self._current_dd = dd

        prev_state = self._state
        scale = self._compute_scale(dd)

        # Log state transitions
        if self._state != prev_state:
            self._history.append({
                "date": dt,
                "from_state": prev_state.value,
                "to_state": self._state.value,
                "drawdown": round(dd, 4),
                "nav": round(nav, 2),
                "peak": round(self._peak, 2),
                "scale": round(scale, 4),
            })

        return scale

    def _compute_scale(self, dd: float) -> float:
        cfg = self.config

        if self._state == RiskState.NORMAL:
            if dd >= cfg.l2_threshold:
                self._state = RiskState.L2_LIQUIDATED
                self._recovery_day = 0
                return cfg.l2_position_scale
            elif dd >= cfg.l1_threshold:
                self._state = RiskState.L1_REDUCED
                return cfg.l1_position_scale
            return 1.0

        elif self._state == RiskState.L1_REDUCED:
            if dd >= cfg.l2_threshold:
                self._state = RiskState.L2_LIQUIDATED
                self._recovery_day = 0
                return cfg.l2_position_scale
            elif dd < cfg.recovery_threshold:
                self._state = RiskState.RECOVERING
                self._recovery_day = 0
                return self._ramp_scale()
            return cfg.l1_position_scale

        elif self._state == RiskState.L2_LIQUIDATED:
            if dd < cfg.recovery_threshold:
                self._state = RiskState.RECOVERING
                self._recovery_day = 0
                return self._ramp_scale()
            return cfg.l2_position_scale

        elif self._state == RiskState.RECOVERING:
            if dd >= cfg.l1_threshold:
                self._state = RiskState.L1_REDUCED
                self._recovery_day = 0
                return cfg.l1_position_scale
            self._recovery_day += 1
            scale = self._ramp_scale()
            if self._recovery_day >= cfg.recovery_ramp_days:
                self._state = RiskState.NORMAL
            return scale

        return 1.0

    def _ramp_scale(self) -> float:
        """Gradual re-entry: linearly ramp from l1_scale to 1.0 over N days."""
        cfg = self.config
        progress = min(self._recovery_day / max(cfg.recovery_ramp_days, 1), 1.0)
        return cfg.l1_position_scale + (1.0 - cfg.l1_position_scale) * progress

    def get_history(self) -> list[dict]:
        return list(self._history)

    def get_stats(self) -> dict:
        l1_count = sum(1 for h in self._history if h["to_state"] == "l1_reduced")
        l2_count = sum(1 for h in self._history if h["to_state"] == "l2_liquidated")
        return {
            "total_transitions": len(self._history),
            "l1_triggers": l1_count,
            "l2_triggers": l2_count,
            "current_state": self._state.value,
            "peak_nav": round(self._peak, 2),
            "current_drawdown": round(self._current_dd, 4),
        }
