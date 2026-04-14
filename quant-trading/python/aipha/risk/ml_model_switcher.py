"""ML 模型切换器 — CSI2000/CSI1000 四状态机 (C3: R89)

当 CSI2000 模型滚动 SR 持续低迷时，自动切换到 CSI1000 备用模型。

状态机：
    NORMAL(100% CSI2000)
      → TRANSITION(50/50, transition_days)
      → BACKUP(100% CSI1000)
      → RECOVER(50/50, transition_days)
      → NORMAL

触发条件（NORMAL→TRANSITION）：
    CSI2000 rolling SR < sr_threshold 连续 consecutive_months 月

恢复条件（BACKUP→RECOVER）：
    CSI2000 rolling SR > recovery_sr_threshold 连续 recovery_months 月
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class SwitcherState(enum.Enum):
    """模型切换器状态。"""

    NORMAL = "normal"
    TRANSITION = "transition"
    BACKUP = "backup"
    RECOVER = "recover"


@dataclass
class MLModelSwitcherConfig:
    """切换器配置。"""

    sr_threshold: float = 0.5
    consecutive_months: int = 2
    recovery_sr_threshold: float = 1.0
    recovery_months: int = 2
    transition_days: int = 21
    rolling_window_days: int = 60
    rebalance_interval: int = 21
    primary_model_dir: str = "models/ml_csi2000_lgb_fund"
    backup_model_dir: str = "models/ml_csi1000_lgb_fund"


@dataclass
class SwitcherStatus:
    """切换器运行状态。"""

    state: SwitcherState = SwitcherState.NORMAL
    months_below_threshold: int = 0
    months_above_recovery: int = 0
    transition_days_elapsed: int = 0
    primary_weight: float = 1.0
    backup_weight: float = 0.0
    rolling_sr: float = 0.0
    day_counter: int = 0

    def to_dict(self) -> dict[str, Any]:
        """序列化。"""
        return {
            "state": self.state.value,
            "months_below_threshold": self.months_below_threshold,
            "months_above_recovery": self.months_above_recovery,
            "transition_days_elapsed": self.transition_days_elapsed,
            "primary_weight": self.primary_weight,
            "backup_weight": self.backup_weight,
            "rolling_sr": self.rolling_sr,
        }


class MLModelSwitcher:
    """CSI2000/CSI1000 四状态机切换器。"""

    def __init__(self, config: MLModelSwitcherConfig | None = None) -> None:
        self._config = config or MLModelSwitcherConfig()
        self._status = SwitcherStatus()
        self._returns: list[float] = []

    @property
    def status(self) -> SwitcherStatus:
        return self._status

    def get_model_weights(self) -> tuple[float, float]:
        """返回 (primary_weight, backup_weight)。"""
        return (self._status.primary_weight, self._status.backup_weight)

    def update(self, daily_return: float) -> SwitcherState:
        """每日更新，返回当前状态。"""
        self._returns.append(daily_return)
        self._status.day_counter += 1

        # 仅在 rebalance_interval 周期检查
        if self._status.day_counter % self._config.rebalance_interval != 0:
            # 但 TRANSITION/RECOVER 每日推进
            if self._status.state == SwitcherState.TRANSITION:
                self._advance_transition()
            elif self._status.state == SwitcherState.RECOVER:
                self._advance_recovery()
            return self._status.state

        sr = self._compute_rolling_sr()
        self._status.rolling_sr = sr

        if self._status.state == SwitcherState.NORMAL:
            self._check_transition(sr)
        elif self._status.state == SwitcherState.TRANSITION:
            self._advance_transition()
        elif self._status.state == SwitcherState.BACKUP:
            self._check_recovery(sr)
        elif self._status.state == SwitcherState.RECOVER:
            self._advance_recovery()

        return self._status.state

    def _compute_rolling_sr(self) -> float:
        """计算滚动 SR。"""
        import numpy as np

        window = self._config.rolling_window_days
        if len(self._returns) < window:
            return float("nan")
        recent = self._returns[-window:]
        arr = np.array(recent, dtype=np.float64)
        mean_r = float(np.mean(arr))
        std_r = float(np.std(arr, ddof=1))
        if std_r <= 0:
            return 0.0
        return mean_r / std_r * (244 ** 0.5)

    def _check_transition(self, sr: float) -> None:
        """NORMAL 状态下检查是否触发切换。"""
        import math

        if math.isnan(sr):
            return
        if sr < self._config.sr_threshold:
            self._status.months_below_threshold += 1
        else:
            self._status.months_below_threshold = 0
        if self._status.months_below_threshold >= self._config.consecutive_months:
            logger.warning(
                "ML Model Switcher: NORMAL → TRANSITION "
                f"(SR={sr:.2f} < {self._config.sr_threshold} "
                f"连续 {self._status.months_below_threshold} 月)"
            )
            self._status.state = SwitcherState.TRANSITION
            self._status.transition_days_elapsed = 0
            self._status.primary_weight = 0.5
            self._status.backup_weight = 0.5

    def _advance_transition(self) -> None:
        """TRANSITION 状态下推进。"""
        self._status.transition_days_elapsed += 1
        if self._status.transition_days_elapsed >= self._config.transition_days:
            logger.info(
                "ML Model Switcher: TRANSITION → BACKUP "
                f"(过渡期 {self._config.transition_days} 天完成)"
            )
            self._status.state = SwitcherState.BACKUP
            self._status.primary_weight = 0.0
            self._status.backup_weight = 1.0
            self._status.months_below_threshold = 0
            self._status.months_above_recovery = 0

    def _check_recovery(self, sr: float) -> None:
        """BACKUP 状态下检查是否触发恢复。"""
        import math

        if math.isnan(sr):
            return
        if sr > self._config.recovery_sr_threshold:
            self._status.months_above_recovery += 1
        else:
            self._status.months_above_recovery = 0
        if self._status.months_above_recovery >= self._config.recovery_months:
            logger.info(
                "ML Model Switcher: BACKUP → RECOVER "
                f"(SR={sr:.2f} > {self._config.recovery_sr_threshold} "
                f"连续 {self._status.months_above_recovery} 月)"
            )
            self._status.state = SwitcherState.RECOVER
            self._status.transition_days_elapsed = 0
            self._status.primary_weight = 0.5
            self._status.backup_weight = 0.5

    def _advance_recovery(self) -> None:
        """RECOVER 状态下推进。"""
        self._status.transition_days_elapsed += 1
        if self._status.transition_days_elapsed >= self._config.transition_days:
            logger.info(
                "ML Model Switcher: RECOVER → NORMAL "
                f"(恢复期 {self._config.transition_days} 天完成)"
            )
            self._status.state = SwitcherState.NORMAL
            self._status.primary_weight = 1.0
            self._status.backup_weight = 0.0
            self._status.months_below_threshold = 0
            self._status.months_above_recovery = 0

    def reset(self) -> None:
        """重置到初始状态。"""
        self._status = SwitcherStatus()
        self._returns.clear()
