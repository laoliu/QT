"""DoubleEnsemble 模型 — 基于 LightGBM 的样本重加权 + 特征选择集成方法。

原始论文: Zhang et al., "DoubleEnsemble: A New Ensemble Method Based on
Sample Reweighting and Feature Selection for Financial Data Analysis"
(ICDM 2020). https://arxiv.org/pdf/2010.01265.pdf

参考实现: Microsoft Qlib (MIT License)
https://github.com/microsoft/qlib/blob/main/qlib/contrib/model/double_ensemble.py

Qlib Benchmark (CSI300 + Alpha158, 20 次平均):
- DoubleEnsemble: IC=0.0521, ICIR=0.4223, AR=11.58%, IR=1.3432
- LightGBM:       IC=0.0448, ICIR=0.3660, AR= 9.01%, IR=1.0164

核心思想:
1. 样本重加权 (SR): 基于训练损失曲线识别关键样本
   - 高 h 值 = 模型未充分学习 → 增加权重
   - 低 h 值 = 已学好 / 噪声 → 降低权重
2. 特征选择 (FS): 基于特征打乱后的损失变化选择关键特征
   - 高 g 值 = 重要特征 → 更大概率被选中
   - 低 g 值 = 噪声特征 → 更小概率被选中
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class DEnsembleConfig:
    """DoubleEnsemble 配置。"""

    num_models: int = 6
    enable_sr: bool = True
    enable_fs: bool = True
    alpha1: float = 1.0
    alpha2: float = 1.0
    bins_sr: int = 10
    bins_fs: int = 5
    decay: float | None = None
    sample_ratios: list[float] = field(
        default_factory=lambda: [0.8, 0.7, 0.6, 0.5, 0.4],
    )
    sub_weights: list[float] | None = None
    epochs: int = 100
    early_stopping_rounds: int | None = 50
    loss: str = "mse"
    # LightGBM 超参
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
        """训练 DoubleEnsemble 模型。

        Args:
            X_train: 训练特征 (N, F)。
            y_train: 训练标签 (N,)。
            X_valid: 验证特征，用于 early stopping。
            y_valid: 验证标签。
            feature_names: 特征名称列表。
        """
        cfg = self._config
        N, F = X_train.shape
        self._feature_names = (
            feature_names or [f"f{i}" for i in range(F)]
        )
        if cfg.sub_weights is not None:
            self._sub_weights = list(cfg.sub_weights)
        else:
            self._sub_weights = [1.0] * cfg.num_models

        weights = np.ones(N, dtype=np.float64)
        feat_idx = list(range(F))  # active feature indices
        pred_sub = np.zeros((N, cfg.num_models))

        self._ensemble.clear()
        self._sub_features.clear()

        for k in range(cfg.num_models):
            feat_names_k = [self._feature_names[i] for i in feat_idx]
            self._sub_features.append(feat_names_k)
            logger.info(
                "DoubleEnsemble sub-model %d/%d: %d features",
                k + 1, cfg.num_models, len(feat_idx),
            )
            model_k = self._train_sub(
                X_train, y_train, weights, feat_idx,
                X_valid, y_valid,
            )
            self._ensemble.append(model_k)

            if k + 1 == cfg.num_models:
                break

            # 计算当前子模型的训练集预测和损失曲线
            loss_curve = self._get_loss_curve(
                model_k, X_train[:, feat_idx], y_train,
            )
            pred_k = model_k.predict(X_train[:, feat_idx])
            pred_sub[:, k] = pred_k
            w_arr = np.array(self._sub_weights[: k + 1])
            pred_ens = (pred_sub[:, : k + 1] * w_arr).sum(1) / w_arr.sum()
            loss_vals = self._get_loss(y_train, pred_ens)

            if cfg.enable_sr:
                weights = self._sample_reweight(
                    loss_curve, loss_vals, k + 1,
                )
            if cfg.enable_fs:
                feat_idx = self._feature_select(
                    X_train, y_train, feat_idx, loss_vals,
                )
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        """预测: 所有子模型加权平均。"""
        if not self._ensemble:
            raise RuntimeError("Model not fitted yet.")
        N = X.shape[0]
        pred = np.zeros(N, dtype=np.float64)
        for i, (model, feat_names) in enumerate(
            zip(self._ensemble, self._sub_features),
        ):
            feat_idx = [
                self._feature_names.index(f) for f in feat_names
            ]
            pred += model.predict(X[:, feat_idx]) * self._sub_weights[i]
        pred /= sum(self._sub_weights[: len(self._ensemble)])
        return pred

    def feature_importance(self) -> dict[str, float]:
        """聚合所有子模型的特征重要性 (gain)。"""
        agg: dict[str, float] = {}
        for model, feat_names, w in zip(
            self._ensemble, self._sub_features, self._sub_weights,
        ):
            imp = model.feature_importance(importance_type="gain")
            for j, fname in enumerate(feat_names):
                if j < len(imp):
                    agg[fname] = agg.get(fname, 0.0) + imp[j] * w
        total_w = sum(self._sub_weights[: len(self._ensemble)])
        return {k: v / total_w for k, v in agg.items()}

    # -- 内部方法 (stubs) --

    def _train_sub(self, X, y, w, feat_idx, X_v, y_v) -> Any:
        """训练单个子模型 (LightGBM)。"""
        import lightgbm as lgb

        cfg = self._config
        X_sub = X[:, feat_idx]
        feat_names_k = [self._feature_names[i] for i in feat_idx]
        dtrain = lgb.Dataset(
            X_sub, label=y, weight=w, feature_name=feat_names_k,
        )
        callbacks = [lgb.log_evaluation(period=0)]
        valid_sets = [dtrain]
        valid_names = ["train"]

        if X_v is not None and y_v is not None:
            X_v_sub = X_v[:, feat_idx]
            dvalid = lgb.Dataset(X_v_sub, label=y_v, feature_name=feat_names_k)
            valid_sets.append(dvalid)
            valid_names.append("valid")
            if cfg.early_stopping_rounds:
                callbacks.append(
                    lgb.early_stopping(cfg.early_stopping_rounds),
                )

        params = {
            "objective": cfg.loss,
            "metric": "mse" if cfg.loss == "mse" else "binary_logloss",
            "learning_rate": cfg.learning_rate,
            "max_depth": cfg.max_depth,
            "num_leaves": cfg.num_leaves,
            "min_child_samples": cfg.min_child_samples,
            "subsample": cfg.subsample,
            "colsample_bytree": cfg.colsample_bytree,
            "reg_alpha": cfg.reg_alpha,
            "reg_lambda": cfg.reg_lambda,
            "verbose": -1,
            "seed": cfg.seed,
        }
        model = lgb.train(
            params, dtrain,
            num_boost_round=cfg.epochs,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
        )
        return model

    def _sample_reweight(self, loss_curve, loss_values, k) -> np.ndarray:
        """SR 模块: 基于训练动态重加权样本。

        高 h 值的样本 = 模型尚未学好 → 权重增大。
        """
        cfg = self._config
        N, T = loss_curve.shape
        # 对损失曲线和损失值做排名归一化
        lc_rank = np.apply_along_axis(
            lambda col: pd.Series(col).rank(pct=True).values,
            0, loss_curve,
        )
        lv_rank = pd.Series(-loss_values).rank(pct=True).values

        # 训练曲线的起始段和结束段的平均排名
        part = max(int(T * 0.1), 1)
        l_start = lc_rank[:, :part].mean(axis=1)
        l_end = lc_rank[:, -part:].mean(axis=1)

        # h1 = 当前集成损失排名, h2 = 学习速度排名
        h1 = lv_rank
        ratio = l_end / np.clip(l_start, 1e-8, None)
        h2 = pd.Series(ratio).rank(pct=True).values
        h = cfg.alpha1 * h1 + cfg.alpha2 * h2

        # 按 bins 分组并赋权
        h_series = pd.Series(h)
        bins = pd.cut(h_series, cfg.bins_sr, labels=False)
        bins = bins.fillna(0).astype(int)
        h_avg = h_series.groupby(bins).mean()
        decay = cfg.decay if cfg.decay is not None else 0.9
        weights = np.zeros(N, dtype=np.float64)
        for b in h_avg.index:
            mask = bins.values == b
            weights[mask] = 1.0 / (decay ** k * h_avg[b] + 0.1)
        return weights

    def _feature_select(self, X, y, feat_idx, loss_vals) -> list[int]:
        """FS 模块: 基于特征打乱后的损失变化选择特征。"""
        cfg = self._config
        N = X.shape[0]
        F = len(feat_idx)
        g = np.zeros(F, dtype=np.float64)

        X_tmp = X.astype(np.float32, copy=True)  # float32 节省内存
        # 当样本量过大时子采样以避免 OOM (permutation importance 不需全量)
        _FS_MAX_SAMPLES = 500_000
        if N > _FS_MAX_SAMPLES:
            _fs_idx = np.random.choice(N, _FS_MAX_SAMPLES, replace=False)
            X_tmp = X_tmp[_fs_idx]
            y = y[_fs_idx]
            loss_vals = loss_vals[_fs_idx]
            N = _FS_MAX_SAMPLES
        for i_f, fi in enumerate(feat_idx):
            orig = X_tmp[:, fi].copy()
            X_tmp[:, fi] = np.random.permutation(orig)
            # 用当前集成预测打乱后的损失
            pred = np.zeros(N)
            for i_s, (model, feat_names) in enumerate(
                zip(self._ensemble, self._sub_features),
            ):
                sub_idx = [
                    self._feature_names.index(f)
                    for f in feat_names
                ]
                pred += (
                    model.predict(X_tmp[:, sub_idx])
                    * self._sub_weights[i_s]
                )
            pred /= sum(self._sub_weights[: len(self._ensemble)])
            loss_shuffled = self._get_loss(y, pred)
            diff = loss_shuffled - loss_vals
            g[i_f] = diff.mean() / (diff.std() + 1e-7)
            X_tmp[:, fi] = orig

        # g 值 NaN 替换为 0
        g = np.nan_to_num(g, nan=0.0)
        # 按 g 值分 bins, 高 g 值（重要）的 bin 采样率更高
        g_series = pd.Series(g)
        bins = pd.cut(g_series, cfg.bins_fs, labels=False)
        bins = bins.fillna(0).astype(int)
        sorted_bins = sorted(bins.unique(), reverse=True)
        res_feat: list[int] = []
        for i_b, b in enumerate(sorted_bins):
            b_feats = [feat_idx[j] for j in range(F) if bins.iloc[j] == b]
            ratio = cfg.sample_ratios[min(i_b, len(cfg.sample_ratios) - 1)]
            n_sel = max(int(np.ceil(ratio * len(b_feats))), 1)
            sel = np.random.choice(b_feats, size=min(n_sel, len(b_feats)), replace=False)
            res_feat.extend(sel.tolist())
        return sorted(set(res_feat))

    def _get_loss(self, y_true: np.ndarray, y_pred: np.ndarray) -> np.ndarray:
        """计算逐样本损失。"""
        return (y_true - y_pred) ** 2

    def _get_loss_curve(
        self, model: Any, X: np.ndarray, y: np.ndarray,
    ) -> np.ndarray:
        """获取训练过程中每棵树后的逐样本损失曲线。

        Returns:
            shape (N, num_trees) 的损失矩阵。
        """
        num_trees = model.num_trees()
        N = X.shape[0]
        loss_curve = np.zeros((N, num_trees), dtype=np.float64)
        pred_accum = np.zeros(N, dtype=np.float64)
        for t in range(num_trees):
            pred_accum += model.predict(
                X, start_iteration=t, num_iteration=1,
            )
            loss_curve[:, t] = self._get_loss(y, pred_accum)
        return loss_curve
