"""§13.15 统一前交易风控管线 — Backtest / Paper / Live 三路径共享。

核心职责：
  1. CompositeRiskManager.check_batch() — 仓位/回撤/集中度/流动性约束
  2. ComplianceEngine.check_rebalance() — 停牌/涨跌停/参与率/CB强赎合规
  3. 过滤 + 调整订单数量
  4. 返回通过检查的 (symbol, adjusted_qty, side) 列表 + 拒绝明细

使用方：
  - BacktestEngine._process_intent() — 回测路径（§13.15 三路径统一）
  - ComboEODRunner._rebalance_component() — Paper 生产路径
  - LiveEngineEOD._execute_component_live() — Live 路径
  - PaperEngineEOD._execute_component_rebalance() — 独立 Paper 路径

设计原则：
  - 仅依赖 risk.constraints.CompositeRiskManager + portfolio.ComplianceEngine
  - 入参为简单数据结构 (tuple/dict)，不绑定特定 OrderIntent 类型
  - 返回值为过滤后的 intent 列表 + 拒绝日志，调用方自行构造订单
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aipha.portfolio.compliance_engine import ComplianceEngine
    from aipha.risk.constraints import CompositeRiskManager

logger = logging.getLogger(__name__)

# 模块级标志：回放/回测模式抑制飞书告警
_suppress_alerts: bool = False


@dataclass(frozen=True, slots=True)
class PretradeResult:
    """前交易检查结果。"""

    approved: list[tuple[str, int, str]]  # (symbol, qty, side)
    rejected: list[tuple[str, int, str, str]]  # (symbol, qty, side, reason)
    adjusted: list[tuple[str, int, int, str]]  # (symbol, original_qty, new_qty, side)


def run_pretrade_checks(
    intents: list[tuple[str, int, str]],
    current_positions: dict[str, int],
    nav: float,
    prices: dict[str, float],
    risk_manager: CompositeRiskManager | None = None,
) -> PretradeResult:
    """执行统一前交易检查。

    Args:
        intents: [(symbol, qty, side), ...] — 待检查的订单意图
        current_positions: {symbol: qty} — 当前持仓
        nav: 当前组合净值
        prices: {symbol: price} — 最新价格
        risk_manager: 风控管理器（None 则跳过风控检查）

    Returns:
        PretradeResult 包含 approved / rejected / adjusted 三类结果
    """
    if not intents:
        return PretradeResult(approved=[], rejected=[], adjusted=[])

    # §13.18 Fail-Closed: 无风控管理器 → 禁止放行
    if risk_manager is None:
        raise ValueError(
            "risk_manager is None — §13.15 要求前交易风控管线不可跳过。"
            "请通过 build_risk_manager() 构建 CompositeRiskManager 后传入。"
        )

    # 调用 CompositeRiskManager.check_batch()
    results = risk_manager.check_batch(
        orders=intents,
        current_positions=current_positions,
        nav=nav,
        prices=prices,
    )

    approved: list[tuple[str, int, str]] = []
    rejected: list[tuple[str, int, str, str]] = []
    adjusted: list[tuple[str, int, int, str]] = []

    for symbol, qty, side in intents:
        check = results.get(symbol)
        if check is None:
            # 未被检查的（不应发生）→ 放行
            approved.append((symbol, qty, side))
            continue

        if not check.passed:
            if check.adjusted_qty and check.adjusted_qty > 0:
                # 部分通过 → 调整数量
                adjusted.append((symbol, qty, check.adjusted_qty, side))
                approved.append((symbol, check.adjusted_qty, side))
                logger.info(
                    "pretrade: %s %s %d→%d (%s)",
                    side, symbol, qty, check.adjusted_qty, check.message,
                )
            else:
                # 完全拒绝
                rejected.append((symbol, qty, side, check.message or "risk_check_failed"))
                logger.warning(
                    "pretrade: REJECTED %s %s %d (%s)",
                    side, symbol, qty, check.message,
                )
        else:
            approved.append((symbol, qty, side))

    if rejected:
        logger.info(
            "pretrade: %d approved, %d rejected, %d adjusted",
            len(approved), len(rejected), len(adjusted),
        )

    # P1-5: 高拒绝率告警升级
    total = len(intents)
    if total > 0 and not _suppress_alerts:
        rejection_rate = len(rejected) / total
        if rejection_rate > 0.5:
            level = "CRITICAL" if rejection_rate > 0.8 else "WARNING"
            msg = (
                f"pretrade rejection rate {rejection_rate:.0%} "
                f"({len(rejected)}/{total} orders rejected)"
            )
            logger.warning("§14.2 alert: %s — %s", level, msg)
            try:
                from aipha.monitor.feishu_alert import send_feishu_alert
                send_feishu_alert(msg, level=level)
            except Exception as exc:
                logger.debug("alert dispatch failed: %s", exc)

    return PretradeResult(
        approved=approved, rejected=rejected, adjusted=adjusted,
    )


# ---------------------------------------------------------------------------
# ComplianceEngine 合规检查
# ---------------------------------------------------------------------------

def run_compliance_checks(
    intents: list[tuple[str, int, str]],
    prices: dict[str, float],
    compliance_engine: ComplianceEngine | None = None,
    *,
    market_state: dict[str, dict[str, Any]] | None = None,
    portfolio_total: float = 0.0,
    asset_type: str = "equity",
    force_redeem_map: dict[str, float] | None = None,
    is_crowded: bool = False,
) -> PretradeResult:
    """执行合规检查（停牌/涨跌停/参与率/CB强赎）。

    Args:
        intents: [(symbol, qty, side), ...] — 待检查的订单意图
        prices: {symbol: price} — 最新价格
        compliance_engine: 合规引擎（None 则跳过）
        market_state: {symbol: {is_halted, is_limit_up, is_limit_down, volume}}
                      来源可以是 Bar 对象或 API 返回
        portfolio_total: 组合总市值（用于 CB 强赎暴露计算）
        asset_type: "equity" 或 "cb"
        force_redeem_map: {symbol: 转股价值} CB 强赎数据
        is_crowded: 拥挤状态（影响参与率上限）

    Returns:
        PretradeResult 包含 approved / rejected / adjusted 三类结果
    """
    if not intents:
        return PretradeResult(approved=[], rejected=[], adjusted=[])

    # §13.18 Fail-Closed: 无合规引擎 → 禁止放行
    if compliance_engine is None:
        raise ValueError(
            "compliance_engine is None — §13.15 要求合规检查不可跳过。"
            "请构建 ComplianceEngine 后传入。"
        )

    from aipha.portfolio.compliance_engine import ComplianceOrderIntent

    mkt = market_state or {}
    fr_map = force_redeem_map or {}

    # 构建 ComplianceOrderIntent 列表
    compliance_orders: list[ComplianceOrderIntent] = []
    for symbol, qty, side in intents:
        state = mkt.get(symbol, {})
        price = prices.get(symbol, 0.0)
        compliance_orders.append(ComplianceOrderIntent(
            symbol=symbol,
            side=side.lower(),
            quantity=float(qty),
            price=price,
            daily_volume=float(state.get("volume", 0)),
            is_halted=bool(state.get("is_halted", False)),
            is_limit_up=bool(state.get("is_limit_up", False)),
            is_limit_down=bool(state.get("is_limit_down", False)),
            is_st=bool(state.get("is_st", False)),
            list_status=str(state.get("list_status", "L")),
            asset_type=asset_type,
            force_redeem_value=fr_map.get(symbol),
        ))

    result = compliance_engine.check_rebalance(
        orders=compliance_orders,
        portfolio_total=portfolio_total,
        is_crowded=is_crowded,
    )

    # 将 ComplianceResult 映射回 PretradeResult
    if result.passed:
        return PretradeResult(
            approved=list(intents), rejected=[], adjusted=[],
        )

    # 收集被阻止的 symbols（error 级违规）
    blocked: set[str] = set()
    for v in result.violations:
        if v.severity == "error" and v.symbol != "PORTFOLIO":
            blocked.add(v.symbol)

    # 组合级违规（如 force_redeem_exposure）→ 全部拒绝 CB 买入
    portfolio_block = any(
        v.severity == "error" and v.symbol == "PORTFOLIO"
        for v in result.violations
    )

    approved: list[tuple[str, int, str]] = []
    rejected: list[tuple[str, int, str, str]] = []

    for symbol, qty, side in intents:
        if symbol in blocked:
            reason = next(
                (v.detail for v in result.violations
                 if v.symbol == symbol and v.severity == "error"),
                "compliance_blocked",
            )
            rejected.append((symbol, qty, side, reason))
            logger.warning(
                "compliance: REJECTED %s %s %d (%s)",
                side, symbol, qty, reason,
            )
        elif portfolio_block and side.upper() == "BUY":
            rejected.append((
                symbol, qty, side, "portfolio_force_redeem_exposure",
            ))
        else:
            approved.append((symbol, qty, side))

    if rejected:
        logger.info(
            "compliance: %d approved, %d rejected",
            len(approved), len(rejected),
        )

    # P1-5: 合规拒单高拒绝率告警升级（与 pretrade 一致模式）
    total = len(intents)
    if total > 0 and rejected and not _suppress_alerts:
        rejection_rate = len(rejected) / total
        if rejection_rate > 0.5:
            level = "CRITICAL" if rejection_rate > 0.8 else "WARNING"
            reasons_summary = ", ".join(
                sorted(set(r[3] for r in rejected[:10]))
            )
            msg = (
                f"compliance rejection rate {rejection_rate:.0%} "
                f"({len(rejected)}/{total} orders rejected)\n"
                f"主要原因: {reasons_summary}"
            )
            logger.warning("§14.2 compliance alert: %s — %s", level, msg)
            try:
                from aipha.monitor.feishu_alert import send_feishu_alert
                send_feishu_alert(msg, level=level)
            except Exception as exc:
                logger.debug("compliance alert dispatch failed: %s", exc)

    return PretradeResult(
        approved=approved, rejected=rejected, adjusted=[],
    )


# ---------------------------------------------------------------------------
# §14.1 Fat-finger 四重防护 (Layer ②③)
# ---------------------------------------------------------------------------

def run_fat_finger_checks(
    intents: list[tuple[str, int, str]],
    prices: dict[str, float],
    nav: float,
    *,
    ma5: dict[str, float] | None = None,
    daily_cumulative_traded: float = 0.0,
    max_price_deviation_pct: float = 0.20,
    max_daily_cumulative_pct: float = 2.0,
    max_single_order_pct: float = 0.25,
) -> PretradeResult:
    """§14.1 Fat-finger 四重防护 — Layer ②③ 独立检查。

    Layer ①④ 已在 generate_order_intents (trading_ops) 和
    CompositeRiskManager (PositionLimit) 中实现。本函数补充:
      Layer ②: 价格偏离 MA5 ≥ 20% → 阻止
      Layer ③: 日累计交易额 ≤ NAV × 200% → 阻止新增

    Args:
        intents: [(symbol, qty, side), ...]
        prices: {symbol: current_price}
        nav: 当前组合净值
        ma5: {symbol: 5日移动平均价}, None 则跳过 Layer ②
        daily_cumulative_traded: 当日已累计交易金额
        max_price_deviation_pct: MA5 偏离阈值 (默认 20%)
        max_daily_cumulative_pct: 日累计/NAV 上限 (默认 200%)
        max_single_order_pct: 单笔/NAV 上限 (默认 25%)

    Returns:
        PretradeResult
    """
    if not intents:
        return PretradeResult(approved=[], rejected=[], adjusted=[])

    # §13.18 Fail-Closed: NAV≤0 是异常状态, 禁止绕过 fat-finger 检查
    if nav <= 0:
        logger.critical(
            "§14.1 fat-finger: nav=%.2f ≤ 0, 全部订单拒绝 (Fail-Closed)", nav
        )
        rejected_all = [
            (sym, q, s, f"fat_finger_BLOCK: nav={nav:.2f}<=0, 系统状态异常")
            for sym, q, s in intents
        ]
        return PretradeResult(approved=[], rejected=rejected_all, adjusted=[])

    approved: list[tuple[str, int, str]] = []
    rejected: list[tuple[str, int, str, str]] = []

    cumulative = daily_cumulative_traded

    for symbol, qty, side in intents:
        px = prices.get(symbol, 0.0)
        order_value = qty * px if px > 0 else 0.0

        # Layer ①: 单笔金额 ≤ NAV × 25%
        if order_value > nav * max_single_order_pct:
            rejected.append((
                symbol, qty, side,
                f"fat_finger_L1: order/NAV={order_value/nav:.1%} > {max_single_order_pct:.0%}",
            ))
            logger.warning(
                "§14.1 L1: REJECTED %s %s %d — order/NAV=%.1f%%",
                side, symbol, qty, order_value / nav * 100,
            )
            continue

        # Layer ②: 价格偏离 MA5 ≥ 20% → 阻止 BUY
        if ma5 is not None and side.upper() == "BUY":
            ma5_px = ma5.get(symbol)
            if ma5_px and ma5_px > 0 and px > 0:
                deviation = abs(px - ma5_px) / ma5_px
                if deviation >= max_price_deviation_pct:
                    rejected.append((
                        symbol, qty, side,
                        f"fat_finger_L2: price_dev_MA5={deviation:.1%} >= {max_price_deviation_pct:.0%}",
                    ))
                    logger.warning(
                        "§14.1 L2: REJECTED %s %s %d — price=%.2f MA5=%.2f dev=%.1f%%",
                        side, symbol, qty, px, ma5_px, deviation * 100,
                    )
                    continue

        # Layer ③: 日累计交易额 ≤ NAV × 200%
        if cumulative + order_value > nav * max_daily_cumulative_pct:
            rejected.append((
                symbol, qty, side,
                f"fat_finger_L3: daily_cum={cumulative+order_value:.0f} > NAV*{max_daily_cumulative_pct:.0%}",
            ))
            logger.warning(
                "§14.1 L3: REJECTED %s %s %d — daily_cum=%.0f > NAV*%.0f%%",
                side, symbol, qty, cumulative + order_value,
                max_daily_cumulative_pct * 100,
            )
            continue

        # 通过所有检查
        approved.append((symbol, qty, side))
        cumulative += order_value

    if rejected:
        logger.info(
            "fat_finger: %d approved, %d rejected",
            len(approved), len(rejected),
        )

    return PretradeResult(approved=approved, rejected=rejected, adjusted=[])
