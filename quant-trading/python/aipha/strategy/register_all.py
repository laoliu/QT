"""批量注册所有 aipha A股策略到 StrategyRegistry

导入本模块即可完成注册。
"""
from aipha.strategy.registry import StrategyRegistry

_reg = StrategyRegistry()

# 12 个 A 股多因子轮动策略
from aipha.strategy.dividend_quality import DividendQualityStrategy
from aipha.strategy.earnings_momentum import EarningsMomentumStrategy
from aipha.strategy.garp_select import GARPSelectStrategy
from aipha.strategy.value_quality import ValueQualityStrategy
from aipha.strategy.small_cap_quality import SmallCapQualityStrategy
from aipha.strategy.pead_event import PEADStrategy
from aipha.strategy.northbound_enhanced import NorthboundEnhancedStrategy
from aipha.strategy.sector_rotation import SectorRotationStrategy
from aipha.strategy.vol_managed_value import VolManagedValueStrategy
from aipha.strategy.momentum_acceleration import MomentumAccelerationStrategy
from aipha.strategy.turnaround_play import TurnaroundPlayStrategy
from aipha.strategy.stable_compounder import StableCompounderStrategy

_reg.register_class("dividend_quality", DividendQualityStrategy)
_reg.register_class("earnings_momentum", EarningsMomentumStrategy)
_reg.register_class("garp_select", GARPSelectStrategy)
_reg.register_class("value_quality", ValueQualityStrategy)
_reg.register_class("small_cap_quality", SmallCapQualityStrategy)
_reg.register_class("pead_event", PEADStrategy)
_reg.register_class("northbound_enhanced", NorthboundEnhancedStrategy)
_reg.register_class("sector_rotation", SectorRotationStrategy)
_reg.register_class("vol_managed_value", VolManagedValueStrategy)
_reg.register_class("momentum_acceleration", MomentumAccelerationStrategy)
_reg.register_class("turnaround_play", TurnaroundPlayStrategy)
_reg.register_class("stable_compounder", StableCompounderStrategy)

# 指数增强
from aipha.strategy.index_enhance import IndexEnhanceStrategy
_reg.register_class("index_enhance", IndexEnhanceStrategy)

# 补充策略
from aipha.strategy.buyback_signal import BuybackSignalStrategy
from aipha.strategy.insider_buying import InsiderBuyingStrategy
from aipha.strategy.high_delivery_expect import HighDeliveryExpectStrategy
_reg.register_class("buyback_signal", BuybackSignalStrategy)
_reg.register_class("insider_buying", InsiderBuyingStrategy)
_reg.register_class("high_delivery_expect", HighDeliveryExpectStrategy)

# ── 迁移批次: A 股多因子 / 增强 ──────────────────────────────
from aipha.strategy.a_stock_enhanced_v2 import AStockEnhancedV2Strategy
from aipha.strategy.a_stock_multifactor import AStockMultiFactorStrategy
from aipha.strategy.adaptive_multifactor import AdaptiveMultiFactorStrategy
from aipha.strategy.alt_factor_strategy import AltFactorStrategy
from aipha.strategy.balanced_multifactor import BalancedMultiFactorStrategy
from aipha.strategy.breadth_controlled import BreadthControlledStrategy
from aipha.strategy.bucket_multifactor import BucketMultiFactorStrategy
from aipha.strategy.cash_cow_select import CashCowSelectStrategy
from aipha.strategy.composite_rank_equal import CompositeRankEqualStrategy
from aipha.strategy.comprehensive_alpha import ComprehensiveAlphaStrategy
from aipha.strategy.defensive_value import DefensiveValueStrategy
from aipha.strategy.dividend_defensive import DividendDefensiveStrategy
from aipha.strategy.dividend_quality_select import DividendQualitySelectStrategy
from aipha.strategy.earnings_surprise_alpha import EarningsSurpriseAlphaStrategy
from aipha.strategy.etf_rotation import ETFRotationStrategy
from aipha.strategy.fscore_value_enhanced import FScoreValueEnhancedStrategy
from aipha.strategy.ic_adaptive_multifactor import ICAdaptiveMultifactorStrategy
from aipha.strategy.industry_ic_tilt import IndustryIcTiltStrategy
from aipha.strategy.low_vol_value import LowVolValueStrategy
from aipha.strategy.max_drawdown_guard import MaxDrawdownGuardStrategy
from aipha.strategy.mean_reversion import MeanReversionStrategy
from aipha.strategy.micro_cap_value import MicroCapValueStrategy
from aipha.strategy.ml_factor_strategy import MLFactorStrategy
from aipha.strategy.ml_predict import MLPredictStrategy
from aipha.strategy.momentum import MomentumStrategy
from aipha.strategy.multi_period_ensemble import MultiPeriodEnsembleStrategy
from aipha.strategy.multifactor_enhance import MultiFactorEnhanceStrategy
from aipha.strategy.pead_drift import PeadDriftStrategy
from aipha.strategy.profitability_composite import ProfitabilityCompositeStrategy
from aipha.strategy.quality_growth import QualityGrowthStrategy
from aipha.strategy.quality_momentum_combo import QualityMomentumComboStrategy
from aipha.strategy.reference_five_factor import ReferenceFiveFactorStrategy
from aipha.strategy.regime_switch import RegimeSwitchStrategy
from aipha.strategy.sector_momentum import SectorMomentumStrategy
from aipha.strategy.sector_momentum_plus import SectorMomentumPlusStrategy
from aipha.strategy.size_style_rotation import SizeStyleRotationStrategy
from aipha.strategy.small_size import SmallSizeStrategy, SmallSizeEnsembleStrategy
from aipha.strategy.small_size_enhanced import SmallSizeEnhancedStrategy
from aipha.strategy.smallcap_rotation import SmallCapRotationStrategy
from aipha.strategy.st_removal_play import StRemovalPlayStrategy
from aipha.strategy.trend_quality_managed import TrendQualityManagedStrategy
from aipha.strategy.v19_hgro import V19HgroStrategy
from aipha.strategy.v22_bmf import V22BmfStrategy
from aipha.strategy.v23_sbal import V23SbalStrategy
from aipha.strategy.value_growth_switch import ValueGrowthSwitchStrategy
from aipha.strategy.vol_breakout import VolBreakoutStrategy
from aipha.strategy.volume_price_breakout import VolumePriceBreakoutStrategy
from aipha.strategy.dual_ma import DualMAStrategy

_reg.register_class("a_stock_enhanced_v2", AStockEnhancedV2Strategy)
_reg.register_class("a_stock_multifactor", AStockMultiFactorStrategy)
_reg.register_class("adaptive_multifactor", AdaptiveMultiFactorStrategy)
_reg.register_class("alt_factor_strategy", AltFactorStrategy)
_reg.register_class("balanced_multifactor", BalancedMultiFactorStrategy)
_reg.register_class("breadth_controlled", BreadthControlledStrategy)
_reg.register_class("bucket_multifactor", BucketMultiFactorStrategy)
_reg.register_class("cash_cow_select", CashCowSelectStrategy)
_reg.register_class("composite_rank_equal", CompositeRankEqualStrategy)
_reg.register_class("comprehensive_alpha", ComprehensiveAlphaStrategy)
_reg.register_class("defensive_value", DefensiveValueStrategy)
_reg.register_class("dividend_defensive", DividendDefensiveStrategy)
_reg.register_class("dividend_quality_select", DividendQualitySelectStrategy)
_reg.register_class("earnings_surprise_alpha", EarningsSurpriseAlphaStrategy)
_reg.register_class("etf_rotation", ETFRotationStrategy)
_reg.register_class("fscore_value_enhanced", FScoreValueEnhancedStrategy)
_reg.register_class("ic_adaptive_multifactor", ICAdaptiveMultifactorStrategy)
_reg.register_class("industry_ic_tilt", IndustryIcTiltStrategy)
_reg.register_class("low_vol_value", LowVolValueStrategy)
_reg.register_class("max_drawdown_guard", MaxDrawdownGuardStrategy)
_reg.register_class("mean_reversion", MeanReversionStrategy)
_reg.register_class("micro_cap_value", MicroCapValueStrategy)
_reg.register_class("ml_factor_strategy", MLFactorStrategy)
_reg.register_class("ml_predict", MLPredictStrategy)
_reg.register_class("momentum", MomentumStrategy)
_reg.register_class("multi_period_ensemble", MultiPeriodEnsembleStrategy)
_reg.register_class("multifactor_enhance", MultiFactorEnhanceStrategy)
_reg.register_class("pead_drift", PeadDriftStrategy)
_reg.register_class("profitability_composite", ProfitabilityCompositeStrategy)
_reg.register_class("quality_growth", QualityGrowthStrategy)
_reg.register_class("quality_momentum_combo", QualityMomentumComboStrategy)
_reg.register_class("reference_five_factor", ReferenceFiveFactorStrategy)
_reg.register_class("regime_switch", RegimeSwitchStrategy)
_reg.register_class("sector_momentum", SectorMomentumStrategy)
_reg.register_class("sector_momentum_plus", SectorMomentumPlusStrategy)
_reg.register_class("size_style_rotation", SizeStyleRotationStrategy)
_reg.register_class("small_size", SmallSizeStrategy)
_reg.register_class("small_size_ensemble", SmallSizeEnsembleStrategy)
_reg.register_class("small_size_enhanced", SmallSizeEnhancedStrategy)
_reg.register_class("smallcap_rotation", SmallCapRotationStrategy)
_reg.register_class("st_removal_play", StRemovalPlayStrategy)
_reg.register_class("trend_quality_managed", TrendQualityManagedStrategy)
_reg.register_class("v19_hgro", V19HgroStrategy)
_reg.register_class("v22_bmf", V22BmfStrategy)
_reg.register_class("v23_sbal", V23SbalStrategy)
_reg.register_class("value_growth_switch", ValueGrowthSwitchStrategy)
_reg.register_class("vol_breakout", VolBreakoutStrategy)
_reg.register_class("volume_price_breakout", VolumePriceBreakoutStrategy)
_reg.register_class("dual_ma", DualMAStrategy)

# ── 迁移批次: 可转债策略 ──────────────────────────────────────
from aipha.strategy.cb_adaptive_factor import CBAdaptiveFactorStrategy
from aipha.strategy.cb_clause_play import CBClausePlayStrategy
from aipha.strategy.cb_comprehensive import CBComprehensiveStrategy
from aipha.strategy.cb_credit_spread import CBCreditSpreadStrategy
from aipha.strategy.cb_dl_enhanced_5f import CBDLEnhanced5FStrategy
from aipha.strategy.cb_enhanced_mf_v2 import CBEnhancedMultifactorV2Strategy
from aipha.strategy.cb_four_factor import CBFourFactorStrategy
from aipha.strategy.cb_gamma_convexity import CBGammaConvexityStrategy
from aipha.strategy.cb_heat_momentum import CBHeatMomentumStrategy
from aipha.strategy.cb_intraday_strategies import CBVWAPDeviation, CBOvernightGapDecay
from aipha.strategy.cb_ivol_value import CBIVOLValueStrategy
from aipha.strategy.cb_low_vol_reversion import CBLowVolReversionStrategy
from aipha.strategy.cb_mean_reversion import CBMeanReversionStrategy
from aipha.strategy.cb_ml_ranking import CBMLRankingStrategy
from aipha.strategy.cb_momentum import CBMomentumStrategy
from aipha.strategy.cb_multi_alpha import CBMultiAlphaStrategy
from aipha.strategy.cb_multi_factor import MultiFactorStrategy as CBMultiFactorStrategy
from aipha.strategy.cb_multi_layer import CBMultiLayerStrategy
from aipha.strategy.cb_regime_rotation import CBRegimeRotationStrategy
from aipha.strategy.cb_reversal_small import CBReversalSmallStrategy
from aipha.strategy.cb_small_size_gated import SmallSizeGatedStrategy as CBSmallSizeGatedStrategy
from aipha.strategy.cb_stock_quality import CBStockQualityStrategy
from aipha.strategy.cb_stock_quality_v2 import CBStockQualityV2Strategy
from aipha.strategy.cb_ultra import CBUltraStrategy, CBUltraNoAgeStrategy
from aipha.strategy.cb_value_momentum import CBValueMomentumStrategy
from aipha.strategy.cb_vol_squeeze import CBVolSqueezeStrategy
from aipha.strategy.double_low import DoubleLowStrategy, ConversionArbitrageStrategy
from aipha.strategy.double_low_plus import DoubleLowPlusStrategy
from aipha.strategy.double_low_variants import DoubleLowPercentileStrategy, DoubleLowRankStrategy, DoubleLowZScoreStrategy
from aipha.strategy.forced_redeem_play import ForcedRedeemPlayStrategy
from aipha.strategy.low_premium import LowPremiumStrategy
from aipha.strategy.low_price import LowPriceStrategy
from aipha.strategy.negative_premium import NegativePremiumStrategy
from aipha.strategy.new_bond import NewBondStrategy, NewBondEnsembleStrategy
from aipha.strategy.new_small import NewSmallStrategy, NewSmallEnsembleStrategy
from aipha.strategy.pseudo_ytm import PseudoYTMStrategy
from aipha.strategy.triple_low import TripleLowStrategy
from aipha.strategy.turnover_surge import TurnoverSurgeStrategy
from aipha.strategy.ytm_plus import YTMPlusStrategy

_reg.register_class("cb_adaptive_factor", CBAdaptiveFactorStrategy)
_reg.register_class("cb_clause_play", CBClausePlayStrategy)
_reg.register_class("cb_comprehensive", CBComprehensiveStrategy)
_reg.register_class("cb_credit_spread", CBCreditSpreadStrategy)
_reg.register_class("cb_dl_enhanced_5f", CBDLEnhanced5FStrategy)
_reg.register_class("cb_enhanced_mf_v2", CBEnhancedMultifactorV2Strategy)
_reg.register_class("cb_four_factor", CBFourFactorStrategy)
_reg.register_class("cb_gamma_convexity", CBGammaConvexityStrategy)
_reg.register_class("cb_heat_momentum", CBHeatMomentumStrategy)
_reg.register_class("cb_vwap_deviation", CBVWAPDeviation)
_reg.register_class("cb_overnight_gap_decay", CBOvernightGapDecay)
_reg.register_class("cb_ivol_value", CBIVOLValueStrategy)
_reg.register_class("cb_low_vol_reversion", CBLowVolReversionStrategy)
_reg.register_class("cb_mean_reversion", CBMeanReversionStrategy)
_reg.register_class("cb_ml_ranking", CBMLRankingStrategy)
_reg.register_class("cb_momentum", CBMomentumStrategy)
_reg.register_class("cb_multi_alpha", CBMultiAlphaStrategy)
_reg.register_class("cb_multi_factor", CBMultiFactorStrategy)
_reg.register_class("cb_multi_layer", CBMultiLayerStrategy)
_reg.register_class("cb_regime_rotation", CBRegimeRotationStrategy)
_reg.register_class("cb_reversal_small", CBReversalSmallStrategy)
_reg.register_class("cb_small_size_gated", CBSmallSizeGatedStrategy)
_reg.register_class("cb_stock_quality", CBStockQualityStrategy)
_reg.register_class("cb_stock_quality_v2", CBStockQualityV2Strategy)
_reg.register_class("cb_ultra", CBUltraStrategy)
_reg.register_class("cb_ultra_no_age", CBUltraNoAgeStrategy)
_reg.register_class("cb_value_momentum", CBValueMomentumStrategy)
_reg.register_class("cb_vol_squeeze", CBVolSqueezeStrategy)
_reg.register_class("double_low", DoubleLowStrategy)
_reg.register_class("conversion_arbitrage", ConversionArbitrageStrategy)
_reg.register_class("double_low_plus", DoubleLowPlusStrategy)
_reg.register_class("double_low_percentile", DoubleLowPercentileStrategy)
_reg.register_class("double_low_rank", DoubleLowRankStrategy)
_reg.register_class("double_low_zscore", DoubleLowZScoreStrategy)
_reg.register_class("forced_redeem_play", ForcedRedeemPlayStrategy)
_reg.register_class("low_premium", LowPremiumStrategy)
_reg.register_class("low_price", LowPriceStrategy)
_reg.register_class("negative_premium", NegativePremiumStrategy)
_reg.register_class("new_bond", NewBondStrategy)
_reg.register_class("new_bond_ensemble", NewBondEnsembleStrategy)
_reg.register_class("new_small", NewSmallStrategy)
_reg.register_class("new_small_ensemble", NewSmallEnsembleStrategy)
_reg.register_class("pseudo_ytm", PseudoYTMStrategy)
_reg.register_class("triple_low", TripleLowStrategy)
_reg.register_class("turnover_surge", TurnoverSurgeStrategy)
_reg.register_class("ytm_plus", YTMPlusStrategy)

# ── 迁移批次: RL / ML ────────────────────────────────────────
# rl_portfolio is a training framework, not a Strategy subclass — skip registration
