"""因子衰减监控 — Rolling IC 自动检测因子有效性退化"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class DecayLevel(str, Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    DECAYED = "DECAYED"


@dataclass
class FactorHealth:
    factor_name: str
    current_ic: float
    ic_ma: float
    ic_trend: float       # 斜率
    level: DecayLevel
    half_life: float | None  # IC 半衰期 (天)


class FactorDecayMonitor:
    """持续追踪因子 IC，检测衰减。

    Parameters
    ----------
    ic_warning : float
        IC 低于此阈值触发 WARNING
    ic_decay : float
        IC 低于此阈值触发 DECAYED
    trend_window : int
        计算 IC 趋势的滚动窗口
    """

    def __init__(
        self,
        ic_warning: float = 0.02,
        ic_decay: float = 0.005,
        trend_window: int = 60,
    ) -> None:
        self.ic_warning = ic_warning
        self.ic_decay = ic_decay
        self.trend_window = trend_window
        self._ic_history: dict[str, list[float]] = {}

    def update(self, factor_name: str, ic_value: float) -> FactorHealth:
        """更新一个因子的 IC 值并返回健康状态。"""
        if factor_name not in self._ic_history:
            self._ic_history[factor_name] = []
        self._ic_history[factor_name].append(ic_value)

        hist = self._ic_history[factor_name]
        n = min(len(hist), self.trend_window)
        recent = hist[-n:]

        ic_ma = float(np.mean(recent))
        ic_abs = abs(ic_ma)

        # 趋势斜率
        if n >= 5:
            t = np.arange(n, dtype=float)
            slope = float(np.polyfit(t, recent, 1)[0])
        else:
            slope = 0.0

        # 半衰期估计
        half_life = None
        if slope < 0 and ic_abs > 0:
            half_life = ic_abs / abs(slope) * 0.693

        # 分级
        if ic_abs < self.ic_decay:
            level = DecayLevel.DECAYED
        elif ic_abs < self.ic_warning:
            level = DecayLevel.WARNING
        else:
            level = DecayLevel.HEALTHY

        return FactorHealth(
            factor_name=factor_name,
            current_ic=ic_value,
            ic_ma=ic_ma,
            ic_trend=slope,
            level=level,
            half_life=half_life,
        )

    def batch_update(self, ic_dict: dict[str, float]) -> dict[str, FactorHealth]:
        return {name: self.update(name, ic) for name, ic in ic_dict.items()}

    def decayed_factors(self) -> list[str]:
        """返回已衰减的因子列表。"""
        result = []
        for name, hist in self._ic_history.items():
            if hist:
                n = min(len(hist), self.trend_window)
                ic_ma = abs(float(np.mean(hist[-n:])))
                if ic_ma < self.ic_decay:
                    result.append(name)
        return result
