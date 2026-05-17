"""
因子模块

提供因子计算框架和常用因子实现：
- Factor: 因子抽象基类
- 价值因子：PE, PB, PS, 估值历史百分位
- 质量因子：ROE, ROA, 毛利率, 净利率, 现金流质量
- 高阶质量因子：Piotroski F-Score, 应计盈余, 负债质量, 盈利稳定性
- 股息因子：股息率, 股息增长
- 成长因子：营收增长, 盈利增长, 营收加速度, SUE
- 动量因子：收益率动量、残差动量
- 规模因子：流通市值、换手率
- 波动因子：波动率
- 流动性因子：Amihud、换手率动量、量比
- 复合因子：多因子加权组合
- 标准化：Z-Score, Rank, MinMax, Winsorize, MAD
- 可转债因子：下修接近度
- 分析师因子：一致预期、盈利修正（接口预留）
- 因子分析：IC/IR、衰减、分位数收益
- 因子注册表：配置驱动的因子创建
"""

from aipha.factors.base import Factor, FactorData, FactorResult
from aipha.factors.composite import (
    CompositeFactor,
    FactorNormalizer,
    MADNormalizer,
    MinMaxNormalizer,
    RankNormalizer,
    WinsorizeNormalizer,
    ZScoreNormalizer,
    get_normalizer,
)

# 价值因子
from aipha.factors.value import PBFactor, PEFactor, PSFactor, ValuationPercentileFactor

# 质量因子
from aipha.factors.quality import (
    GrossMarginFactor,
    NetMarginFactor,
    OperatingCashFlowQualityFactor,
    ROAFactor,
    ROEFactor,
)

# 高阶质量因子
from aipha.factors.quality_advanced import (
    AccrualsQualityFactor,
    DebtQualityFactor,
    EarningsStabilityFactor,
    PiotroskiFScoreFactor,
)

# 股息因子
from aipha.factors.dividend import DividendGrowthFactor, DividendYieldFactor

# 分析师因子（接口预留）
from aipha.factors.analyst import AnalystConsensusFactor, EarningsRevisionFactor

# 成长因子
from aipha.factors.growth import (
    EarningsGrowthFactor,
    RevenueAccelerationFactor,
    RevenueGrowthFactor,
    SUEFactor,
)

# 动量因子
from aipha.factors.momentum import ReturnMomentumFactor
from aipha.factors.residual_momentum import ResidualMomentumFactor

# 规模因子
from aipha.factors.size import MarketCapFactor, TurnoverRateFactor

# 波动因子
from aipha.factors.volatility import VolatilityFactor

# 流动性因子
from aipha.factors.liquidity import (
    AmihudIlliquidityFactor,
    TurnoverMomentumFactor,
    VolumeRatioFactor,
)

# 可转债因子
from aipha.factors.putback_clause import PutbackProximityFactor, putback_optionality_score
from aipha.factors.cb_delta_zone import (
    DeltaZone,
    compute_delta_proxy,
    classify_delta_zone,
    compute_gamma_proxy,
    compute_premium_rate,
)
from aipha.factors.maturity_catalyst import (
    MaturityZone,
    MaturityCatalystThresholds,
    classify_maturity_zone,
    compute_maturity_catalyst,
    compute_maturity_urgency,
    days_to_maturity_from_str,
)
from aipha.factors.enhanced_putback import (
    compute_putback_from_cb,
    compute_downward_revision_probability,
    compute_clause_play_score,
)

# 因子分析
from aipha.factors.analysis import (
    FactorAnalyzer,
    FactorDecayResult,
    ICAnalysisResult,
    QuantileReturnResult,
)

# 因子深度分析器（factor_analyzer 模块，与 analysis.FactorAnalyzer 区分）
from aipha.factors.factor_analyzer import FactorReport
from aipha.factors.factor_analyzer import FactorAnalyzer as DetailedFactorAnalyzer

# ESG 因子
from aipha.factors.esg_factors import (
    ESGConfig,
    GovernanceFactor,
    ControversyFactor,
    ESGCompositeFactor,
)

# 自动因子挖掘（遗传编程）
from aipha.factors.auto_factor_mining import (
    MinedFactor,
    MiningResult,
    AutoFactorMiner,
)

# LLM 因子挖掘管道
from aipha.factors.factor_expression_parser import (
    FactorExpressionParser,
    ParsedFactor,
)
from aipha.factors.ast_dedup import (
    ASTFactorDeduplicator,
    ASTSignature,
    DedupResult,
)
from aipha.factors.ic_trap_guard import (
    GuardConfig,
    GuardCheckResult,
    GuardVerdict,
    ICTrapGuard,
)
from aipha.factors.llm_factor_miner import (
    CampaignResult,
    CandidateSource,
    FactorCandidate,
    FactorKnowledgeBase,
    LLMFactorMiner,
    MiningConfig,
    ValidationResult,
)

# 因子研究报告
from aipha.factors.research_report import FactorResearchReport, FactorResearchReporter

# 因子注册表
from aipha.factors.registry import (
    FactorMeta,
    FactorRegistry,
    create_factor,
    create_factor_from_config,
    factor_categories,
    get_factor,
    get_factor_meta,
    list_factors,
    list_factors_by_category,
    register_factor,
)

# 因子相关性与换手率
from aipha.factors.correlation import (
    CorrelationMatrix,
    CorrelationPair,
    FactorTurnoverResult,
    compute_correlation_matrix,
    compute_factor_turnover,
    detect_redundant_factors,
)

# 因子中性化
from aipha.factors.neutralize import (
    NeutralizationConfig,
    neutralize_factor_panel,
    neutralize_multi_factors,
)

# A股因子计算工具函数（注意：equity_factors 因依赖 strategy 模块存在循环引用，
# 不在此处导入，使用时请直接 from aipha.factors.equity_factors import ...）

# 技术指标因子
from aipha.factors.technical import (
    TechnicalFactorCalculator,
    TechnicalIndicatorResult,
    TrendDirection,
)

# 量化因子运算符
from aipha.factors.operators import (
    cs_rank,
    cs_scale,
    cs_mean,
    cs_std,
    cs_zscore,
    safe_div,
    ts_corr,
    ts_cov,
    ts_decay_linear,
    ts_delay,
    ts_delta,
    ts_max,
    ts_mean,
    ts_min,
    ts_rank,
    ts_std,
    ts_sum,
)

# Alpha158 因子库 (Qlib 标准 158 因子)
from aipha.factors.qlib_alpha import (
    Alpha158,
    Alpha158Config,
    compute_alpha158,
    get_alpha158_factor_names,
)

# Alpha101 因子库 (WorldQuant 101 因子)
from aipha.factors.alpha101 import (
    Alpha101,
    compute_alpha101,
    get_alpha101_factor_names,
    SKIPPED_ALPHAS as ALPHA101_SKIPPED,
)

# 可转债专属因子 (Phase 1: 5 截面因子 + 3 时序因子)
from aipha.factors.cb_factors import (
    CBFactorValues,
    FACTOR_META as CB_FACTOR_META,
    compute_all_factors as compute_cb_factors,
    compute_batch_factors as compute_cb_batch_factors,
    compute_credit_spread,
    compute_credit_spread_mom,
    compute_delta_accel,
    compute_gamma_convexity,
    compute_iv_bias,
    compute_liquidity_activity,
    compute_liquidity_shock,
    compute_maturity_convexity,
)

__all__ = [
    # Base
    "Factor",
    "FactorData",
    "FactorResult",
    # Normalizers
    "FactorNormalizer",
    "ZScoreNormalizer",
    "RankNormalizer",
    "MinMaxNormalizer",
    "WinsorizeNormalizer",
    "MADNormalizer",
    "get_normalizer",
    # Composite
    "CompositeFactor",
    # Value
    "PEFactor",
    "PBFactor",
    "PSFactor",
    "ValuationPercentileFactor",
    # Quality
    "ROEFactor",
    "ROAFactor",
    "GrossMarginFactor",
    "NetMarginFactor",
    "OperatingCashFlowQualityFactor",
    # Quality Advanced
    "PiotroskiFScoreFactor",
    "AccrualsQualityFactor",
    "DebtQualityFactor",
    "EarningsStabilityFactor",
    # Dividend
    "DividendYieldFactor",
    "DividendGrowthFactor",
    # Analyst (placeholder)
    "AnalystConsensusFactor",
    "EarningsRevisionFactor",
    # Growth
    "RevenueGrowthFactor",
    "EarningsGrowthFactor",
    "RevenueAccelerationFactor",
    "SUEFactor",
    # Momentum
    "ReturnMomentumFactor",
    "ResidualMomentumFactor",
    # Size
    "MarketCapFactor",
    "TurnoverRateFactor",
    # Volatility
    "VolatilityFactor",
    # Liquidity
    "AmihudIlliquidityFactor",
    "TurnoverMomentumFactor",
    "VolumeRatioFactor",
    # CB-specific
    "PutbackProximityFactor",
    "putback_optionality_score",
    # Analysis
    "FactorAnalyzer",
    "ICAnalysisResult",
    "FactorDecayResult",
    "QuantileReturnResult",
    # Detailed Factor Analyzer
    "FactorReport",
    "DetailedFactorAnalyzer",
    # ESG
    "ESGConfig",
    "GovernanceFactor",
    "ControversyFactor",
    "ESGCompositeFactor",
    # Auto Factor Mining
    "MinedFactor",
    "MiningResult",
    "AutoFactorMiner",
    # Research Report
    "FactorResearchReporter",
    "FactorResearchReport",
    # Registry
    "FactorRegistry",
    "FactorMeta",
    "register_factor",
    "get_factor",
    "get_factor_meta",
    "create_factor",
    "create_factor_from_config",
    "list_factors",
    "list_factors_by_category",
    "factor_categories",
    # Correlation & Turnover
    "CorrelationMatrix",
    "CorrelationPair",
    "FactorTurnoverResult",
    "compute_correlation_matrix",
    "compute_factor_turnover",
    "detect_redundant_factors",
    # Neutralization
    "NeutralizationConfig",
    "neutralize_factor_panel",
    "neutralize_multi_factors",
    # Equity factor utilities (use: from aipha.factors.equity_factors import ...)
    # Not importable from __init__ due to circular dependency with strategy module
    # Technical
    "TechnicalFactorCalculator",
    "TechnicalIndicatorResult",
    "TrendDirection",
    # Operators
    "cs_rank",
    "cs_scale",
    "cs_mean",
    "cs_std",
    "cs_zscore",
    "safe_div",
    "ts_corr",
    "ts_cov",
    "ts_decay_linear",
    "ts_delay",
    "ts_delta",
    "ts_max",
    "ts_mean",
    "ts_min",
    "ts_rank",
    "ts_std",
    "ts_sum",
    # Alpha158
    "Alpha158",
    "Alpha158Config",
    "compute_alpha158",
    "get_alpha158_factor_names",
    # Alpha101
    "Alpha101",
    "compute_alpha101",
    "get_alpha101_factor_names",
    "ALPHA101_SKIPPED",
    # CB 专属因子
    "CBFactorValues",
    "CB_FACTOR_META",
    "compute_cb_factors",
    "compute_cb_batch_factors",
    "compute_iv_bias",
    "compute_gamma_convexity",
    "compute_credit_spread",
    "compute_credit_spread_mom",
    "compute_delta_accel",
    "compute_liquidity_activity",
    "compute_liquidity_shock",
    "compute_maturity_convexity",
]
