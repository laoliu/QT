"""模型重训练触发器 — 基于 PSI / 性能衰减 / 时间窗口

当满足任一条件时触发重训练:
  1. 特征分布漂移 (PSI > threshold)
  2. 滚动 Sharpe 降至阈值以下
  3. 距上次训练超过 max_days
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

import numpy as np


class TriggerReason(str, Enum):
    PSI_DRIFT = "PSI_DRIFT"
    PERFORMANCE_DECAY = "PERFORMANCE_DECAY"
    TIME_ELAPSED = "TIME_ELAPSED"
    MANUAL = "MANUAL"


@dataclass
class RetrainDecision:
    should_retrain: bool
    reasons: list[TriggerReason]
    psi_value: float | None
    rolling_sharpe: float | None
    days_since_last: int


@dataclass
class RetrainConfig:
    psi_threshold: float = 0.25
    min_sharpe: float = 0.3
    max_days: int = 90
    sharpe_window: int = 60


class RetrainTrigger:
    """自动重训练触发器。"""

    def __init__(self, config: RetrainConfig | None = None) -> None:
        self.config = config or RetrainConfig()
        self._last_train_date: datetime | None = None
        self._daily_returns: list[float] = []

    def record_train(self, dt: datetime | None = None) -> None:
        self._last_train_date = dt or datetime.now()

    def record_return(self, ret: float) -> None:
        self._daily_returns.append(ret)

    def check(
        self,
        current_date: datetime | None = None,
        psi_value: float | None = None,
    ) -> RetrainDecision:
        cfg = self.config
        now = current_date or datetime.now()
        reasons: list[TriggerReason] = []

        # 1. PSI 漂移
        if psi_value is not None and psi_value > cfg.psi_threshold:
            reasons.append(TriggerReason.PSI_DRIFT)

        # 2. 滚动 Sharpe
        rolling_sharpe = None
        if len(self._daily_returns) >= cfg.sharpe_window:
            recent = self._daily_returns[-cfg.sharpe_window:]
            mu = float(np.mean(recent))
            std = float(np.std(recent))
            rolling_sharpe = mu / std * np.sqrt(252) if std > 0 else 0.0
            if rolling_sharpe < cfg.min_sharpe:
                reasons.append(TriggerReason.PERFORMANCE_DECAY)

        # 3. 时间
        days_since = 0
        if self._last_train_date:
            days_since = (now - self._last_train_date).days
            if days_since >= cfg.max_days:
                reasons.append(TriggerReason.TIME_ELAPSED)
        else:
            days_since = 999
            reasons.append(TriggerReason.TIME_ELAPSED)

        return RetrainDecision(
            should_retrain=len(reasons) > 0,
            reasons=reasons,
            psi_value=psi_value,
            rolling_sharpe=rolling_sharpe,
            days_since_last=days_since,
        )
