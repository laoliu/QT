"""ML 预测策略

基于机器学习模型的 A 股轮动策略。继承 ``EquityRotationStrategy``
模板，复用其预过滤/止损/调仓/缓冲带机制，仅覆盖
``_score_candidates()`` 使用 ML 模型对候选标的进行横截面排名。

工作流程
--------
1. ``_score_candidates()`` 从候选标的的 ``metadata`` 和价格历史中提取
   ``feature_columns`` 指定的特征，构建 pandas DataFrame。
2. 对特征执行缺失值填充（中位数）与缩尾处理（可选）。
3. 将特征矩阵传入预加载的 ML 模型（Predictor）获取预测分数。
4. 按分数降序排名，返回 top_k 个 symbol。

模型加载
--------
模型通过 ``model_path`` 配置指定，格式为带 SHA-256 校验的
序列化文件（``{"model": estimator, "feature_names": list[str]}``），
与 ``aipha.models.predictor.Predictor`` 兼容。加载时自动进行
哈希校验（若 ``.sha256`` 伴随文件存在）。若 ``model_path``
为空或文件不存在，策略自动回退为等权多因子线性打分
（使用 ``fallback_weights`` 配置）。

配置示例（configs/backtest/ml_predict.yml）::

    strategy:
      name: ml_predict
      params:
        model_path: "models/lgb_v1.pkl"
        feature_columns:
          - pe_ttm
          - pb
          - roe
          - circ_mv
          - turnover_rate
          - momentum_20d
          - volatility_20d
        top_k: 30
        rebalance_days: 5
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_volatility_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)

# Compatibility marker: this configurable strategy remains available for
# historical configurations, but canonical A-share research must use the
# versioned ``ml_ranking`` pipeline and its PIT snapshot contract.
CANONICAL_ENTRYPOINT = "ml_ranking"
LIFECYCLE = "compatibility"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 默认基础特征列表（从 bar.metadata 直接取）
_DEFAULT_META_FEATURES = [
    "pe_ttm",
    "pb",
    "roe",
    "circ_mv",
    "turnover_rate",
    "dt_netprofit_yoy",
]

# 需要从价格历史计算的衍生特征
_DERIVED_FEATURES = {"momentum_20d", "volatility_20d"}

# 回退等权权重（特征越大 → 分数越高的正向权重）
_DEFAULT_FALLBACK_WEIGHTS: dict[str, float] = {
    "pe_ttm": -1.0,       # 低PE更好
    "pb": -1.0,            # 低PB更好
    "roe": 1.0,            # 高ROE更好
    "circ_mv": -0.5,       # 偏小市值
    "turnover_rate": -0.3, # 低换手偏好
    "dt_netprofit_yoy": 0.5,  # 高增长
    "momentum_20d": 0.5,   # 正向动量
    "volatility_20d": -0.5,  # 低波动
}


@dataclass
class MLStrategyConfig(EquityRotationConfig):
    """ML 预测策略配置。"""

    # --- ML 模型 ---
    model_path: str = ""
    feature_columns: list[str] = field(
        default_factory=lambda: list(_DEFAULT_META_FEATURES)
    )
    prediction_threshold: float = float("-inf")  # 分数下限阈值

    # --- 衍生特征 ---
    momentum_lookback: int = 20
    volatility_lookback: int = 20

    # --- 回退权重（无模型时使用线性打分）---
    fallback_weights: dict[str, float] = field(
        default_factory=lambda: dict(_DEFAULT_FALLBACK_WEIGHTS)
    )

    # --- 特征预处理 ---
    winsorize: bool = True
    winsorize_lower: float = 0.02
    winsorize_upper: float = 0.98


# ---------------------------------------------------------------------------
# 策略实现
# ---------------------------------------------------------------------------

@StrategyRegistry().register("ml_predict")
class MLPredictStrategy(EquityRotationStrategy):
    """ML 预测策略：使用机器学习模型进行横截面选股。

    若模型加载失败则自动回退为线性因子打分。
    """

    name = "ml_predict"
    lifecycle = LIFECYCLE
    canonical_entrypoint = CANONICAL_ENTRYPOINT

    def __init__(self, config: MLStrategyConfig | None = None, **kwargs: Any) -> None:
        super().__init__()
        if config is not None:
            self._config = config
        else:
            # 过滤掉 MLStrategyConfig 不支持的 kwargs（如 target_symbols）
            valid_fields = {f.name for f in MLStrategyConfig.__dataclass_fields__.values()}
            filtered = {k: v for k, v in kwargs.items() if k in valid_fields}
            self._config = MLStrategyConfig(**filtered)
        self._model: Any | None = None
        self._model_feature_names: list[str] | None = None
        self._model_loaded = False
        self._load_model()

    @property
    def config(self) -> MLStrategyConfig:
        """策略配置。"""
        return self._config

    # -- 模型加载 --

    def reload_model(self, model_dir: str) -> bool:
        """公开接口：动态切换模型目录并重新加载。

        Args:
            model_dir: 新模型目录路径

        Returns:
            True 加载成功，False 失败（回退到线性打分）
        """
        old_path = self._config.model_path
        self._config.model_path = model_dir
        self._model = None
        self._model_loaded = False
        self._model_feature_names = None
        self._load_model()
        if self._model_loaded:
            logger.info(
                "MLPredictStrategy: 模型热切换成功 %s → %s",
                old_path, model_dir,
            )
            return True
        logger.warning(
            "MLPredictStrategy: 模型热切换失败 %s → %s，回退线性打分",
            old_path, model_dir,
        )
        return False

    def _load_model(self) -> None:
        """尝试加载 ML 模型。失败则回退线性打分。"""
        path = self._config.model_path
        if not path:
            logger.info("MLPredictStrategy: model_path 为空，使用线性回退打分")
            return

        try:
            from pathlib import Path

            from aipha.models.predictor import Predictor, PredictorConfig

            model_file = Path(path)
            predictor_cfg = PredictorConfig(model_path=model_file)
            predictor = Predictor(predictor_cfg)
            if predictor.load(model_file):
                self._model = predictor
                self._model_feature_names = predictor.get_feature_names()
                self._model_loaded = True
                logger.info(
                    "MLPredictStrategy: 模型加载成功 path=%s features=%d",
                    path, len(self._model_feature_names or []),
                )
            else:
                logger.warning("MLPredictStrategy: 模型加载失败 path=%s，回退线性打分", path)
        except Exception as e:
            logger.warning("MLPredictStrategy: 模型加载异常 %s，回退线性打分", e)

    # -- 核心排名 --

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """使用 ML 模型或线性回退对候选标的打分排名。"""
        if not candidates:
            return []

        # 构建特征矩阵
        feature_df = self._build_features(candidates)

        if self._model_loaded and self._model is not None:
            scored = self._predict_with_model(feature_df, candidates)
        else:
            scored = self._linear_fallback(feature_df, candidates)

        # 过滤低分
        threshold = self._config.prediction_threshold
        if threshold > float("-inf"):
            scored = {s: v for s, v in scored.items() if v >= threshold}

        # 按分数降序排列
        ranked = sorted(scored, key=lambda s: scored[s], reverse=True)
        return ranked

    # -- 特征构建 --

    def _build_features(
        self, candidates: list[EquityCandidateInfo]
    ) -> pd.DataFrame:
        """从候选标的 metadata + 价格历史构建特征 DataFrame。

        动量/波动率衍生特征通过 equity_factors 统一模块批量计算，
        避免重复实现。
        """
        cfg = self._config
        records: list[dict[str, Any]] = []

        # 批量预计算衍生特征（委托 equity_factors 统一模块）
        precomputed_mom: dict[str, float] = {}
        precomputed_vol: dict[str, float] = {}
        if "momentum_20d" in cfg.feature_columns:
            precomputed_mom = compute_momentum_factor(
                self._price_history, candidates, lookback=cfg.momentum_lookback,
            )
        if "volatility_20d" in cfg.feature_columns:
            # compute_volatility_factor 返回负值（低波=高分）；ML 需要原始值
            raw_vol = compute_volatility_factor(
                self._price_history, candidates, lookback=cfg.volatility_lookback,
            )
            precomputed_vol = {
                k: (-v if math.isfinite(v) else np.nan)
                for k, v in raw_vol.items()
            }

        for c in candidates:
            row: dict[str, Any] = {"symbol": c.symbol}

            # 从 metadata 提取基础特征
            for feat in cfg.feature_columns:
                if feat in _DERIVED_FEATURES:
                    continue  # 衍生特征单独处理
                val = c.metadata.get(feat, np.nan)
                if val is None:
                    val = np.nan
                else:
                    try:
                        val = float(val)
                        if math.isnan(val) or math.isinf(val):
                            val = np.nan
                    except (ValueError, TypeError):
                        val = np.nan
                row[feat] = val

            # 衍生特征：momentum（统一模块预计算）
            if "momentum_20d" in cfg.feature_columns:
                val = precomputed_mom.get(c.symbol, np.nan)
                row["momentum_20d"] = val if math.isfinite(val) else np.nan

            # 衍生特征：volatility（统一模块预计算）
            if "volatility_20d" in cfg.feature_columns:
                row["volatility_20d"] = precomputed_vol.get(c.symbol, np.nan)

            records.append(row)

        df = pd.DataFrame(records).set_index("symbol")

        # 缺失值填充（中位数）
        for col in df.columns:
            median_val = df[col].median()
            if pd.isna(median_val):
                median_val = 0.0
            df[col] = df[col].fillna(median_val)

        # 缩尾
        if cfg.winsorize:
            for col in df.columns:
                lo = df[col].quantile(cfg.winsorize_lower)
                hi = df[col].quantile(cfg.winsorize_upper)
                df[col] = df[col].clip(lo, hi)

        return df

    # -- ML 模型预测 --

    def _predict_with_model(
        self,
        feature_df: pd.DataFrame,
        candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """使用已加载的 ML 模型预测并返回分数。"""
        symbols = feature_df.index.tolist()
        try:
            result = self._model.predict(feature_df, symbols)
            return dict(zip(symbols, result.predictions.tolist()))
        except Exception as e:
            logger.warning("ML 预测失败(%s)，回退线性打分", e)
            return self._linear_fallback(feature_df, candidates)

    # -- 线性回退打分 --

    def _linear_fallback(
        self,
        feature_df: pd.DataFrame,
        candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """无模型时使用加权线性打分。

        每个特征先做排名标准化(0‒1)，再乘以 fallback_weights 累加。
        """
        weights = self._config.fallback_weights
        scores: dict[str, float] = {s: 0.0 for s in feature_df.index}

        for col in feature_df.columns:
            w = weights.get(col, 0.0)
            if w == 0.0:
                continue
            series = feature_df[col]
            n = len(series)
            if n <= 1:
                continue
            # 排名标准化 → [0, 1]
            ranked = series.rank(method="average", ascending=True)
            normalized = (ranked - 1) / max(n - 1, 1)  # 0..1
            for sym in feature_df.index:
                scores[sym] += w * normalized.loc[sym]

        return scores
