"""统一组件调仓函数 — Phase R2b.

ADR-V2-6: 返回 list[dict] intents，不操作 state。
调用方决定如何处置:
  - Paper: state.pending_orders.extend(intents)
  - Live:  live_delegate.submit_orders(intents)
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from aipha.execution.trading_ops import (
    calc_nav as _ops_calc_nav,
    FallbackPolicy,
    fetch_prev_closes as _ops_fetch_prev_closes,
    fetch_prices,
    generate_order_intents as _ops_generate_order_intents,
    get_fee_params_with_overrides,
)
from aipha.runner.daily_utils import ComboState
from aipha.runner.scorers import get_scorer

logger = logging.getLogger(__name__)


def execute_component_rebalance(
    gw: Any,
    state: ComboState,
    comp_name: str,
    comp_cfg: dict,
    target_value: float,
    prices: dict[str, float],
    trade_date: str,
    cfg: dict,
    risk_manager: Any | None = None,
    compliance_engine: Any | None = None,
    market_state_provider: Callable[..., dict[str, dict]] | None = None,
) -> list[dict]:
    """单组件调仓: scorer → 价格 → intents → 风控 → 合规 → 返回.

    Phase 2 两阶段模型: T 日生成 intents,
    T+1 由 fill 逻辑以开盘价成交 (§4.3 一致性).

    Returns:
        list[dict]: 挂单意图列表 (不操作 state)
    """
    # 1. Scorer
    scorer_name = comp_cfg.get("scorer", comp_name)
    scorer_fn = get_scorer(scorer_name)
    picks = scorer_fn(gw, comp_cfg, trade_date)

    if not picks:
        logger.warning("[%s] 无选股结果, 跳过调仓", comp_name)
        return []

    # 2. 价格补充
    new_codes = [c for c in picks if c not in prices]
    if new_codes:
        new_p = fetch_prices(gw, new_codes, trade_date,
                             min_coverage=0.8, cfg=cfg)
        prices.update(new_p)

    # 3. 覆盖率检查
    covered = [c for c in picks if c in prices and prices[c] > 0]
    cov = len(covered) / len(picks) if picks else 1.0
    if cov < 0.6:
        logger.error("[%s] 覆盖率 %.0f%% < 60%%, 跳过", comp_name, cov * 100)
        return []
    if cov < 0.8:
        logger.warning("[%s] 覆盖率 %.0f%%, 部分调仓", comp_name, cov * 100)
        target_value = target_value * cov
        picks = covered

    # 4. NAV fat-finger 保护
    nav_result = _ops_calc_nav(state.cash, state.positions, prices,
                                fallback=FallbackPolicy.AVG_COST)
    total_nav = nav_result.nav
    per_stock = target_value / len(picks) if picks else 0.0

    # 5. 费率参数
    fp = get_fee_params_with_overrides(comp_name, comp_cfg)

    # 6. 持仓去重 (本地副本, 不修改 state — ADR-V2-6)
    comp_pos = [p for p in state.positions
                if p.get("component") == comp_name]
    unique = {p["code"]: p for p in comp_pos}
    other_pos = [p for p in state.positions
                 if p.get("component") != comp_name]
    working_positions = other_pos + list(unique.values())
    if len(unique) < len(comp_pos):
        logger.warning("[%s] 去重 %d 条持仓", comp_name,
                       len(comp_pos) - len(unique))

    # 7. 前收盘价
    all_codes = list(set(picks + [p["code"] for p in working_positions]))
    pc = _ops_fetch_prev_closes(gw, all_codes, trade_date)

    # 8. Intent 生成
    cash_floor = cfg.get("cash_reserve_pct", 0.02) * total_nav
    intent_result = _ops_generate_order_intents(
        current_positions=working_positions,
        target_codes=picks, prices=prices,
        per_stock_value=per_stock, trade_date=trade_date,
        fee_params=fp, component=comp_name,
        check_price_limits=True, prev_closes=pc,
        cash_available=state.cash, cash_floor=cash_floor,
        total_nav=total_nav,
    )
    new_intents = [
        {
            "code": i.code, "side": i.side,
            "shares": i.shares, "signal_price": i.signal_price,
            "component": i.component, "reason": i.reason,
            "signal_date": trade_date,
        }
        for i in intent_result.intents
    ]
    if intent_result.skipped:
        for s in intent_result.skipped[:5]:
            logger.info("[%s] 跳过 %s: %s",
                        comp_name, s["code"], s["reason"])

    # 9. §13.15 风控约束过滤
    if risk_manager is not None and new_intents:
        new_intents = _apply_pretrade_risk(
            new_intents, state, total_nav, prices, risk_manager, comp_name,
        )

    # 10. §13.15 合规检查（停牌/涨跌停/参与率）
    if compliance_engine is not None and new_intents:
        new_intents = _apply_compliance(
            new_intents, comp_cfg, prices, total_nav,
            compliance_engine, comp_name, market_state_provider,
            gw, trade_date, pc,
        )

    # 11. 返回 intents (ADR-V2-6: 不操作 state)
    logger.info("[%s] 生成 %d 笔挂单意图 (T+1 开盘成交), 市值=%.0f",
                comp_name, len(new_intents), target_value)
    return new_intents


# =========================================================================
# 内部辅助
# =========================================================================
def _apply_pretrade_risk(
    intents: list[dict],
    state: ComboState,
    total_nav: float,
    prices: dict[str, float],
    risk_manager: Any,
    comp_name: str,
) -> list[dict]:
    """§13.15 前交易风控过滤."""
    from aipha.execution.pretrade import run_pretrade_checks

    pre_count = len(intents)
    pos_dict: dict[str, int] = {}
    for p in state.positions:
        pos_dict[p["code"]] = pos_dict.get(p["code"], 0) + p.get("shares", 0)
    batch = [(i["code"], i["shares"], i["side"]) for i in intents]
    pt_result = run_pretrade_checks(
        intents=batch,
        current_positions=pos_dict,
        nav=total_nav,
        prices=prices,
        risk_manager=risk_manager,
    )
    approved_map = {s: q for s, q, _ in pt_result.approved}
    filtered = []
    for intent in intents:
        key = intent["code"]
        if key in approved_map:
            adj_qty = approved_map[key]
            if adj_qty != intent["shares"]:
                logger.info(
                    "[%s] 风控缩减 %s %s: %d→%d",
                    comp_name, intent["side"], key,
                    intent["shares"], adj_qty,
                )
                intent["shares"] = adj_qty
            filtered.append(intent)
        else:
            logger.info("[%s] 风控拒绝 %s %s", comp_name, intent["side"], key)
    if pre_count != len(filtered):
        logger.info("[%s] 风控过滤: %d→%d 笔", comp_name, pre_count, len(filtered))
    return filtered


def _apply_compliance(
    intents: list[dict],
    comp_cfg: dict,
    prices: dict[str, float],
    total_nav: float,
    compliance_engine: Any,
    comp_name: str,
    market_state_provider: Callable[..., dict[str, dict]] | None,
    gw: Any,
    trade_date: str,
    prev_closes: dict[str, float],
) -> list[dict]:
    """§13.15 合规检查（停牌/涨跌停/参与率）."""
    from aipha.execution.pretrade import run_compliance_checks

    at = "cb" if comp_cfg.get("asset_type") == "convertible_bond" else "equity"
    batch = [(i["code"], i["shares"], i["side"]) for i in intents]
    codes = list({i["code"] for i in intents})

    # 构建 market_state — 优先使用外部 provider (ADR-V2-7)
    if market_state_provider is not None:
        mkt_state = market_state_provider(gw, codes, trade_date, prices, prev_closes)
    else:
        mkt_state = {
            code: {"is_halted": False, "is_limit_up": False,
                   "is_limit_down": False, "volume": 0}
            for code in codes
        }

    cc_result = run_compliance_checks(
        intents=batch,
        prices=prices,
        compliance_engine=compliance_engine,
        market_state=mkt_state,
        portfolio_total=total_nav,
        asset_type=at,
    )
    if cc_result.rejected:
        cc_approved = {s for s, _, _ in cc_result.approved}
        pre_cc = len(intents)
        intents = [i for i in intents if i["code"] in cc_approved]
        logger.info("[%s] 合规过滤: %d→%d 笔", comp_name, pre_cc, len(intents))
    return intents
