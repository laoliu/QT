"""
ML 模型模块 —— 特征工程、预测。

本模块提供量化策略所需的机器学习组件：

- **feature_eng**: 特征工程流水线（标准化、去极值、中性化、滚动/滞后特征、特征选择）
  - ``Neutralizer``: 已集成到 ``factors/neutralize.py``，为正式使用组件
  - 其余类（StandardScaler/RankScaler/Winsorizer 等）为 experimental，待策略集成验证
- **predictor**: 统一预测接口（单模型预测）
  - ``Predictor`` / ``PredictorConfig``: 由 ``strategy/ml_predict.py`` 条件引用
  - ``EnsemblePredictor`` / ``OnlinePredictor`` / ``CrossSectionalPredictor``: experimental

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
    CrossSectionalPredictor,
    EnsemblePredictor,
    OnlinePredictor,
    Predictor,
    PredictorConfig,
    PredictionResult,
)

# ---------- ml_stock_selector ----------
from aipha.models.ml_stock_selector import (
    MLSelectorConfig,
    MLStockSelector,
)

# ---------- double_ensemble ----------
from aipha.models.double_ensemble import (
    DEnsembleConfig,
    DoubleEnsembleModel,
)

# ---------- gat_ranker (optional: requires torch + torch_geometric) ----------
try:
    from aipha.models.gat_ranker import (
        GATRankerConfig,
        GATFactorRanker,
        GATNetwork,
        IndustryGraphBuilder,
    )

    _GAT_AVAILABLE = True
except ImportError:
    _GAT_AVAILABLE = False

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

# ---------- ts_foundation_eval (optional: requires transformers) ----------
try:
    from aipha.models.ts_foundation_eval import (
        TSFoundationEvaluator,
        TSModelAdapter,
        EvaluationReport,
    )

    _TS_FOUNDATION_AVAILABLE = True
except ImportError:
    _TS_FOUNDATION_AVAILABLE = False

__all__ = [
    # feature_eng (Neutralizer = production, rest = experimental)
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
    # predictor (Predictor/PredictorConfig = conditional, rest = experimental)
    "PredictionResult",
    "PredictorConfig",
    "Predictor",
    "EnsemblePredictor",
    "OnlinePredictor",
    "CrossSectionalPredictor",
    # ml_stock_selector
    "MLSelectorConfig",
    "MLStockSelector",
    # double_ensemble
    "DEnsembleConfig",
    "DoubleEnsembleModel",
    # gat_ranker (optional)
    "GATRankerConfig",
    "GATFactorRanker",
    "GATNetwork",
    "IndustryGraphBuilder",
    # lgbm_ranker (optional)
    "EquityRankerConfig",
    "EquityLGBMRanker",
    "EquityRankerWFValidator",
    # ts_foundation_eval (optional)
    "TSFoundationEvaluator",
    "TSModelAdapter",
    "EvaluationReport",
]
