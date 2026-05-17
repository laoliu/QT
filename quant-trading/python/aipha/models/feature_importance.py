"""SHAP-based feature importance diagnostics for ML models.

Provides TreeSHAP feature importance computation and temporal stability
analysis for LightGBM models used in stock selection.

Key capabilities:
- Per-fold SHAP value computation with efficient TreeSHAP
- Cross-fold importance aggregation (mean |SHAP|)
- Temporal stability analysis: IC decay, importance drift detection
- Feature group-level attribution

Reference:
  Lundberg & Lee (2017) — A Unified Approach to Interpreting Model Predictions
  Gu, Kelly, Xiu (2020) — Empirical Asset Pricing via Machine Learning
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Configuration
# ------------------------------------------------------------------

@dataclass
class SHAPDiagConfig:
    """SHAP diagnostics configuration."""

    max_samples: int = 5000
    """Maximum samples per fold for SHAP computation (speed control)."""

    aggregate_method: str = "mean_abs"
    """Aggregation: 'mean_abs' (default) or 'mean_rank'."""

    stability_window: int = 60
    """Rolling window (trading days) for temporal stability."""

    stability_min_periods: int = 20
    """Minimum periods for rolling IC."""

    importance_threshold: float = 0.01
    """Features with mean |SHAP| < threshold flagged as noise."""

    drift_threshold: float = 0.5
    """Importance rank change > threshold × n_features flags drift."""


# ------------------------------------------------------------------
# Core class
# ------------------------------------------------------------------

class SHAPFeatureDiagnostics:
    """SHAP-based feature importance diagnostics.

    Usage:
        diag = SHAPFeatureDiagnostics(config)
        # After model training with fold-level access:
        diag.compute_fold_shap(model, X_test, fold_id=0)
        diag.compute_fold_shap(model, X_test, fold_id=1)
        report = diag.generate_report()
    """

    def __init__(self, config: SHAPDiagConfig | None = None) -> None:
        self._config = config or SHAPDiagConfig()
        self._fold_results: dict[int, pd.DataFrame] = {}
        self._feature_names: list[str] = []

    def compute_fold_shap(
        self,
        model: Any,
        X: pd.DataFrame | np.ndarray,
        fold_id: int = 0,
        dates: pd.Series | np.ndarray | None = None,
    ) -> pd.DataFrame:
        """Compute SHAP values for a single fold's test data.

        Args:
            model: Trained LightGBM model (Booster or LGBMModel).
            X: Feature matrix (DataFrame preferred for feature names).
            fold_id: Fold identifier for multi-fold aggregation.
            dates: Optional date array aligned with X rows (for temporal analysis).

        Returns:
            DataFrame of SHAP values with shape (n_samples, n_features).
        """
        import shap

        cfg = self._config

        if isinstance(X, pd.DataFrame):
            self._feature_names = list(X.columns)
            X_arr = X.values
        else:
            X_arr = X
            if not self._feature_names:
                self._feature_names = [f"f_{i}" for i in range(X_arr.shape[1])]

        # Sub-sample for speed
        n = X_arr.shape[0]
        if n > cfg.max_samples:
            rng = np.random.default_rng(42 + fold_id)
            idx = rng.choice(n, cfg.max_samples, replace=False)
            idx.sort()
            X_arr = X_arr[idx]
            if dates is not None:
                dates = np.asarray(dates)[idx]
        else:
            idx = None

        # TreeSHAP
        explainer = shap.TreeExplainer(model)
        shap_values = explainer.shap_values(X_arr)

        # shap_values may be list for multiclass; take first
        if isinstance(shap_values, list):
            shap_values = shap_values[0]

        df_shap = pd.DataFrame(
            shap_values, columns=self._feature_names,
        )
        if dates is not None:
            df_shap["_date"] = np.asarray(dates)
        df_shap["_fold"] = fold_id

        self._fold_results[fold_id] = df_shap
        logger.info(
            "SHAP fold %d: %d samples, %d features",
            fold_id, len(df_shap), len(self._feature_names),
        )
        return df_shap

    def get_aggregate_importance(self) -> pd.DataFrame:
        """Aggregate SHAP importance across all computed folds.

        Returns:
            DataFrame indexed by feature name with columns:
            mean_abs_shap, std_abs_shap, rank, pct_contribution.
        """
        if not self._fold_results:
            raise ValueError("No SHAP results computed yet. Call compute_fold_shap() first.")

        feat_cols = self._feature_names
        all_abs: list[pd.Series] = []

        for fold_id, df_shap in self._fold_results.items():
            abs_mean = df_shap[feat_cols].abs().mean()
            abs_mean.name = fold_id
            all_abs.append(abs_mean)

        combined = pd.DataFrame(all_abs)
        result = pd.DataFrame({
            "mean_abs_shap": combined.mean(),
            "std_abs_shap": combined.std(),
        })
        result.sort_values("mean_abs_shap", ascending=False, inplace=True)
        result["rank"] = range(1, len(result) + 1)
        total = result["mean_abs_shap"].sum()
        result["pct_contribution"] = (
            result["mean_abs_shap"] / total * 100 if total > 0 else 0.0
        )
        return result

    def get_temporal_stability(self) -> pd.DataFrame:
        """Compute temporal stability metrics for each feature.

        Requires at least one fold with '_date' column. Computes rolling
        mean |SHAP| over time windows and returns per-feature CV
        (coefficient of variation) as stability proxy.

        Returns:
            DataFrame indexed by feature with columns:
            rolling_cv (lower = more stable), trend_slope.
        """
        feat_cols = self._feature_names
        dated_parts: list[pd.DataFrame] = []
        for df_shap in self._fold_results.values():
            if "_date" in df_shap.columns:
                dated_parts.append(df_shap)
        if not dated_parts:
            logger.warning("No date information in SHAP results; skipping temporal stability")
            return pd.DataFrame(columns=["rolling_cv", "trend_slope"], index=feat_cols)

        combined = pd.concat(dated_parts, ignore_index=True)
        combined.sort_values("_date", inplace=True)

        window = self._config.stability_window
        min_p = self._config.stability_min_periods

        results = {}
        for feat in feat_cols:
            abs_vals = combined[feat].abs()
            rolling = abs_vals.rolling(window, min_periods=min_p)
            rmean = rolling.mean().dropna()
            if len(rmean) < 2:
                results[feat] = {"rolling_cv": np.nan, "trend_slope": np.nan}
                continue
            cv = rmean.std() / rmean.mean() if rmean.mean() > 0 else np.nan
            # Simple OLS slope for trend
            x = np.arange(len(rmean), dtype=float)
            y = rmean.values
            slope = np.polyfit(x, y, 1)[0] if len(x) > 2 else 0.0
            results[feat] = {"rolling_cv": cv, "trend_slope": slope}

        return pd.DataFrame(results).T

    def detect_importance_drift(self) -> list[dict[str, Any]]:
        """Detect features whose importance rank shifted significantly across folds.

        Returns:
            List of dicts with keys: feature, rank_change, fold_ranks, severity.
        """
        if len(self._fold_results) < 2:
            return []

        feat_cols = self._feature_names
        fold_ids = sorted(self._fold_results.keys())
        n_feat = len(feat_cols)
        threshold = self._config.drift_threshold * n_feat

        # Per-fold rank
        fold_ranks: dict[str, list[int]] = {f: [] for f in feat_cols}
        for fid in fold_ids:
            df_shap = self._fold_results[fid]
            abs_mean = df_shap[feat_cols].abs().mean().sort_values(ascending=False)
            for rank_pos, feat in enumerate(abs_mean.index, 1):
                fold_ranks[feat].append(rank_pos)

        drifted: list[dict[str, Any]] = []
        for feat, ranks in fold_ranks.items():
            rank_range = max(ranks) - min(ranks)
            if rank_range > threshold:
                drifted.append({
                    "feature": feat,
                    "rank_change": rank_range,
                    "fold_ranks": dict(zip(fold_ids, ranks)),
                    "severity": "high" if rank_range > 2 * threshold else "medium",
                })

        drifted.sort(key=lambda x: x["rank_change"], reverse=True)
        if drifted:
            logger.warning(
                "Importance drift detected: %d features (threshold=%.0f rank positions)",
                len(drifted), threshold,
            )
        return drifted

    def generate_report(self) -> dict[str, Any]:
        """Generate comprehensive SHAP diagnostics report.

        Returns:
            Dict with keys: aggregate_importance, temporal_stability,
            drift_alerts, noise_features, summary.
        """
        imp = self.get_aggregate_importance()
        stability = self.get_temporal_stability()
        drift = self.detect_importance_drift()
        noise = self.get_noise_features()

        top_10 = imp.head(10).index.tolist()
        summary = {
            "n_folds": len(self._fold_results),
            "n_features": len(self._feature_names),
            "top_10_features": top_10,
            "noise_feature_count": len(noise),
            "drift_alert_count": len(drift),
            "top_1_pct": float(imp["pct_contribution"].iloc[0]) if len(imp) > 0 else 0.0,
            "top_5_pct": float(imp["pct_contribution"].head(5).sum()) if len(imp) >= 5 else 0.0,
        }

        logger.info(
            "SHAP report: %d features, top-5 explain %.1f%%, %d noise, %d drift alerts",
            summary["n_features"], summary["top_5_pct"],
            summary["noise_feature_count"], summary["drift_alert_count"],
        )

        return {
            "aggregate_importance": imp,
            "temporal_stability": stability,
            "drift_alerts": drift,
            "noise_features": noise,
            "summary": summary,
        }

    def get_noise_features(self) -> list[str]:
        """Return features below importance threshold (noise candidates)."""
        imp = self.get_aggregate_importance()
        threshold = self._config.importance_threshold
        noise = imp[imp["mean_abs_shap"] < threshold].index.tolist()
        if noise:
            logger.info(
                "Noise features (mean|SHAP| < %.4f): %d of %d — %s",
                threshold, len(noise), len(imp), noise[:10],
            )
        return noise

    def get_group_importance(
        self,
        group_mapping: dict[str, list[str]] | None = None,
    ) -> pd.DataFrame:
        """Aggregate importance at feature group level.

        Args:
            group_mapping: Dict of group_name -> feature_name_list.
                If None, attempts to use feature_groups.FEATURE_GROUPS.

        Returns:
            DataFrame indexed by group name with sum/mean SHAP contribution.
        """
        imp = self.get_aggregate_importance()

        if group_mapping is None:
            try:
                from aipha.models.feature_groups import FEATURE_GROUPS
                group_mapping = FEATURE_GROUPS
            except ImportError:
                logger.warning("Cannot import FEATURE_GROUPS; returning empty")
                return pd.DataFrame()

        rows = []
        for group_name, features in group_mapping.items():
            matched = [f for f in features if f in imp.index]
            if not matched:
                continue
            sub = imp.loc[matched]
            rows.append({
                "group": group_name,
                "n_features": len(matched),
                "sum_shap": sub["mean_abs_shap"].sum(),
                "mean_shap": sub["mean_abs_shap"].mean(),
                "top_feature": sub["mean_abs_shap"].idxmax(),
            })

        result = pd.DataFrame(rows)
        if not result.empty:
            result.sort_values("sum_shap", ascending=False, inplace=True)
            result.set_index("group", inplace=True)
        return result
