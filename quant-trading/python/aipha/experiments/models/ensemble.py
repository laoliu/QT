"""Experimental 多模型集成预测框架

支持 LightGBM + XGBoost + ElasticNet 三模型集成，
通过 rank-average / weighted-average / stacking 合成最终预测。

集成方法：
1. rank_average: 每个模型预测做截面 rank → 求平均 rank（最稳健）
2. weighted_average: 按指定权重加权预测值（需预测量纲一致或先归一化）
3. stacking: 用 Ridge 做二层堆叠（需额外 OOS 预测做训练）

理论基础：
- Breiman (1996): Bagging reduces variance; model diversity → better ensemble
- Gu, Kelly, Xiu (2020): Ensemble of heterogeneous models best for financial prediction
- 异质模型集成 > 同质集成（树 + 线性 + 非参数互补）

对齐 copilot-instructions.md § P2 多模型集成要求。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

EnsembleMethod = Literal["rank_average", "weighted_average", "stacking"]


@dataclass
class EnsembleConfig:
    """集成配置。"""

    method: EnsembleMethod = "rank_average"
    model_configs: list[dict[str, Any]] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    stacking_alpha: float = 1.0


class MultiModelEnsemble:
    """多模型集成预测器。"""

    def __init__(self, config: EnsembleConfig) -> None:
        self._config = config
        self._selectors: list[Any] = []
        self._stacking_model: Any = None

    # --- 公开接口 ---

    def fit(
        self, panel: pd.DataFrame, label_col: str = "fwd_return_rank",
    ) -> dict[str, Any]:
        """训练所有子模型并返回汇总 WF 结果。

        每个子模型独立做 Walk-Forward 训练，集成层汇总 OOS 预测。
        """
        from aipha.models.ml_stock_selector import (
            MLSelectorConfig,
            MLStockSelector,
        )

        cfg = self._config
        self._selectors.clear()
        all_summaries: list[dict[str, Any]] = []

        for i, mcfg in enumerate(cfg.model_configs):
            sel_cfg = MLSelectorConfig(**mcfg)
            selector = MLStockSelector(sel_cfg)
            logger.info(
                "Ensemble fit sub-model %d/%d: %s",
                i + 1, len(cfg.model_configs), sel_cfg.model_type,
            )
            oos_df = selector.walk_forward_train(panel)
            summary = selector.get_wf_summary()
            summary["sub_model_idx"] = i
            summary["sub_model_type"] = sel_cfg.model_type
            all_summaries.append(summary)
            self._selectors.append(selector)

        # 合成集成汇总
        ensemble_summary: dict[str, Any] = {
            "n_sub_models": len(self._selectors),
            "method": cfg.method,
            "sub_summaries": all_summaries,
        }
        if all_summaries:
            srs = [s.get("overall_oos_ic", 0) for s in all_summaries]
            ensemble_summary["mean_sub_oos_ic"] = float(np.nanmean(srs))
        return ensemble_summary

    def predict(self, df: pd.DataFrame) -> pd.Series:
        """集成预测：返回 symbol → score。

        根据 config.method 选择合成方式。
        """
        if not self._selectors:
            raise RuntimeError("No models fitted. Call fit() first.")

        preds: list[np.ndarray] = []
        for sel in self._selectors:
            scores = sel.predict(df)
            preds.append(scores.values)

        cfg = self._config
        if cfg.method == "rank_average":
            combined = self._rank_average(preds)
        elif cfg.method == "weighted_average":
            combined = self._weighted_average(preds)
        elif cfg.method == "stacking":
            X_stack = np.column_stack(preds)
            if self._stacking_model is None:
                combined = self._rank_average(preds)
            else:
                combined = self._stacking_model.predict(X_stack)
        else:
            combined = self._rank_average(preds)

        idx = self._selectors[0].predict(df).index
        return pd.Series(combined, index=idx, name="ensemble_score")

    def save(self, path: Path) -> None:
        """保存所有子模型。"""
        import joblib

        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        for i, sel in enumerate(self._selectors):
            sub_dir = path / f"sub_{i}"
            sel.save(sub_dir)
        joblib.dump(
            {
                "config": self._config,
                "stacking_model": self._stacking_model,
            },
            path / "ensemble_meta.pkl",
        )
        logger.info("Saved ensemble (%d sub-models) to %s", len(self._selectors), path)

    def load(self, path: Path, *, allow_experimental_models: bool = False) -> None:
        """加载所有子模型。"""
        import joblib
        from aipha.models.ml_stock_selector import (
            MLSelectorConfig,
            MLStockSelector,
        )

        path = Path(path)
        meta = joblib.load(path / "ensemble_meta.pkl")
        self._config = meta["config"]
        self._stacking_model = meta.get("stacking_model")
        self._selectors.clear()
        sub_dirs = sorted(path.glob("sub_*"))
        for sd in sub_dirs:
            sel = MLStockSelector(MLSelectorConfig())
            sel.load(sd, allow_experimental_models=allow_experimental_models)
            self._selectors.append(sel)
        logger.info("Loaded ensemble (%d sub-models) from %s", len(self._selectors), path)

    # --- 内部方法 ---

    def _rank_average(self, preds: list[np.ndarray]) -> np.ndarray:
        """截面 rank 平均：最稳健的集成方法。"""
        from scipy.stats import rankdata

        n = len(preds[0])
        rank_sum = np.zeros(n)
        for p in preds:
            rank_sum += rankdata(p, method="average")
        return rank_sum / len(preds)

    def _weighted_average(self, preds: list[np.ndarray]) -> np.ndarray:
        """加权平均（需预测量纲一致）。"""
        cfg = self._config
        weights = cfg.weights
        if len(weights) != len(preds):
            weights = [1.0 / len(preds)] * len(preds)
        total_w = sum(weights)
        result = np.zeros(len(preds[0]))
        for w, p in zip(weights, preds):
            result += (w / total_w) * p
        return result

    def _fit_stacking(
        self, oos_preds: list[np.ndarray], y: np.ndarray,
    ) -> None:
        """训练 stacking 二层模型（Ridge 回归）。"""
        from sklearn.linear_model import Ridge

        X = np.column_stack(oos_preds)
        model = Ridge(alpha=self._config.stacking_alpha)
        model.fit(X, y)
        self._stacking_model = model
        logger.info(
            "Stacking model fitted: %d features, alpha=%.2f",
            X.shape[1], self._config.stacking_alpha,
        )
