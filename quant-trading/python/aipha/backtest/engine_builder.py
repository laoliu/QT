"""Engine component factory functions for configuration-driven backtesting.

This module provides factory functions that construct engine components
(strategy, fill model, slippage, fee model, market rules, risk manager,
stop loss, audit logger, etc.) from BacktestRunConfig.

Extracted from config_runner.py to reduce God Module complexity.
All functions accept BacktestRunConfig and return configured component instances.
"""
from __future__ import annotations

import logging
from pathlib import Path

from aipha.backtest.config_models import BacktestRunConfig, CostConfig
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TimeVaryingStampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import get_fill_model
from aipha.execution.slippage import (
    FixedBpsSlippageModel,
    LinearImpactSlippageModel,
    SlippageModel,
    SquareRootImpactSlippageModel,
    TieredImpactSlippageModel,
    # 回测诚实性护栏：滑点配得低到不真实时告警。实现留在 slippage.py
    # （建模层唯一落点），这里只做转出口，供按 engine_builder 引用的调用方用。
    _warn_low_slippage,
    create_slippage_model,
)
from aipha.market.base import MarketRules
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.risk.audit_log import AuditLogConfig, AuditLogger
from aipha.risk.constraints import (
    CompositeRiskManager,
    MarketRegimeDetector,
)
from aipha.risk.stop_loss import (
    CircuitBreaker,
    CircuitBreakerConfig,
    FixedPercentStopLoss,
    StopLossConfig,
    StopLossManager,
    StopLossType,
    TrailingStopLoss,
    TriggerAction,
)
from aipha.universe.exclusions import (
    DelistedExclusionRule,
    STExclusionRule,
    SuspendedExclusionRule,
    load_manual_exclusion_from_config,
)
from aipha.universe.policy import CompositeUniversePolicy

logger = logging.getLogger(__name__)


_SLIPPAGE_MODELS: dict[str, type] = {
    "fixed": FixedBpsSlippageModel,
    "linear_impact": LinearImpactSlippageModel,
    "square_root_impact": SquareRootImpactSlippageModel,
    "tiered_impact": TieredImpactSlippageModel,
}

# ============================================================================
# 策略注册与创建
# ============================================================================

# 策略模块列表 —— 导入即触发 @StrategyRegistry().register() 装饰器
# 包含所有 CB（可转债）策略与 A 股策略
_STRATEGY_MODULES: list[str] = [
    # --- CB 策略 ---
    "aipha.strategy.triple_low",
    "aipha.strategy.double_low",
    "aipha.strategy.double_low_plus",
    "aipha.strategy.double_low_variants",
    "aipha.strategy.low_price",
    "aipha.strategy.pseudo_ytm",
    "aipha.strategy.cb_momentum",
    "aipha.strategy.low_premium",
    "aipha.strategy.negative_premium",
    "aipha.strategy.small_size",
    "aipha.strategy.small_size_enhanced",
    "aipha.strategy.cb_small_size_gated",
    "aipha.strategy.cb_mean_reversion",
    "aipha.strategy.cb_multi_factor",
    "aipha.strategy.cb_multi_alpha",
    "aipha.strategy.cb_comprehensive",
    "aipha.strategy.cb_reversal_small",
    "aipha.strategy.cb_regime_rotation",
    "aipha.strategy.cb_ultra",
    "aipha.strategy.comprehensive_alpha",
    "aipha.strategy.new_bond",
    "aipha.strategy.new_small",
    "aipha.strategy.vol_breakout",
    "aipha.strategy.defensive_value",
    "aipha.strategy.dividend_defensive",
    "aipha.strategy.ytm_plus",
    "aipha.strategy.cb_value_momentum",
    "aipha.strategy.turnover_surge",
    "aipha.strategy.forced_redeem_play",
    "aipha.strategy.factor_strategy",
    "aipha.strategy.cb_stock_quality",
    "aipha.strategy.cb_gamma_convexity",
    "aipha.strategy.cb_clause_play",
    "aipha.strategy.cb_multi_layer",
    "aipha.strategy.cb_low_vol_reversion",
    "aipha.strategy.cb_vol_squeeze",
    "aipha.strategy.cb_dl_enhanced_5f",
    "aipha.strategy.cb_heat_momentum",
    "aipha.strategy.cb_ivol_value",
    "aipha.strategy.cb_credit_spread",
    "aipha.strategy.cb_pricing_bias",
    "aipha.strategy.cb_stock_quality_v2",
    "aipha.strategy.cb_stock_quality_v3",
    "aipha.strategy.cb_four_factor",
    "aipha.strategy.cb_enhanced_mf_v2",
    "aipha.strategy.cb_ml_ranking",
    "aipha.strategy.cb_adaptive_factor",
    "aipha.strategy.cb_gp_mined",
    "aipha.strategy.cb_iv_quality",
    "aipha.strategy.cb_iv_strategy",
    "aipha.strategy.cb_iv_event_satellite",
    # --- A 股策略 ---
    "aipha.strategy.smallcap_rotation",
    "aipha.strategy.multifactor_enhance",
    "aipha.strategy.balanced_multifactor",
    "aipha.strategy.a_stock_multifactor",       # C-1: EP/ROE/MOM_12_1/IVOL 四因子基线
    "aipha.strategy.a_stock_enhanced_v2",       # C-2: 8 因子增强 (EP/ROE/MOM/IVOL/STR/Analyst/Holder/Forecast)
    "aipha.strategy.bucket_multifactor",          # R71: 3-bucket (large/mid/small) 多因子
    "aipha.strategy.pead_event",
    "aipha.strategy.momentum",
    "aipha.strategy.sector_momentum",
    "aipha.strategy.dual_ma",
    "aipha.strategy.mean_reversion",
    "aipha.strategy.earnings_momentum",
    "aipha.strategy.value_quality",
    "aipha.strategy.quality_growth",
    "aipha.strategy.dividend_quality",
    "aipha.strategy.ml_predict",
    "aipha.strategy.ml_factor_strategy",
    "aipha.strategy.multi_period_ensemble",
    "aipha.strategy.adaptive_multifactor",
    "aipha.strategy.micro_cap_value",
    "aipha.strategy.reference_five_factor",
    "aipha.strategy.v19_hgro",
    "aipha.strategy.v22_bmf",
    "aipha.strategy.v23_sbal",
    "aipha.strategy.three_sleeve_equity",
    "aipha.strategy.behavioral_sleeve",
    "aipha.strategy.csi500_quality_sleeve",
    "aipha.strategy.overreaction",
    "aipha.strategy.pair_trading_industry_neutral",
    "aipha.strategy.quality_lowvol",
    # --- 30 策略计划 (G1-G6) ---
    # G1 防御型
    "aipha.strategy.dividend_quality_select",
    "aipha.strategy.low_vol_value",
    "aipha.strategy.fscore_value_enhanced",
    "aipha.strategy.cash_cow_select",
    "aipha.strategy.stable_compounder",
    # G2 均衡型
    "aipha.strategy.quality_momentum_combo",
    "aipha.strategy.ic_adaptive_multifactor",
    "aipha.strategy.garp_select",
    "aipha.strategy.profitability_composite",
    "aipha.strategy.composite_rank_equal",
    # G3 进攻型
    "aipha.strategy.small_cap_quality",
    "aipha.strategy.momentum_acceleration",
    "aipha.strategy.earnings_surprise_alpha",
    "aipha.strategy.turnaround_play",
    "aipha.strategy.volume_price_breakout",
    # G4 择时增强型
    "aipha.strategy.trend_quality_managed",
    "aipha.strategy.vol_managed_value",
    "aipha.strategy.regime_switch",
    "aipha.strategy.breadth_controlled",
    "aipha.strategy.max_drawdown_guard",
    # G5 事件驱动型
    "aipha.strategy.pead_drift",
    "aipha.strategy.a_stock_event_pead",
    "aipha.strategy.buyback_signal",
    "aipha.strategy.st_removal_play",
    "aipha.strategy.high_delivery_expect",
    "aipha.strategy.insider_buying",
    # G6 行业风格轮动
    "aipha.strategy.sector_momentum_plus",
    "aipha.strategy.size_style_rotation",
    "aipha.strategy.value_growth_switch",
    "aipha.strategy.industry_ic_tilt",
    "aipha.strategy.northbound_enhanced",
    # --- ETF 策略 ---
    "aipha.strategy.etf_rotation",
    # --- 另类因子策略 ---
    "aipha.strategy.alt_factor_strategy",
]

_strategies_registered = False


def _ensure_strategies_registered() -> None:
    """确保所有策略模块已导入并注册到全局 StrategyRegistry。

    仅在首次调用时执行导入，后续调用为无操作。
    """
    global _strategies_registered
    if _strategies_registered:
        return

    import importlib

    for module_name in _STRATEGY_MODULES:
        try:
            importlib.import_module(module_name)
        except ImportError as e:
            logger.warning(f"无法导入策略模块 {module_name}: {e}")

    _strategies_registered = True


def _create_strategy(config: BacktestRunConfig):
    """从配置创建策略实例

    支持全局注册表中的所有策略（A-stock + CB）。

    Args:
        config: 回测运行配置

    Returns:
        Strategy 实例
    """
    _ensure_strategies_registered()

    from aipha.strategy.registry import create_strategy

    strategy_name = config.strategy.name
    params = dict(config.strategy.params)

    # benchmark / base_index 仅用于数据加载（限定成分股范围），不传给策略
    params.pop("benchmark", None)
    params.pop("base_index", None)

    # A-stock 策略可能需要 target_symbols
    if config.data.symbols and "target_symbols" not in params:
        params["target_symbols"] = config.data.symbols

    return create_strategy(strategy_name, **params)


# ============================================================================
# 滑点模型工厂
# ============================================================================

def _create_slippage_model(
    cost: CostConfig,
    opening_volume_fraction: float = 1.0,
) -> SlippageModel:
    """根据 CostConfig 创建滑点模型实例。

    支持的 slippage_model 值：
    - "fixed" (默认): FixedBpsSlippageModel(bps=slippage_bps)
    - "linear_impact": LinearImpactSlippageModel(**slippage_params)
    - "square_root_impact": SquareRootImpactSlippageModel(**slippage_params)

    Args:
        cost: 成本配置
        opening_volume_fraction: 开盘成交量占全天比例（0.0~1.0）。
            当 fill_model 为 next_bar_open 时，滑点模型中的参与率计算
            应使用开盘时段的流动性而非全天流动性。此值从
            fill_model.volume_constraint.opening_volume_fraction 传入。

    slippage_params 示例::

        # linear_impact
        slippage_params:
          base_bps: 3.0
          impact_coeff: 50.0
          fallback_bps: 10.0
          max_bps: 100.0

        # square_root_impact
        slippage_params:
          impact_coeff: 0.5
          sigma_daily: 0.02
          max_bps: 200.0
    """
    return create_slippage_model(
        cost.slippage_model,
        slippage_bps=cost.slippage_bps,
        slippage_params=cost.slippage_params,
        opening_volume_fraction=opening_volume_fraction,
    )


# ============================================================================
# 引擎组件工厂
# ============================================================================


def _create_engine_components(config: BacktestRunConfig):
    """从配置创建引擎组件（fill_model, slippage, fee_model, rules）

    所有参数均来自配置，不硬编码任何值。
    根据 data.source 自动选择 A 股或可转债的市场规则。
    如果配置中包含 volume_constraint，自动包装 VolumeConstrainedFillModel。

    Args:
        config: 回测运行配置

    Returns:
        (fill_model, slippage, fee_model, rules) 元组
    """
    from aipha.execution.fill_models import VolumeConstraint, VolumeConstrainedFillModel

    fill_model = get_fill_model(config.fill_model.name)

    # 包装成交量参与约束
    vc_config = config.fill_model.volume_constraint
    if vc_config:
        constraint = VolumeConstraint(
            max_participation_rate=vc_config.get("max_participation_rate", 0.10),
            lot_size=vc_config.get("lot_size", config.lot_size),
            opening_volume_fraction=vc_config.get("opening_volume_fraction", 0.15),
        )
        fill_model = VolumeConstrainedFillModel(
            inner_model=fill_model,
            constraint=constraint,
        )
        logger.info(
            f"启用成交量参与约束: max_rate={constraint.max_participation_rate}, "
            f"lot_size={constraint.lot_size}, "
            f"opening_vol_frac={constraint.opening_volume_fraction}"
        )
    else:
        logger.warning(
            "未配置 volume_constraint，成交不受成交量约束。"
            "生产回测建议配置 fill_model.volume_constraint.max_participation_rate"
        )

    # 提取 opening_volume_fraction 传给滑点模型，使其参与率计算与 fill_model 一致
    ovf = 0.15
    if vc_config:
        ovf = vc_config.get("opening_volume_fraction", 0.15)
    slippage = _create_slippage_model(config.cost, opening_volume_fraction=ovf)

    # P0-1 修复：仅 A 股（stamp_tax_rate > 0）启用时变印花税模型
    # CB/ETF/Bond 的 stamp_tax_rate=0.0 直接使用固定模型（免税）
    _use_tv = config.cost.stamp_tax_auto_schedule and config.cost.stamp_tax_rate > 0
    _stamp_tax_model = (
        TimeVaryingStampTaxModel(default_rate=config.cost.stamp_tax_rate)
        if _use_tv
        else StampTaxModel(sell_rate=config.cost.stamp_tax_rate)
    )

    fee_model = FeeModelCNEquity(
        commission=CommissionModel(
            rate=config.cost.commission_rate,
            min_fee=config.cost.commission_min,
        ),
        stamp_tax=_stamp_tax_model,
        transfer_fee=TransferFeeModel(
            rate=config.cost.transfer_fee_rate,
        ),
    )

    rules: MarketRules
    if config.data.source == "tushare_cb":
        from decimal import Decimal

        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

        rules = MarketRulesConvertibleBond(
            lot_size=config.lot_size,
            t_plus=config.t_plus_n,
            commission_rate=Decimal(str(config.cost.commission_rate)),
            commission_min=Decimal(str(config.cost.commission_min)),
            stamp_tax_rate=Decimal(str(config.cost.stamp_tax_rate)),
        )
        logger.info(
            f"使用可转债市场规则: lot_size={config.lot_size}, "
            f"t_plus={config.t_plus_n}"
        )
    else:
        rules = MarketRulesCNEquity(lot_size=config.lot_size)

    return fill_model, slippage, fee_model, rules


# ============================================================================
# Universe 策略构建
# ============================================================================


def _build_universe_policy(
    config: BacktestRunConfig,
) -> CompositeUniversePolicy | None:
    """从 UniverseConfig 构建 CompositeUniversePolicy。

    根据配置中的 exclude_st / exclude_suspended / exclude_list_path
    组装剔除规则链。如果没有任何规则被启用，返回 None（即不过滤）。

    Args:
        config: 回测运行配置

    Returns:
        CompositeUniversePolicy 或 None
    """
    rules = []

    if config.universe.exclude_st:
        rules.append(STExclusionRule())

    if config.universe.exclude_suspended:
        rules.append(SuspendedExclusionRule())

    # 退市/暂停上市剔除（总是启用）
    rules.append(DelistedExclusionRule())

    # 手动黑名单
    if config.universe.exclude_list_path:
        try:
            manual_rule = load_manual_exclusion_from_config(
                config.universe.exclude_list_path
            )
            rules.append(manual_rule)
            logger.info(
                f"加载手动剔除名单: {config.universe.exclude_list_path}"
            )
        except FileNotFoundError:
            logger.warning(
                f"手动剔除名单不存在: {config.universe.exclude_list_path}"
            )

    if not rules:
        return None

    return CompositeUniversePolicy(rules=rules)


# ============================================================================
# 风控管理器构建
# ============================================================================


def _build_risk_manager(
    config: BacktestRunConfig,
) -> CompositeRiskManager | None:
    """从 RiskConfig 构建 CompositeRiskManager。

    委托给 §13.15 唯一工厂 ``risk_config_loader.build_risk_manager()``。

    Args:
        config: 回测运行配置

    Returns:
        CompositeRiskManager 或 None
    """
    from aipha.risk.risk_config_loader import build_risk_manager
    return build_risk_manager(config.risk, lot_size=config.lot_size)


# ============================================================================
# §R-3: 合规引擎构建
# ============================================================================


def _build_compliance_engine(config: BacktestRunConfig):
    """从配置构建 ComplianceEngine（可选）。

    如果 risk 未启用，返回 None。
    """
    if not config.risk.enabled:
        return None
    try:
        from aipha.portfolio.compliance_engine import ComplianceEngine
        return ComplianceEngine()
    except Exception:
        logger.warning("无法构建 ComplianceEngine，合规检查将被跳过")
        return None


# ============================================================================
# 止损管理器构建
# ============================================================================


def _build_stop_loss_manager(
    config: BacktestRunConfig,
) -> StopLossManager | None:
    """从 RiskConfig 构建 StopLossManager。

    根据配置中的止损阈值创建对应的 StopLossRule 实例，
    可选地附加 CircuitBreaker。若无止损规则且无熔断，返回 None。

    支持的规则：
    - FixedPercentStopLoss: 固定百分比止损
    - TrailingStopLoss: 追踪止损
    - CircuitBreaker: 熔断器（日亏损/回撤触发）

    Args:
        config: 回测运行配置

    Returns:
        StopLossManager 或 None
    """
    if not config.risk.enabled:
        return None

    manager = StopLossManager()
    has_rules = False

    # 固定止损
    if config.risk.stop_loss_pct is not None and config.risk.stop_loss_pct > 0:
        sl_config = StopLossConfig(
            enabled=True,
            stop_type=StopLossType.FIXED_PERCENT,
            threshold=config.risk.stop_loss_pct,
            action=TriggerAction.CLOSE_POSITION,
        )
        manager.add_rule(FixedPercentStopLoss(sl_config))
        has_rules = True
        logger.info(
            f"止损·固定止损: threshold={config.risk.stop_loss_pct:.2%}"
        )

    # 追踪止损
    if config.risk.trailing_stop_pct is not None and config.risk.trailing_stop_pct > 0:
        tsl_config = StopLossConfig(
            enabled=True,
            stop_type=StopLossType.TRAILING,
            threshold=config.risk.trailing_stop_pct,
            action=TriggerAction.CLOSE_POSITION,
        )
        manager.add_rule(TrailingStopLoss(tsl_config))
        has_rules = True
        logger.info(
            f"止损·追踪止损: threshold={config.risk.trailing_stop_pct:.2%}"
        )

    # 熔断器
    if config.risk.circuit_breaker_enabled:
        cb_config = CircuitBreakerConfig(
            enabled=True,
            max_drawdown=config.risk.circuit_breaker_drawdown,
            max_daily_loss=(
                config.risk.daily_loss_limit
                if config.risk.daily_loss_limit is not None
                else 0.05
            ),
        )
        # Use a BacktestClock so cooldown is measured in simulated time,
        # not wall-clock time (prevents permanent halt in fast backtests).
        from aipha.risk.stop_loss import BacktestClock
        bt_clock = BacktestClock()
        manager.set_circuit_breaker(CircuitBreaker(cb_config, clock=bt_clock))
        manager._backtest_clock = bt_clock  # engine will advance this
        has_rules = True
        logger.info(
            f"止损·熔断器: drawdown={cb_config.max_drawdown:.2%}, "
            f"daily_loss={cb_config.max_daily_loss:.2%}"
        )

    return manager if has_rules else None


# ============================================================================
# 审计日志构建
# ============================================================================


def _build_audit_logger(
    config: BacktestRunConfig,
) -> AuditLogger | None:
    """构建 AuditLogger。

    根据配置中的 enable_audit_log 决定是否创建审计日志记录器。
    日志写入 output_dir 下的 audit/ 子目录。

    Args:
        config: 回测运行配置

    Returns:
        AuditLogger 或 None
    """
    if not config.risk.enabled or not config.risk.enable_audit_log:
        return None

    log_dir = Path(config.output_dir) / "audit"
    audit_config = AuditLogConfig(
        log_dir=log_dir,
        enable_masking=True,
        console_output=False,
    )
    audit_logger = AuditLogger(config=audit_config)
    logger.info(f"审计日志已启用: {log_dir}")
    return audit_logger


# ============================================================================
# 市场状态检测器构建
# ============================================================================


def _build_market_regime_detector(
    config: BacktestRunConfig,
) -> MarketRegimeDetector | None:
    """构建 MarketRegimeDetector。

    根据配置中的 market_regime_enabled 和 benchmark_symbol 决定
    是否创建市场状态检测器。

    Args:
        config: 回测运行配置

    Returns:
        MarketRegimeDetector 或 None
    """
    if not config.risk.enabled or not config.risk.market_regime_enabled:
        return None

    detector = MarketRegimeDetector(
        bear_scale=config.risk.market_regime_bear_scale,
        neutral_scale=config.risk.market_regime_neutral_scale,
    )
    logger.info(
        f"市场状态检测器已启用: benchmark={config.risk.benchmark_symbol} "
        f"bear_scale={config.risk.market_regime_bear_scale} "
        f"neutral_scale={config.risk.market_regime_neutral_scale}"
    )
    return detector


# ============================================================================
# 仓位管理覆盖层构建
# ============================================================================


def _build_position_sizing(
    config: BacktestRunConfig,
) -> "UnifiedPositionSizer | None":
    """从 risk 配置中构建 UnifiedPositionSizer。

    YAML 配置示例::

        risk:
          position_sizing:
            volatility_target:
              enabled: true
              target_vol: 0.20
              lookback_days: 60
              min_scale: 0.2
            drawdown_control:
              enabled: true
              threshold: 0.05
              max_threshold: 0.10
              min_scale: 0.3
            mdd_gradient:              # R94 新增
              enabled: true
              levels: [[0.10, 1.0], [0.15, 0.80], [0.20, 0.55], [0.25, 0.30], [1.00, 0.10]]

    返回 ``None`` 则表示不启用统一仓位缩放器。
    """
    from aipha.risk.position_sizing import (
        DrawdownControlConfig,
        UnifiedPositionSizer,
        ValuationConfig,
        VolatilityTargetConfig,
    )

    if not config.risk.enabled:
        return None

    ps_raw = config.risk.extra.get("position_sizing")
    if not ps_raw or not isinstance(ps_raw, dict):
        ps_raw = {}

    vol_raw = ps_raw.get("volatility_target", {})
    dd_raw = ps_raw.get("drawdown_control", {})
    val_raw = config.risk.extra.get("valuation_sizing", {})
    mdd_grad_raw = ps_raw.get("mdd_gradient", {})

    # 检查各模式是否至少有一个启用
    vol_enabled = vol_raw.get("enabled", False) if vol_raw else False
    dd_enabled = dd_raw.get("enabled", False) if dd_raw else False
    val_enabled = val_raw.get("enabled", False) if val_raw else False
    mdd_grad_enabled = mdd_grad_raw.get("enabled", False) if mdd_grad_raw else False
    if not vol_enabled and not dd_enabled and not val_enabled and not mdd_grad_enabled:
        return None

    vol_cfg = VolatilityTargetConfig(
        target_vol=float(vol_raw.get("target_vol", 0.20)),
        lookback_days=int(vol_raw.get("lookback_days", 60)),
        min_scale=float(vol_raw.get("min_scale", 0.2)),
        max_scale=float(vol_raw.get("max_scale", 1.5)),
        enabled=vol_enabled,
    )
    dd_cfg = DrawdownControlConfig(
        threshold=float(dd_raw.get("threshold", 0.05)),
        max_threshold=float(dd_raw.get("max_threshold", 0.10)),
        min_scale=float(dd_raw.get("min_scale", 0.3)),
        enabled=dd_enabled,
        peak_reset_after_flat_days=int(dd_raw.get("peak_reset_after_flat_days", 60)),
    )
    val_cfg = ValuationConfig(
        enabled=val_enabled,
        metric=str(val_raw.get("metric", "premium_percentile")),
        lookback_days=int(val_raw.get("lookback_days", 500)),
        high_threshold=float(val_raw.get("high_threshold", 0.80)),
        low_threshold=float(val_raw.get("low_threshold", 0.20)),
        high_scale=float(val_raw.get("high_scale", 0.50)),
        low_scale=float(val_raw.get("low_scale", 1.00)),
        mid_scale=float(val_raw.get("mid_scale", 0.80)),
        smooth_days=int(val_raw.get("smooth_days", 5)),
    )

    # R94: MDD 梯度减仓配置（阶梯式）
    mdd_gradient_levels = None
    if mdd_grad_enabled:
        raw_levels = mdd_grad_raw.get("levels")
        if raw_levels and isinstance(raw_levels, list):
            mdd_gradient_levels = [
                (float(lv[0]), float(lv[1])) for lv in raw_levels
            ]
        else:
            # 使用 ml_risk_controls 的默认 5 级
            from aipha.risk.ml_risk_controls import DEFAULT_MDD_LEVELS
            mdd_gradient_levels = list(DEFAULT_MDD_LEVELS)

    # R161 §4.1: 主动减持参数（默认禁用 reduce_threshold=0.0）
    reduce_threshold = float(ps_raw.get("reduce_threshold", 0.0))
    reduce_cooldown_days = int(ps_raw.get("reduce_cooldown_days", 5))
    scale_floor = float(ps_raw.get("scale_floor", 0.3))

    sizer = UnifiedPositionSizer(
        vol_config=vol_cfg, dd_config=dd_cfg, val_config=val_cfg,
        mdd_gradient_levels=mdd_gradient_levels,
        reduce_threshold=reduce_threshold,
        reduce_cooldown_days=reduce_cooldown_days,
        scale_floor=scale_floor,
    )
    logger.info(
        "UnifiedPositionSizer 已启用: vol_target=%.2f(enabled=%s), "
        "dd_threshold=%.2f(enabled=%s), val_metric=%s(enabled=%s), "
        "mdd_gradient=%s",
        vol_cfg.target_vol, vol_enabled,
        dd_cfg.threshold, dd_enabled,
        val_cfg.metric, val_enabled,
        "enabled" if mdd_grad_enabled else "disabled",
    )
    return sizer


# ============================================================================
# 降级管理器构建
# ============================================================================


def _build_degradation_manager(
    config: BacktestRunConfig,
) -> "DegradationManager | None":
    """从 risk 配置中构建 DegradationManager。

    YAML 配置示例::

        risk:
          degradation_enabled: true
          degradation_auto_recover: true
          degradation_recovery_cooldown: 300.0
          degradation_position_scale_warning: 0.5
          degradation_position_scale_degraded: 0.0

    返回 ``None`` 则表示不启用降级管理器。
    """
    from aipha.risk.degradation import DegradationConfig, DegradationManager

    if not config.risk.enabled or not config.risk.degradation_enabled:
        return None

    deg_config = DegradationConfig(
        auto_recover=config.risk.degradation_auto_recover,
        recovery_cooldown_seconds=config.risk.degradation_recovery_cooldown,
        position_scale_warning=config.risk.degradation_position_scale_warning,
        position_scale_degraded=config.risk.degradation_position_scale_degraded,
    )
    manager = DegradationManager(config=deg_config)
    logger.info(
        "DegradationManager 已启用: auto_recover=%s, "
        "cooldown=%.0fs, warn_scale=%.2f, degrade_scale=%.2f",
        deg_config.auto_recover,
        deg_config.recovery_cooldown_seconds,
        deg_config.position_scale_warning,
        deg_config.position_scale_degraded,
    )
    return manager


# ============================================================================
# 组合优化器构建
# ============================================================================


def _build_weight_optimizer(
    config: BacktestRunConfig,
) -> "WeightOptimizer | None":
    """从 portfolio 配置中构建 WeightOptimizer。

    YAML 配置示例::

        portfolio:
          method: risk_parity     # 等权(equal)/风险平价(risk_parity)/最小方差(min_variance)/...
          max_iterations: 200

    当 ``portfolio.method == "equal"`` 时返回 ``None``，因为等权是回测引擎的默认行为。

    Returns:
        WeightOptimizer 实例或 None（等权时无需额外优化器）。
    """
    from aipha.portfolio.convex_optimizer import create_optimizer
    from aipha.portfolio.optimizer import WeightOptimizer  # noqa: F811

    portfolio_cfg = config.portfolio
    method = portfolio_cfg.method.lower()

    if method in ("equal", "equal_weight", "ew"):
        return None  # 等权无需优化器

    optimizer: WeightOptimizer = create_optimizer(method, **portfolio_cfg.params)
    logger.info("组合优化器已创建: method=%s, params=%s", method, portfolio_cfg.params)
    return optimizer
