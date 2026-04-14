"""ML 特征漂移监控 — PSI (Population Stability Index)

PSI < 0.1: 稳定  |  0.1~0.2: 轻微漂移  |  > 0.2: 显著漂移 → 触发重训
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np


PSI_STABLE = 0.1
PSI_WARNING = 0.2


@dataclass
class FeatureDriftInfo:
    feature_name: str
    psi: float
    is_drifted: bool
    baseline_mean: float = 0.0
    current_mean: float = 0.0
    baseline_std: float = 0.0
    current_std: float = 0.0


@dataclass
class DriftReport:
    feature_drifts: list[FeatureDriftInfo] = field(default_factory=list)
    max_psi: float = 0.0
    mean_psi: float = 0.0
    n_drifted: int = 0
    n_features: int = 0

    @property
    def has_significant_drift(self) -> bool:
        return self.n_drifted > 0

    @property
    def drifted_features(self) -> list[str]:
        return [d.feature_name for d in self.feature_drifts if d.is_drifted]

    def to_dict(self) -> dict:
        return {
            "max_psi": round(self.max_psi, 4),
            "mean_psi": round(self.mean_psi, 4),
            "n_drifted": self.n_drifted,
            "n_features": self.n_features,
            "drifted_features": self.drifted_features,
        }


class DriftMonitor:
    """PSI-based 特征漂移监控器。

    用法::

        monitor = DriftMonitor(feature_names=["f1", "f2"])
        monitor.set_baseline(baseline_data)  # (N, F)
        report = monitor.detect_drift(current_data)  # (M, F)
    """

    def __init__(
        self,
        feature_names: list[str] | None = None,
        n_bins: int = 10,
        psi_threshold: float = PSI_WARNING,
    ) -> None:
        self.feature_names = feature_names or []
        self.n_bins = n_bins
        self.psi_threshold = psi_threshold
        self._baseline_bins: dict[str, np.ndarray] = {}
        self._baseline_counts: dict[str, np.ndarray] = {}
        self._baseline_stats: dict[str, tuple[float, float]] = {}

    def set_baseline(self, data: np.ndarray) -> None:
        """设定基准分布。data: (N, F)"""
        if data.ndim == 1:
            data = data.reshape(-1, 1)
        n_features = data.shape[1]
        if not self.feature_names:
            self.feature_names = [f"f{i}" for i in range(n_features)]

        for i, name in enumerate(self.feature_names[:n_features]):
            col = data[:, i]
            valid = col[np.isfinite(col)]
            if len(valid) < self.n_bins:
                continue
            bins = np.quantile(valid, np.linspace(0, 1, self.n_bins + 1))
            bins = np.unique(bins)
            if len(bins) < 2:
                continue
            counts, _ = np.histogram(valid, bins=bins)
            self._baseline_bins[name] = bins
            self._baseline_counts[name] = counts.astype(float)
            self._baseline_stats[name] = (float(np.mean(valid)), float(np.std(valid)))

    def detect_drift(self, data: np.ndarray) -> DriftReport:
        """检测当前数据相对基准的漂移。"""
        if data.ndim == 1:
            data = data.reshape(-1, 1)

        drifts: list[FeatureDriftInfo] = []
        n_features = min(data.shape[1], len(self.feature_names))

        for i in range(n_features):
            name = self.feature_names[i]
            if name not in self._baseline_bins:
                continue

            col = data[:, i]
            valid = col[np.isfinite(col)]
            if len(valid) < 5:
                continue

            bins = self._baseline_bins[name]
            baseline_counts = self._baseline_counts[name]
            current_counts, _ = np.histogram(valid, bins=bins)

            psi = self._compute_psi(baseline_counts, current_counts.astype(float))
            bl_mean, bl_std = self._baseline_stats.get(name, (0, 1))

            drifts.append(FeatureDriftInfo(
                feature_name=name, psi=psi,
                is_drifted=psi > self.psi_threshold,
                baseline_mean=bl_mean, current_mean=float(np.mean(valid)),
                baseline_std=bl_std, current_std=float(np.std(valid)),
            ))

        psi_vals = [d.psi for d in drifts]
        return DriftReport(
            feature_drifts=drifts,
            max_psi=max(psi_vals) if psi_vals else 0.0,
            mean_psi=float(np.mean(psi_vals)) if psi_vals else 0.0,
            n_drifted=sum(1 for d in drifts if d.is_drifted),
            n_features=len(drifts),
        )

    @staticmethod
    def _compute_psi(baseline: np.ndarray, current: np.ndarray) -> float:
        """计算 PSI"""
        eps = 1e-6
        bl = baseline / (baseline.sum() + eps) + eps
        cr = current / (current.sum() + eps) + eps
        # 对齐长度
        n = min(len(bl), len(cr))
        bl, cr = bl[:n], cr[:n]
        return float(np.sum((cr - bl) * np.log(cr / bl)))
