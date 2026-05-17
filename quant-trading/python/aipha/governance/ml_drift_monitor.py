"""ML feature drift / PSI monitor.

Implements Population Stability Index (PSI) and feature-distribution
drift detection for ML model inputs. Per Principle B: concept drift
must be detected and trigger retraining before predictions degrade.

Reference: Gu, Kelly, Xiu (2020); López de Prado (2018);
copilot-instructions.md §11.2 "PSI > 0.2 -> significant shift".

Public API:
    compute_psi(baseline, current, bins=10) -> float
    check_feature_drift(baseline, current, threshold=0.20) -> DriftReport
    check_model_drift(baseline_features, current_features,
                      feature_names=None, threshold=0.20) -> ModelDriftReport
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable

PSI_SIGNIFICANT = 0.10   # warning threshold
PSI_SEVERE = 0.20        # significant shift per Gu Kelly Xiu
PSI_EXTREME = 0.30       # retraining strongly recommended


@dataclass
class FeatureDriftReport:
    """Drift analysis for a single feature."""
    feature_name: str = ""
    psi: float = 0.0
    severity: str = "stable"  # stable / warning / significant / extreme
    baseline_stats: dict = field(default_factory=dict)
    current_stats: dict = field(default_factory=dict)
    n_baseline: int = 0
    n_current: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class ModelDriftReport:
    """Drift analysis across all features of a model."""
    n_features: int = 0
    n_stable: int = 0
    n_warning: int = 0
    n_significant: int = 0
    n_extreme: int = 0
    max_psi: float = 0.0
    mean_psi: float = 0.0
    recommend_retrain: bool = False
    features: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def _basic_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"n": 0, "mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0}
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / max(1, n - 1)
    return {
        "n": n,
        "mean": mean,
        "std": math.sqrt(var),
        "min": min(values),
        "max": max(values),
    }


def _isnan(x: Any) -> bool:
    try:
        return math.isnan(x)
    except (TypeError, ValueError):
        return False


def _bin_edges(values: list[float], bins: int) -> list[float]:
    """Quantile-based bin edges from sorted values."""
    if not values:
        return []
    s = sorted(values)
    edges = [s[0]]
    for i in range(1, bins):
        idx = int(i * len(s) / bins)
        idx = min(idx, len(s) - 1)
        edges.append(s[idx])
    edges.append(s[-1])
    deduped: list[float] = []
    for e in edges:
        if not deduped or e > deduped[-1]:
            deduped.append(e)
    return deduped


def _bucket_counts(values: list[float], edges: list[float]) -> list[int]:
    if len(edges) < 2:
        return [len(values)]
    counts = [0] * (len(edges) - 1)
    for v in values:
        placed = False
        for i in range(len(edges) - 1):
            left, right = edges[i], edges[i + 1]
            if i == len(edges) - 2:
                if left <= v <= right:
                    counts[i] += 1
                    placed = True
                    break
            else:
                if left <= v < right:
                    counts[i] += 1
                    placed = True
                    break
        if not placed:
            if v < edges[0]:
                counts[0] += 1
            else:
                counts[-1] += 1
    return counts


def compute_psi(
    baseline: Iterable[float],
    current: Iterable[float],
    bins: int = 10,
    eps: float = 1e-6,
) -> float:
    """Population Stability Index between baseline and current.

    PSI = sum( (p_cur - p_base) * ln(p_cur / p_base) ) over buckets.
    Returns 0.0 for empty inputs or inadequate sample size.
    """
    base = [float(x) for x in baseline if x is not None and not _isnan(x)]
    curr = [float(x) for x in current if x is not None and not _isnan(x)]
    if len(base) < bins or len(curr) < bins:
        return 0.0
    edges = _bin_edges(base, bins)
    if len(edges) < 2:
        return 0.0
    b_counts = _bucket_counts(base, edges)
    c_counts = _bucket_counts(curr, edges)
    nb = sum(b_counts) or 1
    nc = sum(c_counts) or 1
    psi = 0.0
    for b, c in zip(b_counts, c_counts):
        p_b = b / nb if b > 0 else eps
        p_c = c / nc if c > 0 else eps
        psi += (p_c - p_b) * math.log(p_c / p_b)
    return psi


def _severity(psi: float) -> str:
    if psi >= PSI_EXTREME:
        return "extreme"
    if psi >= PSI_SEVERE:
        return "significant"
    if psi >= PSI_SIGNIFICANT:
        return "warning"
    return "stable"


def check_feature_drift(
    baseline: Iterable[float],
    current: Iterable[float],
    feature_name: str = "",
    bins: int = 10,
) -> FeatureDriftReport:
    base = [float(x) for x in baseline if x is not None and not _isnan(x)]
    curr = [float(x) for x in current if x is not None and not _isnan(x)]
    psi = compute_psi(base, curr, bins=bins)
    return FeatureDriftReport(
        feature_name=feature_name,
        psi=psi,
        severity=_severity(psi),
        baseline_stats=_basic_stats(base),
        current_stats=_basic_stats(curr),
        n_baseline=len(base),
        n_current=len(curr),
    )


def check_model_drift(
    baseline_features: dict[str, Iterable[float]],
    current_features: dict[str, Iterable[float]],
    bins: int = 10,
) -> ModelDriftReport:
    """Aggregate drift report across a dict of named features."""
    common = sorted(set(baseline_features.keys()) & set(current_features.keys()))
    report = ModelDriftReport(n_features=len(common))
    psi_vals: list[float] = []
    for name in common:
        fr = check_feature_drift(
            baseline_features[name], current_features[name],
            feature_name=name, bins=bins,
        )
        report.features.append(fr.to_dict())
        psi_vals.append(fr.psi)
        if fr.severity == "stable":
            report.n_stable += 1
        elif fr.severity == "warning":
            report.n_warning += 1
        elif fr.severity == "significant":
            report.n_significant += 1
        elif fr.severity == "extreme":
            report.n_extreme += 1
    if psi_vals:
        report.max_psi = max(psi_vals)
        report.mean_psi = sum(psi_vals) / len(psi_vals)
    # Principle B: significant drift triggers review; extreme -> retrain
    report.recommend_retrain = (
        report.n_extreme > 0
        or report.n_significant >= max(1, report.n_features // 5)
    )
    return report
