"""CB LightGBM Cross-Sectional Ranker — 可转债 ML 排名模型。

使用 LightGBM lambdarank 对可转债截面进行打分排名，
特征来自 cb_factors + 传统因子（价格/溢价率/转股价值等）。

训练方案：Purged Walk-Forward（3yr train / 6m test），避免前视偏差。
标签定义：持仓期（rebalance_days）内收益率的截面排名分位。

理论基础：
- Gu, Kelly, Xiu (2020) — 金融数据 R² 通常 < 5%, 特征工程 > 模型选择
- López de Prado (2018) — Purged K-Fold 时序校验
- Burges (2010) — LambdaMART / LambdaRank 排名学习

与现有架构关系：
- 特征工程复用 cb_factors + models/feature_eng.py
- 推理结果对接 CBRotationStrategy._score_candidates()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class CBRankerConfig:
    """CB LightGBM Ranker 配置。

    Attributes:
        feature_cols: 特征列名
        label_col: 标签列名
        group_col: 分组列（截面日期）
        n_estimators: 树数量
        learning_rate: 学习率
        max_depth: 最大深度
        num_leaves: 叶子数
        min_child_samples: 叶子最小样本数
        subsample: 行采样率
        colsample_bytree: 列采样率
        train_years: 训练窗口（年）
        test_months: 测试窗口（月）
        purge_days: 训练-测试间隔（日，防信息泄漏）
        rebalance_days: 持仓期（与策略调仓一致）
    """
    feature_cols: list[str] = field(default_factory=lambda: [
        # Price/valuation (existing)
        "close", "cb_over_rate", "cb_value", "issue_size",
        "rating_score", "ytm", "delta",
        # CB-specific factors (existing)
        "iv_bias", "gamma_convexity", "credit_spread",
        "liquidity_activity", "maturity_convexity",
        "amount", "turnover_rate", "days_listed",
        # TS-1 fundamental factors (Phase 3)
        "roe", "or_yoy", "netprofit_yoy", "ocf_to_or",
        # BSM IV factors (Phase 3 / C4)
        "iv", "iv_prem", "pricing_bias",
        # Northbound flow (Phase 3 / B)
        "hk_ratio", "hk_chg_5d",
    ])
    label_col: str = "fwd_return_rank"
    group_col: str = "trade_date"
    n_estimators: int = 200
    learning_rate: float = 0.05
    max_depth: int = 6
    num_leaves: int = 31
    min_child_samples: int = 20
    subsample: float = 0.8
    colsample_bytree: float = 0.8
    train_years: int = 3
    test_months: int = 6
    purge_days: int = 10
    rebalance_days: int = 3


# ---------------------------------------------------------------------------
# Ranker 核心类
# ---------------------------------------------------------------------------

class CBLGBRanker:
    """CB LightGBM cross-sectional ranker。

    Usage:
        >>> ranker = CBLGBRanker(config)
        >>> ranker.fit(train_df)
        >>> scores = ranker.predict(test_df)
    """

    def __init__(self, config: CBRankerConfig | None = None) -> None:
        self._config = config or CBRankerConfig()
        self._model: Any = None
        self._feature_importance: dict[str, float] = {}

    @property
    def config(self) -> CBRankerConfig:
        return self._config

    @property
    def is_fitted(self) -> bool:
        return self._model is not None

    def fit(self, df: pd.DataFrame) -> "CBLGBRanker":
        """训练 ranker。

        df 必须包含 feature_cols + label_col + group_col。
        group_col 用于构建 LambdaRank 的 query group。
        """
        import lightgbm as lgb

        cfg = self._config
        features = [c for c in cfg.feature_cols if c in df.columns]
        if not features:
            raise ValueError("No valid feature columns found in df")

        X = df[features].fillna(0.0).values
        y = df[cfg.label_col].values.astype(int) if cfg.label_col in df.columns else np.zeros(len(df), dtype=int)

        # 构建 query groups（每个截面日期的样本数）
        if cfg.group_col in df.columns:
            groups = df.groupby(cfg.group_col).size().values
        else:
            groups = np.array([len(df)])

        train_set = lgb.Dataset(X, label=y, group=groups, feature_name=features)

        params = {
            "objective": "lambdarank",
            "metric": "ndcg",
            "ndcg_eval_at": [5, 10, 20],
            "learning_rate": cfg.learning_rate,
            "max_depth": cfg.max_depth,
            "num_leaves": cfg.num_leaves,
            "min_child_samples": cfg.min_child_samples,
            "subsample": cfg.subsample,
            "colsample_bytree": cfg.colsample_bytree,
            "verbose": -1,
            "seed": 42,
        }

        self._model = lgb.train(
            params,
            train_set,
            num_boost_round=cfg.n_estimators,
        )

        # 记录特征重要性
        imp = self._model.feature_importance(importance_type="gain")
        self._feature_importance = {
            features[i]: float(imp[i]) for i in range(len(features))
        }
        logger.info("CBLGBRanker fitted: %d samples, %d features", len(df), len(features))
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        """预测排名分数。分数越高 → 排名越靠前。"""
        if self._model is None:
            raise RuntimeError("Model not fitted. Call fit() first.")
        cfg = self._config
        features = [c for c in cfg.feature_cols if c in df.columns]
        X = df[features].fillna(0.0).values
        return self._model.predict(X)

    def get_feature_importance(self) -> dict[str, float]:
        """获取特征重要性（gain）。"""
        return dict(self._feature_importance)

    def save(self, path: Path) -> None:
        """保存模型到文件。"""
        if self._model is None:
            raise RuntimeError("No model to save")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._model.save_model(str(path))
        logger.info("Model saved to %s", path)

    def load(self, path: Path) -> None:
        """加载模型。"""
        import lightgbm as lgb
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Model file not found: {path}")
        self._model = lgb.Booster(model_file=str(path))
        logger.info("Model loaded from %s", path)
