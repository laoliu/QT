"""ML 选股器 — Walk-Forward 训练框架 + 端到端选股流水线。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class MLSelectorConfig:
    model_type: str = "lgbm"  # lgbm, gru, gat, master, double_ensemble
    top_n: int = 30
    retrain_interval: int = 20  # 每 N 个交易日重训
    train_window: int = 500     # 训练窗口（交易日）
    valid_window: int = 60      # 验证窗口
    purge_gap: int = 5          # train/valid 间隔（防未来信息泄漏）
    feature_groups: list[str] = field(default_factory=lambda: ["quality", "valuation", "momentum", "volatility"])


@dataclass
class SelectionResult:
    trade_date: date
    rankings: list[dict[str, Any]]  # [{symbol, score, rank}]
    model_info: dict[str, Any] = field(default_factory=dict)

    @property
    def top_symbols(self) -> list[str]:
        return [r["symbol"] for r in self.rankings]


class MLStockSelector:
    """端到端 ML 选股器。

    支持 Walk-Forward 训练：
    1. 按滚动窗口准备 train/valid 数据
    2. 训练/更新模型
    3. 对当日截面打分、排序
    4. 返回 top-N 股票列表

    用法::

        selector = MLStockSelector(MLSelectorConfig(model_type="lgbm", top_n=30))
        result = selector.select(features_df, labels_series, current_date)
    """

    def __init__(self, config: MLSelectorConfig | None = None) -> None:
        self._config = config or MLSelectorConfig()
        self._model = None
        self._last_train_idx = -1
        self._train_count = 0

    def _create_model(self):
        """根据配置创建模型"""
        mt = self._config.model_type
        if mt == "lgbm":
            from aipha.models.lgbm_ranker import LGBMRanker
            return LGBMRanker()
        elif mt == "gru":
            from aipha.models.gru_ranker import GRURanker
            return GRURanker()
        elif mt == "double_ensemble":
            from aipha.models.double_ensemble import DoubleEnsembleModel
            return DoubleEnsembleModel()
        else:
            from aipha.models.lgbm_ranker import LGBMRanker
            return LGBMRanker()

    def select(
        self,
        features: pd.DataFrame,
        labels: pd.Series,
        current_date: date,
        all_dates: list[date] | None = None,
    ) -> SelectionResult:
        """执行选股。

        Args:
            features: MultiIndex (date, symbol) → 因子列
            labels: 同 Index，下期收益标签
            current_date: 当日日期
            all_dates: 所有交易日列表

        Returns:
            SelectionResult with top-N rankings
        """
        cfg = self._config

        # 判断是否需要重训
        if all_dates is None:
            all_dates = sorted(features.index.get_level_values(0).unique())
        current_idx = all_dates.index(current_date) if current_date in all_dates else -1

        needs_retrain = (
            self._model is None
            or (current_idx - self._last_train_idx >= cfg.retrain_interval)
        )

        if needs_retrain and current_idx >= cfg.train_window + cfg.valid_window + cfg.purge_gap:
            self._retrain(features, labels, all_dates, current_idx)

        # 打分
        if self._model is None:
            return SelectionResult(trade_date=current_date, rankings=[])

        current_features = features.loc[current_date] if current_date in features.index.get_level_values(0) else pd.DataFrame()
        if current_features.empty:
            return SelectionResult(trade_date=current_date, rankings=[])

        symbols = current_features.index.tolist()
        X = current_features.values.astype(np.float64)
        np.nan_to_num(X, copy=False, nan=0.0)

        scores = self._model.predict(X) if hasattr(self._model, "predict") else np.zeros(len(symbols))
        ranked = sorted(zip(symbols, scores), key=lambda x: -x[1])

        rankings = [
            {"symbol": sym, "score": round(float(sc), 6), "rank": i + 1}
            for i, (sym, sc) in enumerate(ranked[:cfg.top_n])
        ]

        return SelectionResult(
            trade_date=current_date,
            rankings=rankings,
            model_info={"train_count": self._train_count, "last_train_idx": self._last_train_idx},
        )

    def _retrain(self, features, labels, all_dates, current_idx):
        cfg = self._config
        train_end_idx = current_idx - cfg.purge_gap - cfg.valid_window
        train_start_idx = max(0, train_end_idx - cfg.train_window)
        valid_end_idx = current_idx - cfg.purge_gap

        train_dates = all_dates[train_start_idx:train_end_idx]
        valid_dates = all_dates[train_end_idx:valid_end_idx]

        X_train = features.loc[features.index.get_level_values(0).isin(train_dates)].values
        y_train = labels.loc[labels.index.get_level_values(0).isin(train_dates)].values

        X_valid = features.loc[features.index.get_level_values(0).isin(valid_dates)].values
        y_valid = labels.loc[labels.index.get_level_values(0).isin(valid_dates)].values

        np.nan_to_num(X_train, copy=False, nan=0.0)
        np.nan_to_num(X_valid, copy=False, nan=0.0)

        if len(X_train) < 100:
            logger.warning(f"Training data too small: {len(X_train)}")
            return

        model = self._create_model()
        try:
            if cfg.model_type == "lgbm":
                group = np.array([len(train_dates)] * (len(X_train) // max(1, len(train_dates)) + 1))[:len(X_train)]
                group_valid = np.array([len(valid_dates)] * (len(X_valid) // max(1, len(valid_dates)) + 1))[:len(X_valid)]
                model.fit(X_train, y_train, group, X_valid, y_valid, group_valid)
            else:
                model.fit(X_train, y_train, X_valid, y_valid)
        except Exception as e:
            logger.error(f"Retrain failed: {e}")
            return

        self._model = model
        self._last_train_idx = current_idx
        self._train_count += 1
        logger.info(f"Retrained model #{self._train_count} at idx={current_idx}")
