"""
ML 模型模块 —— 特征工程、预测。

本模块提供量化策略所需的机器学习组件：

- **feature_eng**: 特征工程流水线（标准化、去极值、中性化、滚动/滞后特征、特征选择）
  - ``Neutralizer``: 已集成到 ``factors/neutralize.py``，为正式使用组件
  - 其余类（StandardScaler/RankScaler/Winsorizer 等）为 experimental，待策略集成验证
- **predictor**: 兼容预测接口（仅 ``Predictor`` 出现在默认导出）
  - ``EnsemblePredictor`` / ``OnlinePredictor`` / ``CrossSectionalPredictor``: experimental，需从具体模块显式导入

注意：trainer 模块已在 W4.2 阶段清理（零消费者，纯 prototype 死代码）。
若未来需要模型训练能力，应基于 Qlib ModelCreator 或 sklearn Pipeline 重新设计。
"""

# ---------- feature_eng ----------
from aipha.models.feature_eng import (
    FeatureConfig,
    FeaturePipeline,
    FeatureSelector,
    FeatureTransformer,
    LagFeatureGenerator,
    MissingValueHandler,
    Neutralizer,
    RankScaler,
    RollingFeatureGenerator,
    ScalerType,
    StandardScaler,
    Winsorizer,
)

# ---------- predictor ----------
from aipha.models.predictor import (
    Predictor,
    PredictorConfig,
    PredictionResult,
)

# ---------- ml_stock_selector ----------
from aipha.models.ml_stock_selector import (
    MLSelectorConfig,
    MLStockSelector,
)

# ---------- lgbm_ranker (optional: requires lightgbm) ----------
try:
    from aipha.models.lgbm_ranker import (
        EquityRankerConfig,
        EquityLGBMRanker,
        EquityRankerWFValidator,
    )

    _LGBM_RANKER_AVAILABLE = True
except ImportError:
    _LGBM_RANKER_AVAILABLE = False

__all__ = [
    # feature_eng (used by the canonical research pipeline)
    "ScalerType",
    "FeatureConfig",
    "FeatureTransformer",
    "StandardScaler",
    "RankScaler",
    "Winsorizer",
    "MissingValueHandler",
    "Neutralizer",
    "LagFeatureGenerator",
    "RollingFeatureGenerator",
    "FeaturePipeline",
    "FeatureSelector",
    # predictor compatibility surface
    "PredictionResult",
    "PredictorConfig",
    "Predictor",
    # ml_stock_selector
    "MLSelectorConfig",
    "MLStockSelector",
    # lgbm_ranker (optional)
    "EquityRankerConfig",
    "EquityLGBMRanker",
    "EquityRankerWFValidator",
]
