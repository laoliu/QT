"""多模型 Ensemble — rank_average / weighted_average / stacking。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class EnsembleConfig:
    method: str = "rank_average"  # rank_average | weighted_average | stacking
    weights: dict[str, float] = field(default_factory=dict)


class MultiModelEnsemble:
    """多模型融合器

    支持三种方法:
    - rank_average: 截面排名平均（最稳健）
    - weighted_average: 加权平均
    - stacking: Ridge 堆叠
    """

    def __init__(self, config: EnsembleConfig | None = None):
        self._config = config or EnsembleConfig()
        self._stacking_model = None

    def combine(
        self,
        predictions: dict[str, pd.Series],
        symbols: list[str] | None = None,
    ) -> pd.Series:
        """融合多模型预测。

        Args:
            predictions: {model_name: pd.Series(symbol -> score)}
        """
        if not predictions:
            return pd.Series(dtype=float)

        df = pd.DataFrame(predictions)
        if symbols:
            df = df.reindex(symbols)

        method = self._config.method

        if method == "rank_average":
            return self._rank_average(df)
        elif method == "weighted_average":
            return self._weighted_average(df)
        elif method == "stacking":
            return self._stacking_predict(df)
        else:
            return self._rank_average(df)

    def _rank_average(self, df: pd.DataFrame) -> pd.Series:
        """截面排名平均 — 最稳健方法。"""
        ranked = df.rank(pct=True)
        return ranked.mean(axis=1).rename("ensemble_score")

    def _weighted_average(self, df: pd.DataFrame) -> pd.Series:
        """加权平均。"""
        weights = self._config.weights
        if not weights:
            return df.mean(axis=1).rename("ensemble_score")
        w = np.array([weights.get(col, 1.0) for col in df.columns])
        w = w / w.sum()
        return (df * w).sum(axis=1).rename("ensemble_score")

    def _stacking_predict(self, df: pd.DataFrame) -> pd.Series:
        """Stacking 预测（需要先 fit_stacking）。"""
        if self._stacking_model is not None:
            from sklearn.linear_model import Ridge
            preds = self._stacking_model.predict(df.fillna(0).values)
            return pd.Series(preds, index=df.index, name="ensemble_score")
        # 降级到 rank_average
        return self._rank_average(df)

    def fit_stacking(
        self,
        oos_predictions: dict[str, pd.Series],
        labels: pd.Series,
    ) -> None:
        """训练 Stacking meta-learner (Ridge)。"""
        from sklearn.linear_model import Ridge

        df = pd.DataFrame(oos_predictions)
        common = df.dropna().index.intersection(labels.dropna().index)
        X = df.loc[common].values
        y = labels.loc[common].values

        self._stacking_model = Ridge(alpha=1.0)
        self._stacking_model.fit(X, y)
        logger.info("Stacking meta-learner 训练完成: %d 样本, %d 模型", len(common), X.shape[1])

    def get_top_n(
        self,
        predictions: dict[str, pd.Series],
        n: int = 30,
        ascending: bool = False,
    ) -> list[str]:
        """返回排名前 N 的股票。"""
        scores = self.combine(predictions)
        return scores.sort_values(ascending=ascending).head(n).index.tolist()
