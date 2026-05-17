"""Strategy module exports.

所有具体策略均通过 @StrategyRegistry().register() 自注册。
统一入口使用 create_strategy(name, **params) 创建策略实例。
"""

from aipha.strategy.base import (
    IntentType,
    OrderIntent,
    Strategy,
    StrategyContext,
)
from aipha.strategy.param_space import (
    ParamSpace,
    ParamSpec,
    ParamType,
    generate_grid,
    perturb_params,
)
from aipha.strategy.registry import StrategyRegistry, create_strategy

# 指数增强优化器（框架级，不含 Strategy 子类）
from aipha.strategy.index_enhance import (
    IndexEnhanceConfig,
    BarraConstraint,
    IndexEnhanceOptimizer,
)

# RL 组合环境（框架级，依赖 gymnasium 运行时按需导入）
from aipha.strategy.rl_portfolio import (
    RLPortfolioConfig,
    PortfolioEnv,
    RLTrainer,
)

__all__ = [
    # --- 基础框架 ---
    "IntentType",
    "OrderIntent",
    "Strategy",
    "StrategyContext",
    # --- 参数空间 ---
    "ParamSpace",
    "ParamSpec",
    "ParamType",
    "generate_grid",
    "perturb_params",
    # --- 注册表 ---
    "StrategyRegistry",
    "create_strategy",
    # --- 指数增强 ---
    "IndexEnhanceConfig",
    "BarraConstraint",
    "IndexEnhanceOptimizer",
    # --- RL 组合 ---
    "RLPortfolioConfig",
    "PortfolioEnv",
    "RLTrainer",
]
