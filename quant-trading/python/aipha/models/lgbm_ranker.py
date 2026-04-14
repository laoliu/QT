"""LightGBM 排序模型 — LambdaRank 股票排序。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class LGBMRankerConfig:
    """LightGBM 排序配置"""
    objective: str = "lambdarank"
    metric: str = "ndcg"
    n_estimators: int = 500
    learning_rate: float = 0.05
    max_depth: int = 6
    num_leaves: int = 31
    min_child_samples: int = 20
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    reg_alpha: float = 0.1
    reg_lambda: float = 1.0
    early_stopping_rounds: int = 50
    verbose: int = -1
    extra_params: dict[str, Any] = field(default_factory=dict)


class LGBMRanker:
    """LightGBM LambdaRank 股票排序模型"""

    def __init__(self, config: LGBMRankerConfig | None = None):
        self._config = config or LGBMRankerConfig()
        self._model = None
        self._feature_names: list[str] = []

    def fit(
        self,
        X_train: pd.DataFrame | np.ndarray,
        y_train: np.ndarray,
        group_train: np.ndarray,
        X_valid: Optional[pd.DataFrame | np.ndarray] = None,
        y_valid: Optional[np.ndarray] = None,
        group_valid: Optional[np.ndarray] = None,
    ) -> dict[str, Any]:
        """训练排序模型。

        Args:
            X_train: 训练特征
            y_train: 标签（如未来收益排名）
            group_train: 每组样本数（按日期分组）
        """
        try:
            import lightgbm as lgb
        except ImportError:
            raise ImportError("需要安装 lightgbm: pip install lightgbm")

        if isinstance(X_train, pd.DataFrame):
            self._feature_names = list(X_train.columns)

        params = {
            "objective": self._config.objective,
            "metric": self._config.metric,
            "n_estimators": self._config.n_estimators,
            "learning_rate": self._config.learning_rate,
            "max_depth": self._config.max_depth,
            "num_leaves": self._config.num_leaves,
            "min_child_samples": self._config.min_child_samples,
            "subsample": self._config.subsample,
            "colsample_bytree": self._config.colsample_bytree,
            "reg_alpha": self._config.reg_alpha,
            "reg_lambda": self._config.reg_lambda,
            "verbose": self._config.verbose,
            **self._config.extra_params,
        }

        self._model = lgb.LGBMRanker(**params)

        eval_set = [(X_valid, y_valid)] if X_valid is not None else None
        eval_group = [group_valid] if group_valid is not None else None

        callbacks = []
        if self._config.early_stopping_rounds > 0 and eval_set:
            callbacks.append(lgb.early_stopping(self._config.early_stopping_rounds))

        self._model.fit(
            X_train, y_train,
            group=group_train,
            eval_set=eval_set,
            eval_group=eval_group,
            callbacks=callbacks if callbacks else None,
        )

        result = {"n_estimators": self._model.best_iteration_ or self._config.n_estimators}
        logger.info("LGBMRanker 训练完成: best_iter=%d", result["n_estimators"])
        return result

    def predict(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """预测排序得分。"""
        if self._model is None:
            raise RuntimeError("模型未训练")
        return self._model.predict(X)

    def feature_importance(self, importance_type: str = "gain") -> dict[str, float]:
        """特征重要性。"""
        if self._model is None:
            return {}
        imp = self._model.feature_importances_
        names = self._feature_names or [f"f{i}" for i in range(len(imp))]
        return dict(sorted(zip(names, imp), key=lambda x: -x[1]))

    def save(self, path: str) -> None:
        import joblib
        joblib.dump({"model": self._model, "features": self._feature_names, "config": self._config}, path)
        logger.info("模型已保存: %s", path)

    def load(self, path: str) -> None:
        import joblib
        data = joblib.load(path)
        self._model = data["model"]
        self._feature_names = data.get("features", [])
        self._config = data.get("config", self._config)
        logger.info("模型已加载: %s", path)
