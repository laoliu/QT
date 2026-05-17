"""
Population Stability Index (PSI) 计算器 — 特征分布漂移检测。

PSI 用于量化两个分布之间的偏移程度，常用于检测模型特征是否发生
概念漂移 (Concept Drift)。

PSI 计算公式：
    PSI = Σ (p_i - q_i) * ln(p_i / q_i)
    其中 p_i = 当前期第 i 个 bin 的占比
        q_i = 基期第 i 个 bin 的占比

判断标准（McLean & Pontiff 2016）：
    PSI < 0.10  → 无显著漂移
    0.10 ≤ PSI < 0.20 → 轻度漂移，需关注
    PSI ≥ 0.20  → 显著漂移，建议重训练

使用示例::

    calc = PSICalculator(n_bins=10)
    report = calc.compute(
        baseline=train_features_df,
        current=recent_features_df,
    )
    if report.has_significant_drift:
        trigger.evaluate(drift_report=report)

参考：
    - 五原则 B: alpha 衰减检验
    - 知识库 ML T3: PSI > 0.2 → 显著偏移
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass
class FeaturePSI:
    """单个特征的 PSI 结果"""
    name: str
    psi: float
    status: str  # "stable" / "warning" / "drifted"
    bin_details: list[dict] = field(default_factory=list)


@dataclass
class DriftReport:
    """PSI 漂移报告（兼容 RetrainTrigger 接口）"""
    overall_psi: float
    feature_psis: list[FeaturePSI] = field(default_factory=list)
    drifted_features: list[str] = field(default_factory=list)
    warning_features: list[str] = field(default_factory=list)
    has_significant_drift: bool = False
    n_features: int = 0
    n_drifted: int = 0
    n_warning: int = 0

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "overall_psi": round(self.overall_psi, 6),
            "has_significant_drift": self.has_significant_drift,
            "n_features": self.n_features,
            "n_drifted": self.n_drifted,
            "n_warning": self.n_warning,
            "drifted_features": self.drifted_features,
            "warning_features": self.warning_features,
            "feature_details": [
                {"name": f.name, "psi": round(f.psi, 6), "status": f.status}
                for f in self.feature_psis
            ],
        }


class PSICalculator:
    """PSI 计算器"""

    def __init__(
        self,
        n_bins: int = 10,
        psi_warning: float = 0.10,
        psi_threshold: float = 0.20,
        epsilon: float = 1e-6,
    ) -> None:
        self.n_bins = n_bins
        self.psi_warning = psi_warning
        self.psi_threshold = psi_threshold
        self.epsilon = epsilon

    def compute_single(
        self,
        baseline: Sequence[float],
        current: Sequence[float],
        feature_name: str = "unnamed",
    ) -> FeaturePSI:
        """计算单个特征的 PSI"""
        bl = [v for v in baseline if math.isfinite(v)]
        cr = [v for v in current if math.isfinite(v)]
        if len(bl) < self.n_bins or len(cr) < self.n_bins:
            return FeaturePSI(
                name=feature_name, psi=0.0, status="insufficient_data"
            )
        # 用 baseline 分位数确定 bin 边界
        bl_sorted = sorted(bl)
        n = len(bl_sorted)
        edges = []
        for i in range(1, self.n_bins):
            idx = int(i * n / self.n_bins)
            edges.append(bl_sorted[min(idx, n - 1)])
        # 去重边界
        unique_edges = sorted(set(edges))
        if not unique_edges:
            return FeaturePSI(
                name=feature_name, psi=0.0, status="constant"
            )
        num_bins = len(unique_edges) + 1
        # 计算 baseline 和 current 的 bin 占比
        bl_counts = [0] * num_bins
        cr_counts = [0] * num_bins
        for v in bl:
            bl_counts[self._find_bin(v, unique_edges)] += 1
        for v in cr:
            cr_counts[self._find_bin(v, unique_edges)] += 1
        bl_total = sum(bl_counts)
        cr_total = sum(cr_counts)
        bl_prop = [c / bl_total for c in bl_counts]
        cr_prop = [c / cr_total for c in cr_counts]
        psi_val = self._psi_from_proportions(
            cr_prop, bl_prop, self.epsilon
        )
        if psi_val >= self.psi_threshold:
            status = "drifted"
        elif psi_val >= self.psi_warning:
            status = "warning"
        else:
            status = "stable"
        bin_details = [
            {"bin": i, "baseline": round(bl_prop[i], 4),
             "current": round(cr_prop[i], 4)}
            for i in range(num_bins)
        ]
        return FeaturePSI(
            name=feature_name,
            psi=psi_val,
            status=status,
            bin_details=bin_details,
        )

    @staticmethod
    def _find_bin(value: float, edges: list[float]) -> int:
        """二分查找 value 所属的 bin 索引"""
        lo, hi = 0, len(edges)
        while lo < hi:
            mid = (lo + hi) // 2
            if value <= edges[mid]:
                hi = mid
            else:
                lo = mid + 1
        return lo

    def compute(
        self,
        baseline: dict[str, Sequence[float]],
        current: dict[str, Sequence[float]],
    ) -> DriftReport:
        """批量计算多特征 PSI"""
        feature_names = sorted(
            set(baseline.keys()) & set(current.keys())
        )
        if not feature_names:
            return DriftReport(overall_psi=0.0, n_features=0)
        results: list[FeaturePSI] = []
        for fname in feature_names:
            fp = self.compute_single(
                baseline[fname], current[fname], fname
            )
            results.append(fp)
        drifted = [f.name for f in results if f.status == "drifted"]
        warning = [f.name for f in results if f.status == "warning"]
        valid_psis = [f.psi for f in results if f.status != "insufficient_data"]
        overall = sum(valid_psis) / len(valid_psis) if valid_psis else 0.0
        return DriftReport(
            overall_psi=overall,
            feature_psis=results,
            drifted_features=drifted,
            warning_features=warning,
            has_significant_drift=(
                overall >= self.psi_threshold or len(drifted) > 0
            ),
            n_features=len(feature_names),
            n_drifted=len(drifted),
            n_warning=len(warning),
        )

    @staticmethod
    def _psi_from_proportions(
        p: Sequence[float],
        q: Sequence[float],
        epsilon: float = 1e-6,
    ) -> float:
        """从比例向量计算 PSI 值"""
        total = 0.0
        for pi, qi in zip(p, q):
            pi_safe = max(pi, epsilon)
            qi_safe = max(qi, epsilon)
            total += (pi_safe - qi_safe) * math.log(pi_safe / qi_safe)
        return total
