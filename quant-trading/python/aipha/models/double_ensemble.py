"""DoubleEnsemble 模型 — 样本重加权 + 特征选择集成。

Zhang et al. (ICDM 2020) "DoubleEnsemble: A New Ensemble Method Based on
Sample Reweighting and Feature Selection for Financial Data Analysis"

Qlib 基准 (CSI300 + Alpha158): IC=0.0521, ICIR=0.4223, AR=11.58%
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DEnsembleConfig:
    num_models: int = 6
    enable_sr: bool = True
    enable_fs: bool = True
    alpha1: float = 1.0
    alpha2: float = 1.0
    bins_sr: int = 10
    bins_fs: int = 5
    decay: float | None = None
    sample_ratios: list[float] = field(default_factory=lambda: [0.8, 0.7, 0.6, 0.5, 0.4])
    sub_weights: list[float] | None = None
    epochs: int = 100
    early_stopping_rounds: int | None = 50
    loss: str = "mse"
    learning_rate: float = 0.05
    max_depth: int = 6
    num_leaves: int = 31
    min_child_samples: int = 50
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_alpha: float = 0.1
    reg_lambda: float = 1.0
    seed: int = 42


class DoubleEnsembleModel:
    """DoubleEnsemble 集成模型。"""

    def __init__(self, config: DEnsembleConfig | None = None) -> None:
        self._config = config or DEnsembleConfig()
        self._ensemble: list[Any] = []
        self._sub_features: list[list[str]] = []
        self._sub_weights: list[float] = []
        self._feature_names: list[str] = []

    def fit(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_valid: np.ndarray | None = None,
        y_valid: np.ndarray | None = None,
        feature_names: list[str] | None = None,
    ) -> "DoubleEnsembleModel":
        cfg = self._config
        N, F = X_train.shape
        self._feature_names = feature_names or [f"f{i}" for i in range(F)]
        self._sub_weights = list(cfg.sub_weights) if cfg.sub_weights else [1.0] * cfg.num_models

        weights = np.ones(N, dtype=np.float64)
        feat_idx = list(range(F))
        pred_sub = np.zeros((N, cfg.num_models))

        self._ensemble.clear()
        self._sub_features.clear()

        for k in range(cfg.num_models):
            feat_names_k = [self._feature_names[i] for i in feat_idx]
            self._sub_features.append(feat_names_k)
            logger.info("DoubleEnsemble sub-model %d/%d: %d features", k + 1, cfg.num_models, len(feat_idx))

            model_k = self._train_sub(X_train, y_train, weights, feat_idx, X_valid, y_valid)
            self._ensemble.append(model_k)

            if k + 1 == cfg.num_models:
                break

            pred_k = model_k.predict(X_train[:, feat_idx])
            pred_sub[:, k] = pred_k
            w_arr = np.array(self._sub_weights[:k + 1])
            pred_ens = (pred_sub[:, :k + 1] * w_arr).sum(1) / w_arr.sum()
            loss_vals = (y_train - pred_ens) ** 2

            if cfg.enable_sr:
                weights = self._sample_reweight(loss_vals, k + 1)
            if cfg.enable_fs:
                feat_idx = self._feature_select(X_train, y_train, feat_idx, loss_vals)

        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if not self._ensemble:
            raise RuntimeError("Model not fitted.")
        pred = np.zeros(X.shape[0], dtype=np.float64)
        for i, (model, feat_names) in enumerate(zip(self._ensemble, self._sub_features)):
            feat_idx = [self._feature_names.index(f) for f in feat_names]
            pred += model.predict(X[:, feat_idx]) * self._sub_weights[i]
        return pred / sum(self._sub_weights[:len(self._ensemble)])

    def feature_importance(self) -> dict[str, float]:
        agg: dict[str, float] = {}
        for model, feat_names, w in zip(self._ensemble, self._sub_features, self._sub_weights):
            imp = model.feature_importance(importance_type="gain")
            for j, fname in enumerate(feat_names):
                if j < len(imp):
                    agg[fname] = agg.get(fname, 0.0) + imp[j] * w
        total_w = sum(self._sub_weights[:len(self._ensemble)])
        return {k: v / total_w for k, v in agg.items()}

    def _train_sub(self, X, y, w, feat_idx, X_v, y_v):
        import lightgbm as lgb
        cfg = self._config
        params = {
            "objective": cfg.loss, "learning_rate": cfg.learning_rate,
            "max_depth": cfg.max_depth, "num_leaves": cfg.num_leaves,
            "min_child_samples": cfg.min_child_samples,
            "subsample": cfg.subsample, "colsample_bytree": cfg.colsample_bytree,
            "reg_alpha": cfg.reg_alpha, "reg_lambda": cfg.reg_lambda,
            "seed": cfg.seed, "verbosity": -1,
        }
        dtrain = lgb.Dataset(X[:, feat_idx], label=y, weight=w)
        callbacks = []
        valid_sets = [dtrain]
        if X_v is not None and y_v is not None:
            dval = lgb.Dataset(X_v[:, feat_idx], label=y_v)
            valid_sets.append(dval)
            if cfg.early_stopping_rounds:
                callbacks.append(lgb.early_stopping(cfg.early_stopping_rounds))
        callbacks.append(lgb.log_evaluation(period=0))
        return lgb.train(params, dtrain, num_boost_round=cfg.epochs, valid_sets=valid_sets, callbacks=callbacks)

    def _sample_reweight(self, loss_vals: np.ndarray, step: int) -> np.ndarray:
        """SR: 高损失样本 → 高权重"""
        cfg = self._config
        n = len(loss_vals)
        bins = np.quantile(loss_vals, np.linspace(0, 1, cfg.bins_sr + 1))
        bin_idx = np.digitize(loss_vals, bins[1:-1])
        h = np.zeros(n)
        for b in range(cfg.bins_sr):
            mask = bin_idx == b
            h[mask] = b / cfg.bins_sr
        weights = 1.0 + cfg.alpha1 * h
        return weights / weights.sum() * n

    def _feature_select(self, X, y, feat_idx, loss_vals) -> list[int]:
        """FS: 基于特征重要性的概率选择"""
        cfg = self._config
        rng = np.random.RandomState(cfg.seed + len(self._ensemble))
        n_select = max(5, int(len(feat_idx) * 0.7))
        if len(feat_idx) <= n_select:
            return feat_idx
        # 使用方差作为简化的特征重要性度量
        importances = np.var(X[:, feat_idx], axis=0)
        importances = importances / (importances.sum() + 1e-15)
        probs = importances ** cfg.alpha2
        probs = probs / probs.sum()
        selected = rng.choice(len(feat_idx), size=n_select, replace=False, p=probs)
        return [feat_idx[i] for i in sorted(selected)]

    def save(self, path: str) -> None:
        import joblib
        from pathlib import Path as P
        P(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "config": self._config, "ensemble": self._ensemble,
            "sub_features": self._sub_features, "sub_weights": self._sub_weights,
            "feature_names": self._feature_names,
        }, path)

    def load(self, path: str) -> None:
        import joblib
        data = joblib.load(path)
        self._config = data["config"]
        self._ensemble = data["ensemble"]
        self._sub_features = data["sub_features"]
        self._sub_weights = data["sub_weights"]
        self._feature_names = data["feature_names"]
