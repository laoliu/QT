"""Shared risk manager factory — §4.3.2 / §13.15 唯一构建工厂.

build_risk_manager(risk_config, lot_size) 是 CompositeRiskManager 的
唯一构建入口，Backtest / Paper / Live 三条路径**必须**调用此函数。

禁止在 engine_builder.py / combo_orchestrator.py / live_engine_eod.py
中维护独立的风控约束构建逻辑。
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

from aipha.config_shared import RiskConfig
from aipha.risk.constraints import (
    CompositeRiskManager,
    ConcentrationLimit,
    DrawdownLimit,
    ForceRedeemExposureLimit,
    IndustryNeutralConstraint,
    LiquidityConstraint,
    MarketRegimeDetector,
    PositionLimit,
    PriceDeviationGuard,
    SectorLimit,
    StyleExposureLimit,
    TurnoverLimit,
)

logger = logging.getLogger(__name__)


def build_risk_manager(
    risk_config: RiskConfig,
    lot_size: int = 10,
) -> CompositeRiskManager | None:
    """从 RiskConfig 构建 CompositeRiskManager.

    Args:
        risk_config: 风控配置
        lot_size: 最小交易单位（股/张）

    Returns:
        CompositeRiskManager 或 None（风控禁用时）
    """
    if not risk_config.enabled:
        return None

    constraints = []

    # 单票仓位限制（始终启用）
    constraints.append(
        PositionLimit(
            max_weight=risk_config.max_position_weight,
            lot_size=lot_size,
        )
    )
    logger.info(
        "风控·仓位限制: max_weight=%.2f%%",
        risk_config.max_position_weight * 100,
    )

    # 回撤限制 — §4.3.2: max_drawdown >= 1.0 时完全禁用（默认禁用渐进减仓）
    if risk_config.max_drawdown < 1.0:
        constraints.append(
            DrawdownLimit(
                max_drawdown=risk_config.max_drawdown,
                reduce_position_at=risk_config.max_drawdown * 0.5,
                lot_size=lot_size,
                scale_factor_floor=risk_config.drawdown_scale_factor_floor,
                allow_rotation=risk_config.drawdown_allow_rotation,
                peak_reset_after_flat_days=risk_config.drawdown_peak_reset_days,
            )
        )
        logger.info(
            "风控·回撤限制: max_drawdown=%.2f%%"
            ", scale_floor=%s, allow_rotation=%s, peak_reset_days=%s",
            risk_config.max_drawdown * 100,
            risk_config.drawdown_scale_factor_floor,
            risk_config.drawdown_allow_rotation,
            risk_config.drawdown_peak_reset_days,
        )
    else:
        logger.info(
            "风控·回撤限制: 已禁用 (max_drawdown=%.2f >= 1.0, §4.3.2 默认禁用渐进减仓)",
            risk_config.max_drawdown,
        )

    # 行业集中度限制（可选）
    if risk_config.max_sector_weight is not None:
        constraints.append(
            SectorLimit(max_weight=risk_config.max_sector_weight)
        )
        logger.info(
            "风控·行业限制: max_weight=%.2f%%",
            risk_config.max_sector_weight * 100,
        )

    # 换手率限制（可选）
    if risk_config.max_daily_turnover is not None:
        constraints.append(
            TurnoverLimit(
                max_daily_turnover=risk_config.max_daily_turnover,
                lot_size=lot_size,
            )
        )
        logger.info(
            "风控·换手限制: max_daily_turnover=%.2f%%",
            risk_config.max_daily_turnover * 100,
        )

    # 行业中性约束（可选）
    if risk_config.industry_neutral_enabled:
        constraints.append(
            IndustryNeutralConstraint(
                max_deviation=risk_config.industry_max_deviation,
                lot_size=lot_size,
            )
        )
        logger.info(
            "风控·行业中性: max_deviation=%.2f%%",
            risk_config.industry_max_deviation * 100,
        )

    # 集中度限制（可选）
    if risk_config.concentration_enabled:
        constraints.append(
            ConcentrationLimit(
                max_top_n_weight=risk_config.concentration_max_top_n_weight,
                top_n=risk_config.concentration_top_n,
                min_positions=risk_config.concentration_min_positions,
            )
        )
        logger.info(
            "风控·集中度限制: top_%d_max_weight=%.2f%%",
            risk_config.concentration_top_n,
            risk_config.concentration_max_top_n_weight * 100,
        )

    # 流动性约束（可选）
    if risk_config.liquidity_enabled:
        constraints.append(
            LiquidityConstraint(
                max_participation_rate=risk_config.liquidity_max_participation_rate,
                lot_size=lot_size,
            )
        )
        logger.info(
            "风控·流动性约束: max_participation_rate=%.2f%%",
            risk_config.liquidity_max_participation_rate * 100,
        )

    # 风格暴露限制（可选）
    if risk_config.style_exposure_enabled:
        constraints.append(
            StyleExposureLimit(
                max_exposure=dict(risk_config.style_exposure_limits),
            )
        )
        logger.info(
            "风控·风格暴露限制: %s",
            ", ".join(
                f"{k}=±{v:.2f}"
                for k, v in risk_config.style_exposure_limits.items()
            ),
        )

    # 强赎暴露限制（可转债，可选）
    if risk_config.force_redeem_enabled:
        constraints.append(
            ForceRedeemExposureLimit(
                max_weight=risk_config.force_redeem_max_weight,
                cb_value_threshold=risk_config.force_redeem_cb_value_threshold,
                lot_size=lot_size,
            )
        )
        logger.info(
            "风控·强赎暴露限制: max_weight=%.2f%%"
            ", cb_value_threshold=%.1f",
            risk_config.force_redeem_max_weight * 100,
            risk_config.force_redeem_cb_value_threshold,
        )

    # 价格偏离防护（§14.1 Fat-finger 铁律 #1: 价格偏离 MA5 ≥ 20% 阻止买入）
    if risk_config.price_deviation_enabled:
        constraints.append(
            PriceDeviationGuard(
                max_deviation_pct=risk_config.price_deviation_max_pct,
            )
        )
        logger.info(
            "风控·价格偏离防护: max_deviation_pct=%.1f%%",
            risk_config.price_deviation_max_pct * 100,
        )

    risk_manager = CompositeRiskManager(constraints=constraints)
    logger.info("风控管理器已构建: %d 个约束", len(constraints))
    return risk_manager


def build_risk_manager_from_dict(
    raw: dict,
    lot_size: int = 100,
    *,
    top_k: int | None = None,
) -> CompositeRiskManager | None:
    """从原始 dict（YAML risk: 节）一步构建 CompositeRiskManager.

    用于 combo_orchestrator / live_engine_eod / paper_engine_eod 的
    _build_shared_risk_manager() 统一委托，避免重复的 dict→RiskConfig 转换。

    Args:
        raw: YAML 中的 risk: 节字典
        lot_size: 最小交易单位
        top_k: 组合层面的预期持仓数（多组件时取各组件之和）。
            仅用于 §4.3.2 规则 9 / R163 的「死亡螺旋」告警判定
            （``top_k <= 10`` 且 ``max_drawdown < 0.40``）。
            **不传就等于关掉那条告警** —— 这正是修之前的状态：
            上面那句 "Paper/Live 路径也必须执行安全校验" 的注释是对的，
            但三个调用方都没把 top_k 递进来，于是整条 R163 规则在
            paper/live 上从未触发过，只在回测路径（config_runner 传了
            top_k）生效。
    """
    if not raw or not raw.get("enabled", True):
        return None
    risk_cfg = RiskConfig(**{
        k: v for k, v in raw.items()
        if k in RiskConfig.__dataclass_fields__
    })
    # §4.3.2 / WF-9: Paper/Live 路径也必须执行 DrawdownLimit 安全校验
    risk_cfg.validate_drawdown_safety(top_k=top_k)
    return build_risk_manager(risk_cfg, lot_size=lot_size)


def infer_portfolio_top_k(components: Iterable[Any]) -> int | None:
    """从组件列表推出组合层面的持仓数，供 §4.3.2 规则 9 判定用。

    取**各组件 top_k 之和**而不是最小值：R163 关心的是「组合总共只拿几只票」，
    3 个组件各 top_k=10 的组合实际持有约 30 只，并不集中；若取 min 会把它
    误判成高危、天天刷告警，久了就没人看告警了。

    top_k 可能写在组件顶层，也可能在 ``params:`` 子节（两种格式都有存量配置）。
    一个都读不到时返回 ``None`` —— 宁可不判，也不要拿一个猜出来的数去报警。
    """
    total = 0
    found = False
    for c in components or ():
        if not isinstance(c, dict):
            continue
        val = c.get("top_k")
        if val is None:
            params = c.get("params")
            if isinstance(params, dict):
                val = params.get("top_k")
        if val is None:
            continue
        try:
            total += int(val)
            found = True
        except (TypeError, ValueError):
            continue
    return total if found else None
