"""
风控模块

提供完整的风控体系：
- 持仓/行业/回撤/换手/集中度/风格/流动性约束
- 止损规则（固定/追踪/日内亏损）
- 熔断器（CircuitBreaker）
- 市场状态检测（MarketRegimeDetector）
- 审计日志、持仓对账
- 仓位管理（波动率目标/回撤减仓）
"""

from aipha.risk.constraints import (
    CompositeRiskManager,
    ConcentrationLimit,
    DrawdownLimit,
    ForceRedeemExposureLimit,
    IndustryNeutralConstraint,
    LiquidityConstraint,
    MarketRegimeDetector,
    PositionLimit,
    RiskCheckResult,
    RiskConstraint,
    RiskViolationType,
    SectorLimit,
    StyleExposureLimit,
    TurnoverLimit,
)
from aipha.risk.stop_loss import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerState,
    DailyLossLimit,
    FixedPercentStopLoss,
    StopLossConfig,
    StopLossManager,
    StopLossResult,
    StopLossRule,
    StopLossType,
    TrailingStopLoss,
    TriggerAction,
)
from aipha.risk.var import (
    A_SHARE_STRESS_SCENARIOS,
    CornishFisherVaR,
    HistoricalVaR,
    ParametricVaR,
    StressTestResult,
    StressTestRunner,
    StressTestScenario,
    VaRMethod,
    VaRResult,
)

__all__ = [
    # constraints
    "RiskConstraint",
    "RiskCheckResult",
    "RiskViolationType",
    "PositionLimit",
    "SectorLimit",
    "DrawdownLimit",
    "TurnoverLimit",
    "ConcentrationLimit",
    "StyleExposureLimit",
    "ForceRedeemExposureLimit",
    "LiquidityConstraint",
    "IndustryNeutralConstraint",
    "CompositeRiskManager",
    "MarketRegimeDetector",
    # stop_loss
    "StopLossType",
    "TriggerAction",
    "StopLossConfig",
    "StopLossResult",
    "StopLossRule",
    "FixedPercentStopLoss",
    "TrailingStopLoss",
    "DailyLossLimit",
    "StopLossManager",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "CircuitBreakerState",
    # VaR / CVaR / 压力测试
    "VaRMethod",
    "VaRResult",
    "HistoricalVaR",
    "ParametricVaR",
    "CornishFisherVaR",
    "StressTestScenario",
    "StressTestResult",
    "StressTestRunner",
    "A_SHARE_STRESS_SCENARIOS",
]
