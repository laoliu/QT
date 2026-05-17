"""
Portfolio 模块

包含持仓管理、账户、T+1状态机、组合优化等核心组件。
"""

from aipha.portfolio.account import Account, TransactionType
from aipha.portfolio.position import Position, PositionSide, PositionUpdateReason
from aipha.portfolio.position_manager import PositionManager, PositionSnapshot

# 优化器
from aipha.portfolio.optimizer import (
    EqualWeightOptimizer,
    MarketCapWeightOptimizer,
    MaxSharpeOptimizer,
    MinVarianceOptimizer,
    RiskParityOptimizer,
    WeightOptimizer,
    apply_weight_constraints,
    calculate_portfolio_metrics,
)

# 协方差估计
from aipha.portfolio.covariance import (
    CovarianceEstimator,
    ExponentialCovarianceEstimator,
    SampleCovarianceEstimator,
    ShrinkageCovarianceEstimator,
    ensure_positive_definite,
)

# 凸优化器
from aipha.portfolio.convex_optimizer import (
    BlackLittermanOptimizer,
    ConvexMaxSharpeOptimizer,
    ConvexMinVarianceOptimizer,
    ConvexRiskParityOptimizer,
    MeanVarianceOptimizer,
    OptimizationConstraints,
    create_optimizer,
)

# 合规
from aipha.portfolio.compliance_engine import ComplianceGuard

__all__ = [
    # 账户与持仓
    "Account",
    "TransactionType",
    "Position",
    "PositionSide",
    "PositionUpdateReason",
    "PositionManager",
    "PositionSnapshot",
    # 基础优化器
    "WeightOptimizer",
    "EqualWeightOptimizer",
    "MarketCapWeightOptimizer",
    "RiskParityOptimizer",
    "MinVarianceOptimizer",
    "MaxSharpeOptimizer",
    "apply_weight_constraints",
    "calculate_portfolio_metrics",
    # 协方差估计
    "CovarianceEstimator",
    "SampleCovarianceEstimator",
    "ShrinkageCovarianceEstimator",
    "ExponentialCovarianceEstimator",
    "ensure_positive_definite",
    # 凸优化器
    "OptimizationConstraints",
    "ConvexMinVarianceOptimizer",
    "ConvexRiskParityOptimizer",
    "ConvexMaxSharpeOptimizer",
    "MeanVarianceOptimizer",
    "BlackLittermanOptimizer",
    "create_optimizer",
    # 合规
    "ComplianceGuard",
]
