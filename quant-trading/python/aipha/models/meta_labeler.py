"""Meta-Labeling: secondary classifier for bet sizing.

Implements the Meta-Labeling framework from López de Prado (2018), Ch. 3:
- Primary model outputs directional signal (buy/sell/hold)
- Secondary (meta) model learns P(primary is correct)
- Final position = primary_side × meta_confidence × size

Key benefits:
- Separates signal generation from bet sizing
- Improves precision by filtering low-confidence primary signals
- Enables independent calibration of signal quality

Usage:
    labeler = MetaLabeler(config)
    labeler.fit(X_train, primary_signals, y_true)
    meta_proba = labeler.predict_proba(X_test, primary_signals_test)
    # Adjust position sizes by meta_proba
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
class MetaLabelConfig:
    """Meta-Labeling configuration."""

    model_type: str = "lightgbm"
    """Secondary model type: 'lightgbm' or 'logistic'."""

    confidence_threshold: float = 0.55
    """Minimum meta-model probability to pass signal through."""

    bet_size_mode: str = "linear"
    """Bet sizing: 'binary' (pass/block), 'linear' (proportional), 'concave' (sqrt)."""

    n_estimators: int = 200
    """Number of boosting rounds for LightGBM meta-model."""

    max_depth: int = 4
    """Max tree depth (keep shallow to avoid overfitting)."""

    learning_rate: float = 0.05

    subsample: float = 0.8

    class_weight: str = "balanced"
    """Handle imbalanced correct/incorrect labels."""

    extra_features: list[str] = field(default_factory=list)
    """Additional features for meta-model (e.g., volatility, volume)."""


# ------------------------------------------------------------------
# Core class
# ------------------------------------------------------------------

class MetaLabeler:
    """Meta-Labeling secondary classifier for bet sizing.

    Workflow:
        1. Primary model generates directional signals (+1/-1/0)
        2. Meta-labeler creates binary labels: 1 if primary was correct, 0 if not
        3. Meta-model (LightGBM classifier) learns P(primary_correct | features)
        4. At inference, meta_proba filters or sizes bets
    """

    def __init__(self, config: MetaLabelConfig | None = None) -> None:
        self._config = config or MetaLabelConfig()
        self._model: Any = None
        self._feature_names: list[str] = []
        self._fitted = False

    def create_meta_labels(
        self,
        primary_signals: np.ndarray | pd.Series,
        actual_returns: np.ndarray | pd.Series,
    ) -> np.ndarray:
        """Create binary meta-labels: 1 if primary signal direction matches actual return.

        For primary_signal > 0: correct if actual_return > 0
        For primary_signal < 0: correct if actual_return < 0
        For primary_signal == 0: always incorrect (held when should have traded, or vice versa)
        """
        signals = np.asarray(primary_signals, dtype=np.float64)
        returns = np.asarray(actual_returns, dtype=np.float64)

        # Direction match
        meta_labels = np.zeros(len(signals), dtype=np.int32)
        mask_long = signals > 0
        mask_short = signals < 0

        meta_labels[mask_long & (returns > 0)] = 1
        meta_labels[mask_short & (returns < 0)] = 1

        n_correct = meta_labels.sum()
        logger.info(
            "Meta-labels: %d correct / %d total (%.1f%%)",
            n_correct, len(meta_labels), 100.0 * n_correct / max(len(meta_labels), 1),
        )
        return meta_labels

    def fit(
        self,
        X: pd.DataFrame | np.ndarray,
        primary_signals: np.ndarray | pd.Series,
        actual_returns: np.ndarray | pd.Series,
    ) -> "MetaLabeler":
        """Train meta-model on features + meta-labels.

        Args:
            X: Feature matrix (same features used by primary model).
            primary_signals: Primary model directional signals.
            actual_returns: Actual forward returns (for creating meta-labels).

        Returns:
            self (fitted MetaLabeler).
        """
        cfg = self._config
        meta_y = self.create_meta_labels(primary_signals, actual_returns)

        if isinstance(X, pd.DataFrame):
            self._feature_names = list(X.columns)
            X_arr = X.values
        else:
            X_arr = X
            self._feature_names = [f"f_{i}" for i in range(X_arr.shape[1])]

        # Filter out NaN/Inf rows
        valid = np.isfinite(X_arr).all(axis=1)
        X_arr = X_arr[valid]
        meta_y = meta_y[valid]

        if cfg.model_type == "lightgbm":
            import lightgbm as lgb

            is_balanced = cfg.class_weight == "balanced"
            self._model = lgb.LGBMClassifier(
                n_estimators=cfg.n_estimators,
                max_depth=cfg.max_depth,
                learning_rate=cfg.learning_rate,
                subsample=cfg.subsample,
                is_unbalance=is_balanced,
                verbose=-1,
                random_state=42,
            )
            self._model.fit(X_arr, meta_y)

        elif cfg.model_type == "logistic":
            from sklearn.linear_model import LogisticRegression

            self._model = LogisticRegression(
                class_weight=cfg.class_weight,
                max_iter=1000,
                random_state=42,
            )
            self._model.fit(X_arr, meta_y)
        else:
            raise ValueError(f"Unknown model_type: {cfg.model_type}")

        self._fitted = True
        logger.info("Meta-model fitted: %s, %d samples", cfg.model_type, len(meta_y))
        return self

    def predict_proba(
        self,
        X: pd.DataFrame | np.ndarray,
        primary_signals: np.ndarray | pd.Series | None = None,
    ) -> np.ndarray:
        """Predict P(primary_correct) for new data.

        Args:
            X: Feature matrix.
            primary_signals: Optional (unused in current impl, reserved for
                signal-conditioned meta-models).

        Returns:
            Array of probabilities P(correct) ∈ [0, 1].
        """
        if not self._fitted:
            raise RuntimeError("MetaLabeler not fitted. Call fit() first.")

        if isinstance(X, pd.DataFrame):
            X_arr = X.values
        else:
            X_arr = X

        proba = self._model.predict_proba(X_arr)
        # Class 1 = correct
        if proba.ndim == 2:
            return proba[:, 1]
        return proba

    def apply_bet_sizing(
        self,
        primary_signals: np.ndarray | pd.Series,
        meta_proba: np.ndarray,
    ) -> np.ndarray:
        """Apply bet sizing based on meta-model confidence.

        Args:
            primary_signals: Directional signals from primary model.
            meta_proba: P(correct) from predict_proba().

        Returns:
            Sized signals: primary_signal × size_factor.
            Signals below confidence_threshold are zeroed.
        """
        cfg = self._config
        signals = np.asarray(primary_signals, dtype=np.float64)
        proba = np.asarray(meta_proba, dtype=np.float64)

        # Zero out low-confidence
        mask = proba >= cfg.confidence_threshold

        if cfg.bet_size_mode == "binary":
            # Pass or block
            sized = np.where(mask, signals, 0.0)

        elif cfg.bet_size_mode == "linear":
            # Scale by confidence: signal × proba
            sized = np.where(mask, signals * proba, 0.0)

        elif cfg.bet_size_mode == "concave":
            # Concave (sqrt) sizing: less aggressive scaling
            sized = np.where(mask, signals * np.sqrt(proba), 0.0)

        else:
            raise ValueError(f"Unknown bet_size_mode: {cfg.bet_size_mode}")

        n_passed = mask.sum()
        n_total = len(signals)
        logger.info(
            "Bet sizing (%s): %d/%d signals passed (%.1f%%), threshold=%.2f",
            cfg.bet_size_mode, n_passed, n_total,
            100.0 * n_passed / max(n_total, 1), cfg.confidence_threshold,
        )
        return sized

    def get_diagnostics(self) -> dict[str, Any]:
        """Return meta-model diagnostics (feature importances if available)."""
        if not self._fitted:
            return {"fitted": False}

        diag: dict[str, Any] = {
            "fitted": True,
            "model_type": self._config.model_type,
            "confidence_threshold": self._config.confidence_threshold,
            "bet_size_mode": self._config.bet_size_mode,
        }

        if hasattr(self._model, "feature_importances_"):
            imp = self._model.feature_importances_
            sorted_idx = np.argsort(imp)[::-1]
            diag["top_features"] = [
                (self._feature_names[i], float(imp[i]))
                for i in sorted_idx[:10]
            ]

        return diag
