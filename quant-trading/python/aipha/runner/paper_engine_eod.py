"""通用 EOD Paper Trading Engine — 配置驱动的模拟盘引擎.

替代 R115/R116/R124/V3 等所有独立 paper 脚本.
每个组合只需 YAML 配置 + scorer 名称即可运行.

用法::

    from aipha.runner.paper_engine_eod import PaperEngineEOD
    engine = PaperEngineEOD("configs/paper/r115_combo.yml")
    engine.run()

CLI::

    python -m aipha.runner.paper_engine_eod --config configs/paper/r115_combo.yml
    python -m aipha.runner.paper_engine_eod --config configs/paper/r115_combo.yml --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger("paper_engine_eod")

_LOG_DIR = Path(__file__).resolve().parents[3] / "logs" / "paper"
_MAX_PENDING_AGE_DAYS = 2  # 挂单最大保留交易日数
_DEFAULT_DEADLINE = "23:30"

# ── 导入共享交易层 ──
from aipha.data.tushare_gateway import get_tushare_gateway
from aipha.execution.trading_ops import (
    FeeParams,
    FallbackPolicy,
    get_fee_params_with_overrides,
    calc_nav as _ops_calc_nav,
    fetch_prev_closes as _ops_fetch_prev_closes,
    fetch_prices,
    fetch_open_prices as _ops_fetch_open_prices,
    fill_order_intents as _ops_fill_order_intents,
    generate_order_intents as _ops_generate_order_intents,
    should_rebalance as _ops_should_rebalance,
    get_latest_trade_date as _ops_get_latest_trade_date,
)
from aipha.execution.state_manager import save_state_json_atomic
from aipha.risk.ml_risk_controls import MddAlertChecker
from aipha.runner.scorers import get_scorer

# =========================================================================
# Re-export from daily_utils for backward compatibility (Phase R1)
# =========================================================================
from aipha.runner.daily_utils import (  # noqa: F401
    ComboState,
    load_config,
    load_state,
    save_state,
    _send_alert,
    _notify_trades,
    _is_trading_day,
    _data_preflight,
    _update_nav,
    _check_mdd,
    _write_audit_log,
    _acquire_pid_lock,
    _resolve_components,
    _state_dir,
    _validate_state,
    _RESERVED_KEYS,
)
from aipha.runner.daily_utils import (
    _PROJ,
    _CN_TZ,
    _STATE_ROOT,
    _AUDIT_DIR,
    _MAX_NAV_HISTORY,
)


# =========================================================================
# 价格获取 — 委托给 trading_ops.fetch_prices (§13.9)
# =========================================================================
# fetch_prices 已从 aipha.execution.trading_ops 导入


# =========================================================================
# 调仓执行
# =========================================================================
def _execute_component_rebalance(
    gw: Any, state: ComboState, comp_name: str,
    comp_cfg: dict, target_value: float,
    prices: dict, trade_date: str, cfg: dict,
    risk_manager: Any = None,
    compliance_engine: Any = None,
) -> None:
    """单组件调仓: scorer → 价格 → generate_order_intents → pending.

    Phase 2 两阶段模型: T 日只生成 intents 存入 pending_orders,
    T+1 日由 _fill_pending_orders 以开盘价成交 (§4.3 一致性).
    """
    scorer_name = comp_cfg.get("scorer", comp_name)
    scorer_fn = get_scorer(scorer_name)
    picks = scorer_fn(gw, comp_cfg, trade_date)

    if not picks:
        logger.warning("[%s] 无选股结果, 跳过调仓", comp_name)
        return

    # 获取目标价格
    new_codes = [c for c in picks if c not in prices]
    if new_codes:
        new_p = fetch_prices(gw, new_codes, trade_date,
                               min_coverage=0.8, cfg=cfg)
        prices.update(new_p)

    # 覆盖率检查
    covered = [c for c in picks if c in prices and prices[c] > 0]
    cov = len(covered) / len(picks) if picks else 1.0
    if cov < 0.6:
        logger.error("[%s] 覆盖率 %.0f%% < 60%%, 跳过", comp_name, cov * 100)
        return
    if cov < 0.8:
        logger.warning("[%s] 覆盖率 %.0f%%, 部分调仓", comp_name, cov * 100)
        target_value = target_value * cov
        picks = covered

    # Fat-finger 保护 — 使用共享 NAV 计算（§13.2 单一实现）
    nav_for_check = _ops_calc_nav(state.cash, state.positions, prices,
                                   fallback=FallbackPolicy.AVG_COST)
    total_nav = nav_for_check.nav
    per_stock = target_value / len(picks) if picks else 0.0

    # 费率参数
    fp = get_fee_params_with_overrides(comp_name, comp_cfg)

    # 去重
    comp_pos = [p for p in state.positions
                if p.get("component") == comp_name]
    unique = {p["code"]: p for p in comp_pos}
    if len(unique) < len(comp_pos):
        other = [p for p in state.positions
                 if p.get("component") != comp_name]
        state.positions = other + list(unique.values())

    # 前收盘价
    all_codes = list(set(picks + [p["code"] for p in state.positions]))
    pc = _ops_fetch_prev_closes(gw, all_codes, trade_date)

    # Phase 2: generate intents, 不立即执行
    cash_floor = cfg.get("cash_reserve_pct", 0.02) * total_nav
    intent_result = _ops_generate_order_intents(
        current_positions=state.positions,
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

    # §13.15: 风控约束过滤（统一前交易管线 pretrade.py）
    if risk_manager is not None and new_intents:
        from aipha.execution.pretrade import run_pretrade_checks

        pre_filter_count = len(new_intents)
        # 构建 positions dict: symbol → shares
        pos_dict: dict[str, int] = {}
        for p in state.positions:
            pos_dict[p["code"]] = pos_dict.get(p["code"], 0) + p.get("shares", 0)
        batch = [
            (i["code"], i["shares"], i["side"]) for i in new_intents
        ]
        pt_result = run_pretrade_checks(
            intents=batch,
            current_positions=pos_dict,
            nav=total_nav,
            prices=prices,
            risk_manager=risk_manager,
        )
        # 构建审批后的 symbol→qty 映射
        approved_map = {s: q for s, q, _ in pt_result.approved}
        filtered = []
        for intent in new_intents:
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
                logger.info(
                    "[%s] 风控拒绝 %s %s",
                    comp_name, intent["side"], key,
                )
        new_intents = filtered
        if pre_filter_count != len(new_intents):
            logger.info(
                "[%s] 风控过滤: %d→%d 笔",
                comp_name, pre_filter_count, len(new_intents),
            )

    # §13.15: 合规检查（停牌/涨跌停/参与率）
    if compliance_engine is not None and new_intents:
        from aipha.execution.pretrade import run_compliance_checks

        at = "cb" if comp_cfg.get("asset_type") == "convertible_bond" else "equity"
        batch = [(i["code"], i["shares"], i["side"]) for i in new_intents]
        # 构建 market_state（涨跌停/停牌由 prev_closes 推导，ST/退市需上游数据）
        mkt_state: dict[str, dict] = {}
        for code in set(i["code"] for i in new_intents):
            mkt_state[code] = {
                "is_halted": False,
                "is_limit_up": False,
                "is_limit_down": False,
                "volume": 0,
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
            pre_cc = len(new_intents)
            new_intents = [i for i in new_intents if i["code"] in cc_approved]
            logger.info(
                "[%s] 合规过滤: %d→%d 笔",
                comp_name, pre_cc, len(new_intents),
            )

    # CONC-3: 挂单幂等去重 — 按 (trade_date, component, code, side) 去重
    existing_keys = {
        (o.get("date", ""), o.get("component", ""), o.get("code", ""), o.get("side", ""))
        for o in state.pending_orders
    }
    unique_intents = [
        i for i in new_intents
        if (i.get("date", ""), i.get("component", ""), i.get("code", ""), i.get("side", ""))
        not in existing_keys
    ]
    dup_count = len(new_intents) - len(unique_intents)
    if dup_count > 0:
        logger.warning("[%s] 过滤 %d 笔重复挂单", comp_name, dup_count)
    state.pending_orders.extend(unique_intents)
    logger.info("[%s] 生成 %d 笔挂单意图 (T+1 开盘成交), 市值=%.0f",
                comp_name, len(unique_intents), target_value)


# =========================================================================
# Pending Order Fill — T+1 开盘价成交
# =========================================================================
_MAX_PENDING_AGE_DAYS = 2  # 挂单最大保留交易日数, 超过即过期取消


def _fill_pending_orders(
    gw: Any, state: ComboState, trade_date: str,
    cfg: dict,
) -> bool:
    """填充昨日 pending orders, 以今日开盘价成交.

    含挂单过期机制: 超过 _MAX_PENDING_AGE_DAYS 个交易日未成交的
    挂单自动取消并记录 warning.

    Returns: True 如果有成交, False 如果无 pending 或全部跳过.
    """
    if not state.pending_orders:
        return False

    # ── 过期清理: 超过 N 个自然日的挂单自动取消 ──
    from datetime import datetime, timedelta
    td_dt = datetime.strptime(trade_date, "%Y%m%d")
    max_age = timedelta(days=_MAX_PENDING_AGE_DAYS * 2)  # 自然日近似
    active, expired = [], []
    for o in state.pending_orders:
        sig = o.get("signal_date", "")
        if sig:
            try:
                sig_dt = datetime.strptime(sig, "%Y%m%d")
                if td_dt - sig_dt > max_age:
                    expired.append(o)
                    continue
            except ValueError:
                pass
        active.append(o)
    if expired:
        logger.warning("过期取消 %d 笔挂单 (signal_date 距今 >%d 天): %s",
                       len(expired), _MAX_PENDING_AGE_DAYS * 2,
                       [o["code"] for o in expired[:5]])
    state.pending_orders = active
    if not active:
        return False

    n_pending = len(active)
    codes = list({o["code"] for o in active})
    logger.info("填充 %d 笔挂单 (%d 只标的) ...",
                n_pending, len(codes))

    # 获取今日开盘价
    open_prices = _ops_fetch_open_prices(gw, codes, trade_date)
    if not open_prices:
        logger.warning("无法获取开盘价, 挂单保留至下一交易日 "
                       "(%d 笔, 将在 %d 天后过期)",
                       n_pending, _MAX_PENDING_AGE_DAYS * 2)
        return False

    # 按 component 构建费率映射 (§F2-2 修复)
    components = {o.get("component", "") for o in active}
    fp_map: dict[str, FeeParams] = {}
    default_fp: FeeParams | None = None
    for comp in components:
        comp_cfg_local = cfg.get(comp, {})
        fp_map[comp] = get_fee_params_with_overrides(comp, comp_cfg_local)
        if default_fp is None:
            default_fp = fp_map[comp]

    result = _ops_fill_order_intents(
        cash=state.cash, positions=state.positions,
        intents=active, fill_prices=open_prices,
        trade_date=trade_date,
        fee_params=default_fp or get_fee_params_with_overrides("", {}),
        fee_params_by_component=fp_map,
    )
    state.cash = result.new_cash
    state.positions = result.new_positions

    # 回填信号价 —— 没有它就无法事后度量「信号价→成交价」跑掉多少，
    # 追高上限 (max_chase_pct) 也就只能凭直觉给。挂单里本就带着这个数，
    # 此前在成交时被丢掉了。按 (code, side) 关联，同一标的可能一买一卖。
    # 参见 aipha.advisory.chase_calibration
    intent_by_key = {
        (o["code"], o.get("side", "")): o for o in active
    }
    for t in result.sells + result.buys:
        intent = intent_by_key.get((t.code, t.side), {})
        state.trades.append({
            "date": t.trade_date, "code": t.code, "side": t.side,
            "price": t.price, "shares": t.shares,
            "amount": t.amount, "cost": t.cost,
            "component": t.component,
            "signal_price": intent.get("signal_price"),
            "signal_date": intent.get("signal_date", ""),
        })

    # 已成交的 codes
    filled_codes = {t.code for t in result.sells + result.buys}

    # 保留未成交的挂单 (缺价导致的跳过), 已成交的移除
    unfilled = [o for o in active if o["code"] not in filled_codes
                and o["code"] not in {s["code"] for s in result.skipped
                                       if "持仓" in s.get("reason", "")
                                       or "资金" in s.get("reason", "")}]
    if unfilled:
        logger.info("保留 %d 笔缺价挂单至下一交易日", len(unfilled))
    state.pending_orders = unfilled

    if result.skipped:
        for s in result.skipped[:5]:
            logger.warning("挂单跳过 %s: %s", s["code"], s["reason"])
    filled = len(result.sells) + len(result.buys)
    logger.info("挂单成交 %d 笔, 跳过 %d 笔, 保留 %d 笔",
                filled, len(result.skipped), len(unfilled))
    return filled > 0


# =========================================================================
# 核心引擎 run_daily
# =========================================================================
def run_daily(cfg: dict, dry_run: bool = False) -> None:
    """通用单日模拟盘主流程.

    Args:
        cfg: YAML 配置字典, 需包含 profile_name/initial_capital/
             portfolio_weights/components 或顶层组件键.
        dry_run: 仅更新 NAV, 不执行调仓.
    """
    profile = cfg.get("profile_name", "paper_combo")
    capital = cfg.get("initial_capital", 10_000_000)
    weights = cfg.get("portfolio_weights", {})

    # 权重校验
    for wn, wv in weights.items():
        if not isinstance(wv, (int, float)) or wv < 0 or wv > 1:
            raise ValueError(f"权重 {wn}={wv} 无效")
    if abs(sum(weights.values()) - 1.0) > 0.01:
        logger.warning("权重总和 %.4f != 1.0", sum(weights.values()))

    # 全局开关
    settings_path = _PROJ / "configs" / "paper" / "system_settings.json"
    if settings_path.exists():
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        if not settings.get("paper_trading_enabled", True):
            logger.warning("paper_trading_enabled=false, 跳过")
            return

    # §13.13 H5: FREEZE marker check — prevent restart of emergency-stopped combo
    freeze_path = _PROJ / "data" / "paper_state" / profile / "FREEZE"
    if freeze_path.exists():
        logger.error(
            "FREEZE marker detected at %s — combo '%s' is emergency-stopped. "
            "Use 'emergency_stop.py --unfreeze' to lift. Aborting.",
            freeze_path, profile,
        )
        return

    # 数据预检
    gw = get_tushare_gateway()
    if not _data_preflight(gw):
        logger.error("数据预检未通过, 中止")
        return

    # INFRA-3: 检查数据预热成功标志
    today_str = datetime.now(tz=_CN_TZ).strftime("%Y%m%d")
    preheat_marker = _PROJ / "data" / "paper_state" / "_markers" / f"preheat_{today_str}.ok"
    if not preheat_marker.exists():
        logger.warning(
            "数据预热标志不存在 (%s), 数据可能不完整 — 继续但需关注",
            preheat_marker,
        )

    # 交易日检查
    if not _is_trading_day(gw):
        logger.info("非交易日, 跳过")
        return

    trade_date = _ops_get_latest_trade_date(gw)
    logger.info("=" * 60)
    logger.info("PaperEngine daily  date=%s  profile=%s", trade_date, profile)

    state = load_state(profile, capital)

    # ── Phase A: 填充昨日 pending orders (T+1 开盘价成交) ──
    if state.pending_orders:
        try:
            filled = _fill_pending_orders(gw, state, trade_date, cfg)
            if filled:
                save_state(state)  # 填充后立即保存
                logger.info("挂单填充完成, 已保存状态")
        except Exception as exc:
            logger.error("挂单填充异常: %s", exc, exc_info=True)
            _send_alert(
                f"[{profile}] 挂单填充失败",
                f"错误: {str(exc)[:200]}",
                level="ERROR",
            )
            # SRE-4: 挂单填充失败 → 阻止后续调仓，避免基于错误持仓计算新订单
            logger.error("挂单填充失败, 跳过本日调仓以防持仓错误")
            _write_audit_log(state, trade_date)
            save_state(state)
            return

    # 获取持仓价格
    held = [p["code"] for p in state.positions]
    prices = fetch_prices(gw, held, trade_date, cfg=cfg) if held else {}

    # NAV
    nav = _update_nav(state, prices, trade_date)
    logger.info("NAV=%.2f  cash=%.2f  positions=%d",
                nav, state.cash, len(state.positions))

    # MDD
    risk = _check_mdd(state, nav, cfg, trade_date=trade_date)
    if risk == "halt":
        logger.error("MDD 安全阀触发, 暂停调仓")
        _send_alert(
            f"[{profile}] MDD 安全阀",
            f"NAV: {nav:,.0f}, 峰值: {state.peak_nav:,.0f}",
            level="CRITICAL",
        )
        _write_audit_log(state, trade_date)
        save_state(state)
        return

    if dry_run:
        logger.info("[DRY-RUN] 跳过调仓")
        _write_audit_log(state, trade_date)
        save_state(state)
        return

    # ── 组件遍历调仓 ──
    pre_trade_nav = nav
    comp_errors: dict[str, str] = {}

    # §13.15: 从 YAML risk: 节构建共享风控管理器
    _risk_manager = None
    risk_raw = cfg.get("risk", {})
    if risk_raw.get("enabled", True):
        try:
            from aipha.risk.risk_config_loader import build_risk_manager_from_dict
            _risk_manager = build_risk_manager_from_dict(risk_raw, lot_size=10)
        except Exception as _e:
            logger.warning("风控管理器构建失败: %s", _e)

    # §13.15: 合规检查引擎
    _compliance_engine = None
    try:
        from aipha.portfolio.compliance_engine import ComplianceEngine
        from aipha.utils.compliance_config import DEFAULT_COMPLIANCE_CONFIG_PATH
        _ce_path = DEFAULT_COMPLIANCE_CONFIG_PATH
        if _ce_path.exists():
            _compliance_engine = ComplianceEngine.from_config(_ce_path)
        else:
            _compliance_engine = ComplianceEngine()
            logger.warning(
                "§13.15 合规配置缺失 (%s)，已退回内置默认限额 —— "
                "配置文件里的限额本次不生效",
                _ce_path,
            )
    except Exception as _e:
        logger.error(
            "合规检查引擎构建失败: %s — 阻止本日调仓!",
            _e,
        )
        _send_alert(
            f"[{profile}] 合规引擎构建失败 — 已阻止调仓",
            f"错误: {str(_e)[:200]}\n无合规约束不允许交易, 请排查后重试",
            level="CRITICAL",
        )
        return

    # 从 YAML 中提取组件配置
    components = _resolve_components(cfg, weights)

    for comp_name, comp_cfg in components:
        freq = comp_cfg.get("rebalance_freq", "every_5d")
        last_rb = state.last_rebalance.get(comp_name, "")
        if not _ops_should_rebalance(freq, trade_date, last_rb):
            continue

        w = weights.get(comp_name, 0)
        target_value = nav * w
        logger.info("[%s] 调仓日, 开始打分...", comp_name)

        try:
            _execute_component_rebalance(
                gw, state, comp_name, comp_cfg,
                target_value, prices, trade_date, cfg,
                risk_manager=_risk_manager,
                compliance_engine=_compliance_engine,
            )
            state.last_rebalance[comp_name] = trade_date
            save_state(state)  # P0-4: 每组件后保存
            state.consecutive_failures.pop(comp_name, None)
        except Exception as exc:
            logger.error("[%s] 调仓失败: %s", comp_name, exc, exc_info=True)
            comp_errors[comp_name] = str(exc)
            n = state.consecutive_failures.get(comp_name, 0) + 1
            state.consecutive_failures[comp_name] = n
            if n >= 3:
                _send_alert(
                    f"[{profile}] {comp_name} 连续 {n} 天失败",
                    f"错误: {str(exc)[:200]}",
                    level="ERROR",
                )

    if comp_errors:
        logger.warning("%d 个组件失败: %s",
                       len(comp_errors), list(comp_errors.keys()))

    # Post-trade NAV
    held_after = [p["code"] for p in state.positions]
    prices_after = (
        fetch_prices(gw, held_after, trade_date,
                      min_coverage=0.8, cfg=cfg)
        if held_after else {}
    )
    nav_result = _ops_calc_nav(state.cash, state.positions, prices_after,
                                fallback=FallbackPolicy.AVG_COST)
    final_nav = nav_result.nav
    if state.nav_history and state.nav_history[-1].get("date") == trade_date:
        state.nav_history[-1]["nav"] = round(final_nav, 2)
    state.peak_nav = max(state.peak_nav, final_nav)

    # P2-2: NAV 对账
    if pre_trade_nav > 0 and final_nav > 0:
        drift = abs(final_nav - pre_trade_nav) / pre_trade_nav
        if drift > 0.10:
            _send_alert(
                "NAV 对账异常(严重)",
                f"pre={pre_trade_nav:,.0f} → post={final_nav:,.0f}"
                f" 漂移 {drift:.2%} — 阻断状态保存!",
                level="CRITICAL",
            )
            # SRE-6: NAV 漂移 >10% 阻断 state save，防止错误状态持久化
            logger.error(
                "NAV 漂移 %.2f%% 超过 10%% 阈值, 阻断 save_state — "
                "请人工检查后手动恢复",
                drift * 100,
            )
            _write_audit_log(state, trade_date)
            return
        elif drift > 0.03:
            logger.warning("NAV 漂移 %.2f%%", drift * 100)

    _write_audit_log(state, trade_date)
    save_state(state)

    # 交易推送
    _notify_trades(profile, trade_date, state, pre_trade_nav, final_nav)

    logger.info("日终完成. NAV=%.2f (post-trade)", final_nav)


# =========================================================================
# PaperEngineEOD 类包装
# =========================================================================
class PaperEngineEOD:
    """配置驱动的 EOD Paper Trading 引擎.

    .. deprecated:: 2026-05
        请使用 ComboOrchestrator + PaperMode (execution_mode.py)。
        此类将于 2026 Q3 移除。

    用法::

        engine = PaperEngineEOD("configs/paper/r115_combo.yml")
        engine.run()            # 正常运行
        engine.run(dry_run=True)  # 仅更新 NAV
    """

    def __init__(self, config_path: str | Path) -> None:
        import warnings
        warnings.warn(
            "PaperEngineEOD 已废弃, 请使用 ComboOrchestrator + PaperMode。"
            "此类将于 2026 Q3 移除。",
            DeprecationWarning,
            stacklevel=2,
        )
        self.config_path = Path(config_path)
        self.cfg = load_config(self.config_path)

    @property
    def profile(self) -> str:
        return self.cfg.get("profile_name", "paper_combo")

    def run(self, dry_run: bool = False) -> None:
        """执行单日模拟盘主流程."""
        lock_fd = None
        try:
            lock_fd = _acquire_pid_lock(self.profile)
            run_daily(self.cfg, dry_run=dry_run)
        finally:
            if lock_fd is not None:
                try:
                    lock_fd.close()
                except Exception:  # silent-pass-exempt: lock cleanup
                    pass


# =========================================================================
# 日志初始化
# =========================================================================
def _setup_logging(profile: str, verbose: bool = False) -> None:
    """配置文件 + 控制台双输出日志."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(level)

    # console
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(ch)

    # file
    today = datetime.now(_CN_TZ).strftime("%Y%m%d")
    fh = logging.FileHandler(
        _LOG_DIR / f"{profile}_{today}.log",
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(fh)

    # §14.2 告警即生命线：WARNING+ 自动推送飞书
    from aipha.monitor.alert_handler import setup_alert_logging
    setup_alert_logging()


# =========================================================================
# CLI 入口
# =========================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="通用 EOD Paper Trading Engine",
    )
    parser.add_argument(
        "--config", "-c", required=True,
        help="YAML 配置文件路径",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅更新 NAV, 不调仓",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="输出 DEBUG 级别日志",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    profile = cfg.get("profile_name", "paper_combo")
    _setup_logging(profile, verbose=args.verbose)

    engine = PaperEngineEOD(args.config)
    engine.run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
