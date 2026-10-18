"""
ML 模型模块 —— 特征工程、预测。

本模块提供量化策略所需的机器学习组件：

- **feature_eng**: 特征工程流水线（标准化、去极值、中性化、滚动/滞后特征、特征选择）
  - ``Neutralizer``: 已集成到 ``factors/neutralize.py``，为正式使用组件
  - 其余类（StandardScaler/RankScaler/Winsorizer 等）为 experimental，待策略集成验证
- **predictor**: 兼容预测接口（仅 ``Predictor`` 出现在默认导出）
  - ``EnsemblePredictor`` / ``OnlinePredictor`` / ``CrossSectionalPredictor``: experimental，需从具体模块显式导入

注意：trainer 模块已在 W4.2 阶段清理（零消费者，纯 prototype 死代码）。
新的低频 A 股研究使用 ``aipha.research.ResearchPipeline``，由它统一调用
LightGBM/ElasticNet 训练及版本校验；其他模型仅供显式实验和历史回放。
GRU、GAT、TRA、MASTER、HIST 和 Ensemble 的实现位于
``aipha.experiments.models``；旧子模块仅保留导入及 pickle 名称兼容别名，
本包不会主动导入这些实验实现或其可选依赖。
历史独立 ``EquityLGBMRanker`` 也已移至实验目录；旧包级名称通过惰性
属性保留显式导入兼容，不进入默认导出，构造与加载须显式确认历史模式。
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

def __getattr__(name: str):
    # Keep explicit historical package imports/pickle lookups readable without
    # loading the standalone trainer during default discovery or star imports.
    if name in {"EquityRankerConfig", "EquityLGBMRanker", "EquityRankerWFValidator"}:
        from importlib import import_module
        import warnings

        warnings.warn(
            f"aipha.models.{name} is a legacy standalone API; use "
            "aipha.research.ResearchPipeline for canonical research",
            DeprecationWarning, stacklevel=2,
        )
        return getattr(import_module("aipha.models.lgbm_ranker"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

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
]
