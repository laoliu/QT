"""PSI (Population Stability Index) 特征漂移检测"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass
class FeaturePSI:
    name: str
    psi: float
    status: str  # "stable" | "warning" | "drift"
    bin_details: list[dict] = field(default_factory=list)


@dataclass
class DriftReport:
    overall_psi: float
    feature_psis: list[FeaturePSI]
    drifted_features: list[str]
    warning_features: list[str]
    has_significant_drift: bool
    n_features: int
    n_drifted: int
    n_warning: int

    def to_dict(self) -> dict:
        return {
            "overall_psi": round(self.overall_psi, 4),
            "has_significant_drift": self.has_significant_drift,
            "n_features": self.n_features,
            "n_drifted": self.n_drifted,
            "n_warning": self.n_warning,
            "drifted_features": self.drifted_features,
            "features": [{"name": f.name, "psi": round(f.psi, 4), "status": f.status}
                         for f in self.feature_psis],
        }


class PSICalculator:
    """PSI 计算器: 比较基线分布与当前分布的差异

    PSI < 0.10 → stable
    0.10 ≤ PSI < 0.20 → warning
    PSI ≥ 0.20 → drift
    """

    def __init__(
        self,
        n_bins: int = 10,
        psi_warning: float = 0.10,
        psi_threshold: float = 0.20,
        epsilon: float = 1e-6,
    ):
        self._n_bins = n_bins
        self._psi_warning = psi_warning
        self._psi_threshold = psi_threshold
        self._epsilon = epsilon

    def compute_single(
        self, baseline: Sequence[float], current: Sequence[float], feature_name: str,
    ) -> FeaturePSI:
        """计算单特征 PSI"""
        if not baseline or not current:
            return FeaturePSI(feature_name, 0.0, "stable")

        # 基线分位数确定 bin 边界
        sorted_base = sorted(baseline)
        n = len(sorted_base)
        edges = [sorted_base[0] - 1e-10]
        for i in range(1, self._n_bins):
            idx = int(i * n / self._n_bins)
            edges.append(sorted_base[min(idx, n - 1)])
        edges.append(sorted_base[-1] + 1e-10)

        # 统计分布
        base_counts = [0] * self._n_bins
        curr_counts = [0] * self._n_bins
        for v in baseline:
            base_counts[self._find_bin(v, edges)] += 1
        for v in current:
            curr_counts[self._find_bin(v, edges)] += 1

        base_total = max(len(baseline), 1)
        curr_total = max(len(current), 1)
        base_props = [c / base_total for c in base_counts]
        curr_props = [c / curr_total for c in curr_counts]

        psi = self._psi_from_proportions(base_props, curr_props, self._epsilon)

        if psi >= self._psi_threshold:
            status = "drift"
        elif psi >= self._psi_warning:
            status = "warning"
        else:
            status = "stable"

        bin_details = [
            {"bin": i, "base_pct": round(bp, 4), "curr_pct": round(cp, 4)}
            for i, (bp, cp) in enumerate(zip(base_props, curr_props))
        ]
        return FeaturePSI(feature_name, psi, status, bin_details)

    def compute(
        self,
        baseline: dict[str, Sequence[float]],
        current: dict[str, Sequence[float]],
    ) -> DriftReport:
        """计算多特征 PSI 漂移报告"""
        features = sorted(set(baseline.keys()) & set(current.keys()))
        results: list[FeaturePSI] = []
        for name in features:
            results.append(self.compute_single(baseline[name], current[name], name))

        drifted = [f.name for f in results if f.status == "drift"]
        warnings = [f.name for f in results if f.status == "warning"]
        overall = sum(f.psi for f in results) / max(len(results), 1)

        return DriftReport(
            overall_psi=overall,
            feature_psis=results,
            drifted_features=drifted,
            warning_features=warnings,
            has_significant_drift=len(drifted) > 0,
            n_features=len(results),
            n_drifted=len(drifted),
            n_warning=len(warnings),
        )

    @staticmethod
    def _find_bin(value: float, edges: list[float]) -> int:
        for i in range(len(edges) - 1):
            if value <= edges[i + 1]:
                return min(i, len(edges) - 2)
        return len(edges) - 2

    @staticmethod
    def _psi_from_proportions(
        p: list[float], q: list[float], epsilon: float,
    ) -> float:
        total = 0.0
        for pi, qi in zip(p, q):
            pi = max(pi, epsilon)
            qi = max(qi, epsilon)
            total += (qi - pi) * math.log(qi / pi)
        return total
