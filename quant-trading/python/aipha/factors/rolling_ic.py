"""滚动 IC 分析 — 因子 IC 时序跟踪与衰减检测。"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Sequence

from aipha.factors.factor_analyzer import compute_ic


@dataclass
class RollingICResult:
    """滚动 IC 分析结果"""
    factor_name: str
    ic_series: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    rolling_mean: list[float] = field(default_factory=list)
    rolling_std: list[float] = field(default_factory=list)
    cumulative_ic: list[float] = field(default_factory=list)
    is_decaying: bool = False
    decay_start_idx: int = -1

    @property
    def mean_ic(self) -> float:
        valid = [v for v in self.ic_series if math.isfinite(v)]
        return statistics.mean(valid) if valid else float("nan")

    @property
    def icir(self) -> float:
        valid = [v for v in self.ic_series if math.isfinite(v)]
        if len(valid) < 2:
            return float("nan")
        m = statistics.mean(valid)
        s = statistics.stdev(valid)
        return m / s if s > 1e-15 else float("nan")

    @property
    def ic_positive_ratio(self) -> float:
        """IC > 0 的比例"""
        valid = [v for v in self.ic_series if math.isfinite(v)]
        if not valid:
            return float("nan")
        return sum(1 for v in valid if v > 0) / len(valid)


class RollingICAnalyzer:
    """滚动 IC 分析器。

    用法::

        analyzer = RollingICAnalyzer("momentum", window=20)
        for date, factor_values, forward_returns in data:
            analyzer.add_period(date, factor_values, forward_returns)
        result = analyzer.analyze()
    """

    def __init__(self, factor_name: str, window: int = 20) -> None:
        self.factor_name = factor_name
        self.window = window
        self._ic_series: list[float] = []
        self._dates: list[str] = []

    def add_period(
        self,
        date: str,
        factor_values: dict[str, float],
        forward_returns: dict[str, float],
    ) -> float:
        """添加一期数据，返回当期 IC。"""
        ic = compute_ic(factor_values, forward_returns)
        self._ic_series.append(ic)
        self._dates.append(date)
        return ic

    def analyze(self) -> RollingICResult:
        """生成完整滚动 IC 分析结果。"""
        n = len(self._ic_series)
        rolling_mean: list[float] = []
        rolling_std: list[float] = []
        cumulative_ic: list[float] = []

        cum = 0.0
        for i in range(n):
            valid_ic = self._ic_series[i]
            cum += valid_ic if math.isfinite(valid_ic) else 0.0
            cumulative_ic.append(cum)

            start = max(0, i - self.window + 1)
            window_vals = [v for v in self._ic_series[start:i + 1] if math.isfinite(v)]
            if window_vals:
                rolling_mean.append(statistics.mean(window_vals))
                rolling_std.append(statistics.stdev(window_vals) if len(window_vals) > 1 else 0.0)
            else:
                rolling_mean.append(float("nan"))
                rolling_std.append(float("nan"))

        # 检测衰减：近期 IC 均值显著低于早期
        is_decaying = False
        decay_start = -1
        if n >= self.window * 3:
            early = [v for v in self._ic_series[:n // 3] if math.isfinite(v)]
            late = [v for v in self._ic_series[-n // 3:] if math.isfinite(v)]
            if early and late:
                early_mean = statistics.mean(early)
                late_mean = statistics.mean(late)
                if abs(early_mean) > 0.01 and late_mean / (early_mean + 1e-15) < 0.5:
                    is_decaying = True
                    # 找衰减起始点（首次低于早期均值50%的滚动均值）
                    threshold = early_mean * 0.5
                    for j in range(n // 3, n):
                        if j < len(rolling_mean) and math.isfinite(rolling_mean[j]):
                            if rolling_mean[j] < threshold:
                                decay_start = j
                                break

        return RollingICResult(
            factor_name=self.factor_name,
            ic_series=list(self._ic_series),
            dates=list(self._dates),
            rolling_mean=rolling_mean,
            rolling_std=rolling_std,
            cumulative_ic=cumulative_ic,
            is_decaying=is_decaying,
            decay_start_idx=decay_start,
        )
