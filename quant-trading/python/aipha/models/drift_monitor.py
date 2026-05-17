"""
ML 特征漂移监控（PSI-based）

通过 Population Stability Index（PSI）检测特征分布漂移，
当 PSI > 0.2 时触发显著漂移告警，提示模型需要重训练。

PSI 阈值（McLean & Pontiff 2016 参考）：
    < 0.1  : 稳定，无需关注
    0.1~0.2: 轻微漂移，持续监控
    > 0.2  : 显著漂移，触发重训练告警

使用示例：
    monitor = DriftMonitor(feature_names=["f1", "f2"], n_bins=10)
    monitor.set_baseline(baseline_df)
    report = monitor.detect_drift(current_df)
    print(report.has_significant_drift, report.drifted_features)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


PSI_STABLE = 0.1
PSI_WARNING = 0.2   # 显著漂移阈值


@dataclass
class FeatureDriftInfo:
    """单特征漂移信息"""

    feature_name: str
    psi: float
    level: str  # "stable" / "warning" / "drift"
    baseline_mean: float
    current_mean: float
    mean_shift_pct: float  # (current - baseline) / |baseline| * 100

    def to_dict(self) -> dict:
        return {
            "feature_name": self.feature_name,
            "psi": round(self.psi, 4),
            "level": self.level,
            "baseline_mean": round(self.baseline_mean, 4),
            "current_mean": round(self.current_mean, 4),
            "mean_shift_pct": round(self.mean_shift_pct, 2),
        }


@dataclass
class DriftReport:
    """漂移检测报告"""

    has_significant_drift: bool          # 有任意特征 PSI > 0.2
    drifted_features: list[str]          # PSI > 0.2 的特征列表
    warning_features: list[str]          # 0.1 < PSI ≤ 0.2 的特征
    feature_details: list[FeatureDriftInfo]
    overall_psi: float                   # 所有特征 PSI 均值
    n_features_checked: int

    def to_dict(self) -> dict:
        return {
            "has_significant_drift": self.has_significant_drift,
            "drifted_features": self.drifted_features,
            "warning_features": self.warning_features,
            "overall_psi": round(self.overall_psi, 4),
            "n_features_checked": self.n_features_checked,
            "feature_details": [f.to_dict() for f in self.feature_details],
        }


class DriftMonitor:
    """ML 特征漂移监控器（PSI 计算）"""

    def __init__(
        self,
        feature_names: list[str],
        n_bins: int = 10,
    ) -> None:
        if not feature_names:
            raise ValueError("feature_names must not be empty")
        if n_bins < 2:
            raise ValueError(f"n_bins must be >= 2, got {n_bins}")
        self.feature_names = list(feature_names)
        self.n_bins = n_bins
        # 基线统计：{feature: {"bin_edges": [...], "bin_pcts": [...]}}
        self._baseline: dict[str, dict] = {}
        # 最近一次漂移检测的 PSI：{feature: psi_value}
        self._last_psi: dict[str, float] = {}

    def set_baseline(self, baseline_data: pd.DataFrame) -> None:
        """设置基线分布（训练集/验证集特征统计）"""
        self._baseline = {}
        for feat in self.feature_names:
            if feat not in baseline_data.columns:
                continue
            series = baseline_data[feat].dropna()
            if len(series) < self.n_bins:
                continue
            counts, edges = np.histogram(series, bins=self.n_bins)
            pcts = counts / counts.sum()
            self._baseline[feat] = {
                "bin_edges": edges.tolist(),
                "bin_pcts": pcts.tolist(),
                "mean": float(series.mean()),
                "std": float(series.std()),
            }

    def detect_drift(self, current_data: pd.DataFrame) -> DriftReport:
        """检测当前数据与基线的漂移"""
        if not self._baseline:
            raise RuntimeError("Baseline not set. Call set_baseline() first.")

        feature_details: list[FeatureDriftInfo] = []
        self._last_psi = {}

        for feat in self.feature_names:
            if feat not in self._baseline:
                continue
            if feat not in current_data.columns:
                continue
            psi = self._compute_psi_for_feature(feat, current_data[feat].dropna())
            self._last_psi[feat] = psi

            baseline_mean = self._baseline[feat]["mean"]
            current_mean = float(current_data[feat].dropna().mean()) \
                if not current_data[feat].dropna().empty else float("nan")
            shift_pct = (
                (current_mean - baseline_mean) / abs(baseline_mean) * 100
                if baseline_mean != 0 else 0.0
            )
            if psi < PSI_STABLE:
                level = "stable"
            elif psi < PSI_WARNING:
                level = "warning"
            else:
                level = "drift"

            feature_details.append(FeatureDriftInfo(
                feature_name=feat,
                psi=psi,
                level=level,
                baseline_mean=baseline_mean,
                current_mean=current_mean,
                mean_shift_pct=shift_pct,
            ))

        drifted = [f.feature_name for f in feature_details if f.level == "drift"]
        warnings = [f.feature_name for f in feature_details if f.level == "warning"]
        overall_psi = (
            float(np.mean([f.psi for f in feature_details]))
            if feature_details else 0.0
        )
        return DriftReport(
            has_significant_drift=len(drifted) > 0,
            drifted_features=drifted,
            warning_features=warnings,
            feature_details=feature_details,
            overall_psi=overall_psi,
            n_features_checked=len(feature_details),
        )

    def get_psi(self, feature_name: str) -> float:
        """返回指定特征对最近一次 detect_drift 结果的 PSI 值"""
        if feature_name not in self._last_psi:
            raise KeyError(
                f"PSI for '{feature_name}' not found. "
                "Run detect_drift() first, or feature not in baseline."
            )
        return self._last_psi[feature_name]

    def save_baseline(self, path: str) -> None:
        """保存基线分布到 JSON 文件"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"feature_names": self.feature_names,
                       "n_bins": self.n_bins,
                       "baseline": self._baseline}, f, indent=2)

    def load_baseline(self, path: str) -> None:
        """从 JSON 文件加载基线分布"""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self._baseline = data.get("baseline", {})

    def _compute_psi_for_feature(
        self, feature_name: str, current_series: pd.Series
    ) -> float:
        """内部：计算单特征 PSI = Σ (actual% - expected%) * ln(actual%/expected%)"""
        baseline_info = self._baseline.get(feature_name)
        if baseline_info is None or len(current_series) == 0:
            return 0.0

        edges = np.array(baseline_info["bin_edges"])
        expected_pcts = np.array(baseline_info["bin_pcts"])

        # 用同样的 bin edges 对当前数据分桶
        current_counts, _ = np.histogram(current_series, bins=edges)
        total = current_counts.sum()
        if total == 0:
            return 0.0
        actual_pcts = current_counts / total

        # 防止 log(0)：用极小值替代 0
        eps = 1e-8
        actual_pcts = np.clip(actual_pcts, eps, None)
        expected_pcts = np.clip(expected_pcts, eps, None)
        # 重新归一化（保证 epsilon 替代后总和接近 1）
        actual_pcts = actual_pcts / actual_pcts.sum()
        expected_pcts = expected_pcts / expected_pcts.sum()

        psi = float(np.sum((actual_pcts - expected_pcts) * np.log(actual_pcts / expected_pcts)))
        return max(0.0, psi)
