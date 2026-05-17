"""实盘 EOD 引擎 — 读取 QMT 真实持仓, 策略打分, 发起实盘委托.

复用 PaperEngineEOD 的配置驱动架构, 但:
  - 通过 QMTGateway 读取真实持仓/资金
  - 通过 QMTGateway 发起真实委托 (限价单)
  - 调仓前执行持仓对账 (内部状态 ↔ QMT 实际)
  - 安全门控: live_trading_enabled 开关 + 风控预检 + 确认模式

用法::

    engine = LiveEngineEOD("configs/live/live_eod_combo.yml")
    engine.run()                # 正常: 打分 → 调仓 → 下单
    engine.run(dry_run=True)    # 仅打分 + 对账, 不下单
    engine.run(confirm=True)    # 下单前需人工确认

CLI::

    python -m aipha.runner.live_engine_eod -c configs/live/live_eod_combo.yml
    python -m aipha.runner.live_engine_eod -c configs/live/live_eod_combo.yml --dry-run
    python -m aipha.runner.live_engine_eod -c configs/live/live_eod_combo.yml --confirm
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
import yaml

logger = logging.getLogger("live_engine_eod")

_PROJ = Path(__file__).resolve().parents[3]
_CN_TZ = ZoneInfo("Asia/Shanghai")
_STATE_ROOT = _PROJ / "data" / "live_state"
_AUDIT_DIR = _PROJ / "logs" / "audit"
_LOG_DIR = _PROJ / "logs" / "live"

# ── 共享层导入 ──
from aipha.data.tushare_gateway import get_tushare_gateway
from aipha.execution.gateway import GatewayOrder, OrderStatus, TradingMode
from aipha.execution.state_manager import save_state_json_atomic
from aipha.execution.trading_ops import (
    FeeParams,
    OrderIntent,
    OrderIntentResult,
    get_fee_params_with_overrides,
    calc_trade_cost,
    calc_nav as _ops_calc_nav,
    FallbackPolicy,
    fetch_prices,
    fetch_prev_closes as _ops_fetch_prev_closes,
    generate_order_intents,
    should_rebalance as _ops_should_rebalance,
    get_latest_trade_date as _ops_get_latest_trade_date,
)
from aipha.execution.qmt_account_sync import (
    AccountSyncer,
    PositionRecord,
    ReconcileResult,
)
from aipha.runner.scorers import get_scorer
# 共享日级工具 — Phase R1 迁移到 daily_utils
from aipha.runner.daily_utils import (
    load_config,
    ComboState,
    _resolve_components,
    _is_trading_day,
    _data_preflight,
    _check_mdd,
    _write_audit_log,
    _send_alert,
    _notify_trades,
)


def _find_component(state: ComboState, ts_code: str) -> str:
    """在已有持仓中查找标的所属组件名."""
    for p in state.positions:
        if p.get("code") == ts_code:
            return p.get("component", "")
    return ""


def _state_dir(profile: str) -> Path:
    return _STATE_ROOT / profile


def load_live_state(profile: str, initial_capital: float) -> ComboState:
    """加载实盘状态 (独立于 paper_state, 存放在 data/live_state/)."""
    fp = _state_dir(profile) / "state.json"
    if fp.exists():
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
            return ComboState(**{
                k: v for k, v in raw.items()
                if k in ComboState.__dataclass_fields__
            })
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.error("Live state 损坏: %s", exc)
            raise RuntimeError(f"Live state 损坏: {fp}") from exc
    return ComboState(
        profile_name=profile,
        initial_capital=initial_capital,
        cash=initial_capital,
        peak_nav=initial_capital,
    )


def save_live_state(state: ComboState) -> None:
    """原子化保存实盘状态."""
    d = _state_dir(state.profile_name)
    d.mkdir(parents=True, exist_ok=True)
    fp = d / "state.json"
    state.last_update = datetime.now(tz=_CN_TZ).isoformat()
    if state.nav_history and len(state.nav_history) > 1000:
        state.nav_history = state.nav_history[-1000:]
    save_state_json_atomic(fp, asdict(state))


# =====================================================================
# LiveEngineEOD 类
# =====================================================================
class LiveEngineEOD:
    """配置驱动的 EOD 实盘引擎.

    .. deprecated:: 2026-05
        请使用 ComboOrchestrator + LiveMode (execution_mode.py) + LiveDelegate。
        此类将于 2026 Q3 移除。
    """

    def __init__(self, config_path: str | Path) -> None:
        import warnings
        warnings.warn(
            "LiveEngineEOD 已废弃, 请使用 ComboOrchestrator + LiveMode + LiveDelegate。"
            "此类将于 2026 Q3 移除。",
            DeprecationWarning,
            stacklevel=2,
        )
        self.config_path = Path(config_path)
        self.cfg = load_config(self.config_path)
        self._qmt = None  # QMTGateway, lazy init
        self._risk_limits: dict = {}  # 填充于 _safety_preflight
        # §13.15: 共享风控管理器
        self._risk_manager = self._build_shared_risk_manager()
        # §13.15: 合规检查引擎
        self._compliance_engine = self._build_compliance_engine()

    def _build_shared_risk_manager(self):
        """§13.15: 从 YAML risk: 节构建共享风控管理器."""
        from aipha.risk.risk_config_loader import build_risk_manager_from_dict
        risk_raw = self.cfg.get("risk", {})
        if not risk_raw or not risk_raw.get("enabled", True):
            logger.info("风控管理器未启用（risk 节为空或 enabled=false）")
            return None
        # §13.15 + 审计修复: 实盘路径风控构建失败必须抛异常，禁止静默跳过
        # §13.15: 使用组件中的最小 lot_size
        components = self.cfg.get("components", [])
        min_lot = min(
            (int(c.get("lot_size", 100)) for c in components),
            default=100,
        )
        return build_risk_manager_from_dict(risk_raw, lot_size=min_lot)

    def _build_compliance_engine(self):
        """§13.15: 构建合规检查引擎。"""
        try:
            from aipha.portfolio.compliance_engine import ComplianceEngine
            config_path = Path("configs/algo_trading_compliance.yml")
            if config_path.exists():
                return ComplianceEngine.from_config(config_path)
            return ComplianceEngine()
        except Exception as e:
            logger.warning("合规检查引擎构建失败: %s", e)
            return None

    def _feed_risk_constraints(
        self, trade_date: str, nav: float, state: object,
    ) -> None:
        """§13.15: 每日喂养风控约束状态，与 BacktestEngine 对齐。"""
        rm = self._risk_manager
        if rm is None:
            return
        for c in rm.constraints:
            if hasattr(c, 'reset_daily'):
                c.reset_daily(str(trade_date))
        for c in rm.constraints:
            if hasattr(c, 'update_nav'):
                c.update_nav(nav)
        has_pos = bool(getattr(state, 'positions', None))
        for c in rm.constraints:
            if hasattr(c, 'update_position_state'):
                c.update_position_state(has_pos)

    @property
    def profile(self) -> str:
        return self.cfg.get("profile_name", "live_combo")

    def run(
        self, *, dry_run: bool = False, confirm: bool = False,
    ) -> None:
        """执行单日实盘主流程."""
        profile = self.profile
        capital = self.cfg.get("initial_capital", 10_000_000)
        weights = self.cfg.get("portfolio_weights", {})
        try:
            # §14.1 Kill Switch 文件检测
            kill_flag = Path("configs/kill_switch.flag")
            if kill_flag.exists():
                logger.critical(
                    "Kill Switch 已激活 (%s), 中止实盘!", kill_flag,
                )
                _send_alert(
                    f"[{profile}] Kill Switch 激活",
                    f"检测到 {kill_flag}, 实盘中止。请手动删除文件解除。",
                    level="CRITICAL",
                )
                return
            # 安全预检
            if not self._safety_preflight():
                logger.error("安全预检未通过, 中止实盘")
                return
            # 数据预检
            gw = get_tushare_gateway()
            if not _data_preflight(gw):
                logger.error("数据预检未通过, 中止")
                _send_alert(
                    f"[{profile}] 数据预检失败",
                    "TuShare API 不可用, 实盘中止",
                    level="CRITICAL",
                )
                return
            # 交易日检查
            if not _is_trading_day(gw):
                logger.info("非交易日, 跳过")
                return
            trade_date = _ops_get_latest_trade_date(gw)
            logger.info("=" * 60)
            logger.info(
                "LiveEngine EOD  date=%s  profile=%s  dry_run=%s",
                trade_date, profile, dry_run,
            )
            # 加载内部状态
            state = load_live_state(profile, capital)
            # 从 QMT 同步真实持仓
            recon = self._sync_positions_from_qmt(state)
            if recon.critical_count > 0:
                logger.error(
                    "持仓对账存在 %d 个严重差异, 强制 dry-run",
                    recon.critical_count,
                )
                _send_alert(
                    f"[{profile}] 持仓严重差异",
                    f"critical={recon.critical_count}, 已强制 dry-run",
                    level="CRITICAL",
                )
                dry_run = True
            # 获取持仓价格
            held = [p["code"] for p in state.positions]
            prices = (
                fetch_prices(gw, held, trade_date, cfg=self.cfg)
                if held else {}
            )
            # NAV
            nav_result = _ops_calc_nav(
                state.cash, state.positions, prices,
                fallback=FallbackPolicy.AVG_COST,
            )
            nav = nav_result.nav
            state.peak_nav = max(state.peak_nav, nav)
            state.days_traded += 1

            # §13.15 / §4.3.1: 喂养风控约束状态（与 BacktestEngine 对齐）
            self._feed_risk_constraints(trade_date, nav, state)
            pos_value = sum(
                p["shares"] * prices.get(p["code"], p.get("avg_cost", 0))
                for p in state.positions
            )
            state.nav_history.append({
                "date": trade_date,
                "nav": round(nav, 2),
                "cash": round(state.cash, 2),
                "pos_value": round(pos_value, 2),
                "n_positions": len(state.positions),
            })
            logger.info(
                "NAV=%.2f  cash=%.2f  positions=%d",
                nav, state.cash, len(state.positions),
            )
            # MDD 检查
            risk = _check_mdd(state, nav, self.cfg, trade_date=trade_date)
            if risk == "halt":
                logger.error("MDD 安全阀触发, 暂停调仓")
                _send_alert(
                    f"[{profile}] MDD 安全阀",
                    f"NAV={nav:,.0f}, 峰值={state.peak_nav:,.0f}",
                    level="CRITICAL",
                )
                _write_audit_log(state, trade_date)
                save_live_state(state)
                return
            if dry_run:
                logger.info("[DRY-RUN] 跳过调仓, 仅更新 NAV")
                _write_audit_log(state, trade_date)
                save_live_state(state)
                return
            # 组件遍历调仓
            pre_trade_nav = nav
            components = _resolve_components(self.cfg, weights)
            for comp_name, comp_cfg in components:
                freq = comp_cfg.get("rebalance_freq", "every_5d")
                last_rb = state.last_rebalance.get(comp_name, "")
                if not _ops_should_rebalance(freq, trade_date, last_rb):
                    continue
                w = weights.get(comp_name, 0)
                target_value = nav * w
                logger.info("[%s] 调仓日, 开始打分...", comp_name)
                try:
                    self._execute_component_live(
                        state, comp_name, comp_cfg,
                        target_value, prices, trade_date, confirm,
                    )
                    state.last_rebalance[comp_name] = trade_date
                    save_live_state(state)
                except Exception as exc:
                    logger.error(
                        "[%s] 实盘调仓失败: %s", comp_name, exc,
                        exc_info=True,
                    )
                    _send_alert(
                        f"[{profile}] {comp_name} 实盘调仓失败",
                        str(exc)[:300],
                        level="ERROR",
                    )
            # Post-trade NAV 对账
            held_after = [p["code"] for p in state.positions]
            prices_after = (
                fetch_prices(gw, held_after, trade_date,
                             min_coverage=0.8, cfg=self.cfg)
                if held_after else {}
            )
            final_result = _ops_calc_nav(
                state.cash, state.positions, prices_after,
                fallback=FallbackPolicy.AVG_COST,
            )
            final_nav = final_result.nav
            state.peak_nav = max(state.peak_nav, final_nav)
            # NAV 漂移检测
            if pre_trade_nav > 0 and final_nav > 0:
                drift = abs(final_nav - pre_trade_nav) / pre_trade_nav
                if drift > 0.10:
                    _send_alert(
                        f"[{profile}] NAV 对账异常",
                        f"pre={pre_trade_nav:,.0f} → post={final_nav:,.0f}"
                        f" drift={drift:.2%}",
                        level="CRITICAL",
                    )
            _write_audit_log(state, trade_date)
            save_live_state(state)
            _notify_trades(
                profile, trade_date, state,
                pre_trade_nav, final_nav,
            )
            logger.info("实盘日终完成. NAV=%.2f", final_nav)
        except Exception as exc:
            logger.error("LiveEngine 异常: %s", exc, exc_info=True)
            _send_alert(
                f"[{profile}] 实盘引擎异常",
                str(exc)[:500],
                level="CRITICAL",
            )
            raise
        finally:
            self._disconnect_qmt()

    # -- QMT 连接 ---------------------------------------------------------
    def _ensure_qmt(self) -> Any:
        """懒初始化并连接 QMT 网关."""
        if self._qmt is not None:
            return self._qmt
        from aipha.execution.qmt_gateway import QMTGateway, QMTConfig
        qmt_cfg_path = _PROJ / "configs" / "live" / "qmt_gateway.yml"
        if qmt_cfg_path.exists():
            raw = yaml.safe_load(qmt_cfg_path.read_text(encoding="utf-8"))
            primary = raw.get("gateway", {}).get("primary", {})
            cfg = QMTConfig(
                name=primary.get("name", "qmt-live"),
                mini_qmt_path=os.environ.get(
                    "MINI_QMT_PATH", primary.get("mini_qmt_path", ""),
                ),
                account_id=os.environ.get(
                    "TRADING_ACCOUNT_ID", primary.get("account_id", ""),
                ),
                account_type=primary.get("account_type", "STOCK"),
                auto_reconnect=primary.get("auto_reconnect", True),
                event_poll_interval=primary.get("event_poll_interval", 0.5),
            )
        else:
            cfg = QMTConfig.from_env()
        gw = QMTGateway(cfg)
        gw.connect()
        self._qmt = gw
        logger.info("QMT 网关已连接: %s", cfg.account_id[:4] + "****")
        return gw

    def _disconnect_qmt(self) -> None:
        """断开 QMT 连接."""
        if self._qmt is not None:
            try:
                self._qmt.disconnect()
            except Exception as exc:
                logger.warning("QMT 断开异常: %s", exc)
            self._qmt = None

    # -- 持仓同步 ---------------------------------------------------------
    def _sync_positions_from_qmt(
        self, state: ComboState,
    ) -> ReconcileResult:
        """从 QMT 读取真实持仓并与内部状态对账."""
        qmt = self._ensure_qmt()
        # 1) 从 QMT 读取实际持仓
        raw_positions = qmt.query_positions()
        external: dict[str, PositionRecord] = {}
        for p in raw_positions:
            code = p.get("stock_code", "")
            vol = p.get("volume", 0)
            if code and vol > 0:
                external[code] = PositionRecord(
                    ts_code=code,
                    shares=vol,
                    avg_cost=p.get("avg_price", 0.0),
                    market_value=p.get("market_value", 0.0),
                    frozen_shares=p.get("frozen_volume", 0),
                )
        # 2) 将内部状态转为 PositionRecord
        internal: dict[str, PositionRecord] = {}
        for pos in state.positions:
            code = pos.get("code", "")
            if code:
                internal[code] = PositionRecord(
                    ts_code=code,
                    shares=pos.get("shares", 0),
                    avg_cost=pos.get("avg_cost", 0.0),
                )
        # 3) 对账
        syncer = AccountSyncer(share_tolerance=0, value_tolerance_pct=0.02)
        result = syncer.reconcile("qmt-live", internal, external)
        if not result.is_clean:
            logger.warning(
                "持仓对账不一致: diffs=%d internal_only=%s external_only=%s",
                len(result.diffs),
                result.internal_only, result.external_only,
            )
            _send_alert(
                f"[{self.profile}] 持仓对账差异",
                f"差异数: {len(result.diffs)}, "
                f"内部独有: {result.internal_only}, "
                f"QMT独有: {result.external_only}",
                level="WARNING",
            )
        # 4) 以 QMT 为准同步内部状态
        synced = syncer.apply_external(internal, external)
        state.positions = [
            {
                "code": rec.ts_code,
                "shares": rec.shares,
                "avg_cost": rec.avg_cost,
                "component": _find_component(state, rec.ts_code),
            }
            for rec in synced.values()
        ]
        # 5) 同步资金
        account = qmt.query_account()
        state.cash = account.get("cash", state.cash)
        logger.info(
            "持仓同步完成: %d 只持仓, cash=%.2f",
            len(state.positions), state.cash,
        )
        return result

    # -- 安全门控 ---------------------------------------------------------
    def _safety_preflight(self) -> bool:
        """实盘安全预检: 开关 / 风控限制 / 网关连接."""
        # 1) 全局开关
        settings_path = _PROJ / "configs" / "live" / "system_settings.json"
        if not settings_path.exists():
            logger.error("system_settings.json 不存在, 禁止实盘")
            return False
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        if not settings.get("live_trading_enabled", False):
            logger.error("live_trading_enabled=false, 实盘已禁用")
            return False
        # 2) 检查当前 profile 是否在已选组合中
        selected = settings.get("selected_portfolios", [])
        disabled = settings.get("disabled_portfolios", {})
        if self.profile in disabled:
            logger.error("组合 %s 已被禁用: %s",
                         self.profile, disabled[self.profile])
            return False
        if selected and self.profile not in selected:
            logger.warning("组合 %s 不在 selected_portfolios 中", self.profile)
        # 3) 风控限制
        risk = settings.get("risk_limits", {})
        self._risk_limits = risk
        # 4) QMT 连接测试
        try:
            qmt = self._ensure_qmt()
            account = qmt.query_account()
            total = account.get("total_asset", 0)
            if total <= 0:
                logger.error("QMT 账户总资产 <= 0, 中止")
                return False
            logger.info("QMT 账户: 总资产=%.2f, 可用=%.2f",
                        total, account.get("cash", 0))
        except Exception as exc:
            logger.error("QMT 连接/查询失败: %s", exc)
            return False
        return True

    # -- 实盘下单 ---------------------------------------------------------
    def _submit_live_orders(
        self,
        sells: list[dict],
        buys: list[dict],
        prices: dict[str, float],
        trade_date: str,
    ) -> list[dict]:
        """通过 QMT 发起实盘委托 (先卖后买).

        Returns:
            提交的订单记录列表
        """
        qmt = self._ensure_qmt()
        submitted: list[dict] = []
        # 风控: 单笔金额上限
        max_order_amt = self._risk_limits.get("max_order_amount", 2_000_000)
        order_interval = self._risk_limits.get("order_interval_seconds", 1.0)
        daily_traded = getattr(self, '_daily_traded_amount', 0.0)
        # 先卖
        for sell in sells:
            code = sell["code"]
            shares = sell["shares"]
            price = prices.get(code, 0)
            if price <= 0:
                logger.warning("卖出 %s 无价格, 跳过", code)
                continue
            amount = shares * price
            if amount > max_order_amt:
                logger.error(
                    "§14.1 卖出 %s 金额 %.0f > 上限 %.0f, 订单被拒绝",
                    code, amount, max_order_amt,
                )
                submitted.append({
                    "order_id": "", "code": code, "side": "SELL",
                    "shares": shares, "price": price,
                    "status": "rejected", "error": "fat_finger_amount_limit",
                })
                continue
            order = GatewayOrder(
                order_id=f"SELL_{code}_{trade_date}_{int(time.time()*1000)}",
                symbol=code,
                side="SELL",
                qty=shares,
                order_type="LIMIT",
                limit_price=price,
                metadata={
                    "strategy_name": self.profile,
                    "component": sell.get("component", ""),
                    "remark": f"EOD_SELL_{trade_date}",
                },
            )
            try:
                oid = qmt.submit_order(order)
                submitted.append({
                    "order_id": oid, "code": code, "side": "SELL",
                    "shares": shares, "price": price,
                    "component": sell.get("component", ""),
                    "status": "submitted",
                })
                logger.info("卖出委托: %s x%d @ %.3f → %s",
                            code, shares, price, oid)
            except Exception as exc:
                logger.error("卖出 %s 委托失败: %s", code, exc)
                submitted.append({
                    "order_id": "", "code": code, "side": "SELL",
                    "shares": shares, "price": price,
                    "status": "failed", "error": str(exc),
                })
            if order_interval > 0:
                time.sleep(order_interval)
        # 后买
        for buy in buys:
            code = buy["code"]
            shares = buy["shares"]
            price = prices.get(code, 0)
            if price <= 0:
                logger.warning("买入 %s 无价格, 跳过", code)
                continue
            amount = shares * price
            if amount > max_order_amt:
                logger.error(
                    "§14.1 买入 %s 金额 %.0f > 上限 %.0f, 订单被拒绝",
                    code, amount, max_order_amt,
                )
                submitted.append({
                    "order_id": "", "code": code, "side": "BUY",
                    "shares": shares, "price": price,
                    "status": "rejected", "error": "fat_finger_amount_limit",
                })
                continue
            order = GatewayOrder(
                order_id=f"BUY_{code}_{trade_date}_{int(time.time()*1000)}",
                symbol=code,
                side="BUY",
                qty=shares,
                order_type="LIMIT",
                limit_price=price,
                metadata={
                    "strategy_name": self.profile,
                    "component": buy.get("component", ""),
                    "remark": f"EOD_BUY_{trade_date}",
                },
            )
            try:
                oid = qmt.submit_order(order)
                submitted.append({
                    "order_id": oid, "code": code, "side": "BUY",
                    "shares": shares, "price": price,
                    "component": buy.get("component", ""),
                    "status": "submitted",
                })
                logger.info("买入委托: %s x%d @ %.3f → %s",
                            code, shares, price, oid)
            except Exception as exc:
                logger.error("买入 %s 委托失败: %s", code, exc)
                submitted.append({
                    "order_id": "", "code": code, "side": "BUY",
                    "shares": shares, "price": price,
                    "status": "failed", "error": str(exc),
                })
            if order_interval > 0:
                time.sleep(order_interval)
        return submitted

    # -- 单组件调仓 -------------------------------------------------------
    def _execute_component_live(
        self,
        state: ComboState,
        comp_name: str,
        comp_cfg: dict,
        target_value: float,
        prices: dict[str, float],
        trade_date: str,
        confirm: bool,
    ) -> None:
        """单组件实盘调仓: scorer → generate_order_intents → QMT 委托.

        使用 generate_order_intents() 共享决策层, 确保 T+N / 涨跌停 /
        cash_floor / 滑点 等安全检查与 Paper 完全一致 (§4.3).
        """
        gw = get_tushare_gateway()
        scorer_name = comp_cfg.get("scorer", comp_name)
        scorer_fn = get_scorer(scorer_name)
        picks = scorer_fn(gw, comp_cfg, trade_date)
        if not picks:
            logger.warning("[%s] 无选股结果, 跳过", comp_name)
            return
        # 获取目标价格
        new_codes = [c for c in picks if c not in prices]
        if new_codes:
            new_p = fetch_prices(gw, new_codes, trade_date,
                                 min_coverage=0.8, cfg=self.cfg)
            prices.update(new_p)
        # 覆盖率检查
        covered = [c for c in picks if c in prices and prices[c] > 0]
        cov = len(covered) / len(picks) if picks else 1.0
        if cov < 0.6:
            logger.error("[%s] 覆盖率 %.0f%% < 60%%, 跳过",
                         comp_name, cov * 100)
            return
        if cov < 0.8:
            logger.warning("[%s] 覆盖率 %.0f%%, 部分调仓",
                           comp_name, cov * 100)
            target_value = target_value * cov
            picks = covered
        per_stock = target_value / len(picks) if picks else 0.0
        # Fat-finger — 委托给共享层 generate_order_intents
        total_nav = state.cash + sum(
            p["shares"] * prices.get(p["code"], p.get("avg_cost", 0))
            for p in state.positions
        )
        # 获取前收盘价 (涨跌停检查必需)
        all_codes = list(set(
            [p["code"] for p in state.positions
             if p.get("component") == comp_name] + picks
        ))
        prev_closes = _ops_fetch_prev_closes(
            gw, all_codes, trade_date,
        ) if all_codes else {}
        # 共享决策: generate_order_intents (§4.3 统一执行)
        fp = get_fee_params_with_overrides(comp_name, comp_cfg)
        cash_floor = self.cfg.get("cash_reserve_pct", 0.02) * total_nav
        intent_result = generate_order_intents(
            current_positions=state.positions,
            target_codes=picks,
            prices=prices,
            per_stock_value=per_stock,
            trade_date=trade_date,
            fee_params=fp,
            component=comp_name,
            check_price_limits=True,
            prev_closes=prev_closes,
            cash_available=state.cash,
            cash_floor=cash_floor,
            total_nav=total_nav,
        )
        if intent_result.skipped:
            for s in intent_result.skipped[:5]:
                logger.info("[%s] 跳过 %s: %s",
                            comp_name, s["code"], s["reason"])

        # §13.15: 风控约束过滤（统一前交易管线 pretrade.py）
        intents_list = list(intent_result.intents)
        if self._risk_manager is not None and intents_list:
            from aipha.execution.pretrade import run_pretrade_checks

            pos_dict: dict[str, int] = {}
            for p in state.positions:
                pos_dict[p["code"]] = (
                    pos_dict.get(p["code"], 0) + p.get("shares", 0)
                )
            batch = [
                (i.code, i.shares, i.side) for i in intents_list
            ]
            pt_result = run_pretrade_checks(
                intents=batch,
                current_positions=pos_dict,
                nav=total_nav,
                prices=prices,
                risk_manager=self._risk_manager,
            )
            approved_map = {s: q for s, q, _ in pt_result.approved}
            filtered = []
            for intent in intents_list:
                if intent.code in approved_map:
                    adj_qty = approved_map[intent.code]
                    if adj_qty != intent.shares:
                        intent = type(intent)(
                            code=intent.code,
                            side=intent.side,
                            shares=adj_qty,
                            signal_price=intent.signal_price,
                            component=intent.component,
                            reason=intent.reason,
                        )
                        logger.info(
                            "[%s] 风控缩减 %s %s: %d→%d",
                            comp_name, intent.side, intent.code,
                            intent.shares, adj_qty,
                        )
                    filtered.append(intent)
                else:
                    logger.info(
                        "[%s] 风控拒绝 %s %s",
                        comp_name, intent.side, intent.code,
                    )
            intents_list = filtered

        # §13.15: 合规检查（停牌/涨跌停/参与率）
        if self._compliance_engine is not None and intents_list:
            from aipha.execution.pretrade import run_compliance_checks

            at = "cb" if comp_cfg.get("asset_type") == "convertible_bond" else "equity"
            batch = [(i.code, i.shares, i.side) for i in intents_list]
            cc_result = run_compliance_checks(
                intents=batch,
                prices=prices,
                compliance_engine=self._compliance_engine,
                portfolio_total=float(
                    sum(p.get("shares", 0) * prices.get(p["code"], 0)
                        for p in state.positions)
                    + state.cash
                ),
                asset_type=at,
            )
            if cc_result.rejected:
                cc_approved = {s for s, _, _ in cc_result.approved}
                pre_cc = len(intents_list)
                intents_list = [i for i in intents_list if i.code in cc_approved]
                logger.info(
                    "[%s] 合规过滤: %d→%d 笔",
                    comp_name, pre_cc, len(intents_list),
                )

        # 转换 OrderIntent → sells/buys dict (for _submit_live_orders)
        sells: list[dict] = []
        buys: list[dict] = []
        for intent in intents_list:
            entry = {
                "code": intent.code,
                "shares": intent.shares,
                "component": intent.component,
            }
            if intent.side == "sell":
                sells.append(entry)
            else:
                buys.append(entry)
        if not sells and not buys:
            logger.info("[%s] 无需调仓", comp_name)
            return

        # §14.1: Fat-finger 四重防护 (P0-1 修复: Live 路径必须调用)
        try:
            from aipha.execution.pretrade import run_fat_finger_checks
            from aipha.execution.trading_ops import fetch_ma5_prices

            # Layer ②: 获取 MA5 均价
            buy_codes = [b["code"] for b in buys]
            ma5_dict: dict[str, float] = {}
            if buy_codes:
                try:
                    ma5_dict = fetch_ma5_prices(gw, buy_codes, trade_date)
                except Exception as ma5_exc:
                    logger.warning("[%s] MA5 获取失败, Layer ② 跳过: %s", comp_name, ma5_exc)

            ff_batch = [(i.code, i.shares, i.side) for i in intents_list]
            ff_result = run_fat_finger_checks(
                intents=ff_batch,
                prices=prices,
                nav=total_nav,
                ma5=ma5_dict if ma5_dict else None,
                daily_cumulative_traded=getattr(
                    self, '_daily_traded_amount', 0.0
                ),
            )
            if ff_result.rejected:
                ff_approved = {s for s, _, _ in ff_result.approved}
                pre_ff = len(sells) + len(buys)
                sells = [s for s in sells if s["code"] in ff_approved]
                buys = [b for b in buys if b["code"] in ff_approved]
                logger.warning(
                    "[%s] §14.1 Fat-finger 拦截: %d→%d 笔 (拒绝: %s)",
                    comp_name, pre_ff, len(sells) + len(buys),
                    [r.symbol for r in ff_result.rejected],
                )
        except ImportError:
            logger.warning("[%s] pretrade.run_fat_finger_checks 不可用", comp_name)
        except Exception as exc:
            # fat-finger 检查异常 → 安全优先: 拒绝全部订单
            logger.error(
                "[%s] §14.1 Fat-finger 检查异常, 拒绝全部: %s", comp_name, exc
            )
            return

        if not sells and not buys:
            logger.info("[%s] fat-finger 过滤后无订单", comp_name)
            return
        # 打印调仓计划
        logger.info(
            "[%s] 调仓计划: 卖出 %d 笔, 买入 %d 笔",
            comp_name, len(sells), len(buys),
        )
        for s in sells:
            logger.info("  SELL %s x%d", s["code"], s["shares"])
        for b in buys:
            logger.info("  BUY  %s x%d", b["code"], b["shares"])
        # 确认模式
        if confirm:
            ans = input(
                f"[{comp_name}] 确认执行? (y/N): ").strip().lower()
            if ans != "y":
                logger.info("[%s] 用户取消", comp_name)
                return
        # 提交
        submitted = self._submit_live_orders(
            sells, buys, prices, trade_date,
        )
        # 记录交易
        for order in submitted:
            state.trades.append({
                "date": trade_date,
                "code": order["code"],
                "side": order["side"],
                "price": order.get("price", 0),
                "shares": order.get("shares", 0),
                "component": order.get("component", comp_name),
                "order_id": order.get("order_id", ""),
                "status": order.get("status", "unknown"),
            })
        logger.info("[%s] 实盘调仓完成, %d 笔委托",
                    comp_name, len(submitted))


# =====================================================================
# CLI
# =====================================================================
def main() -> None:
    """CLI 入口."""
    parser = argparse.ArgumentParser(
        description="实盘 EOD 引擎 — 读取 QMT 持仓, 策略调仓, 发起委托",
    )
    parser.add_argument(
        "--config", "-c", required=True,
        help="YAML 配置文件路径",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="仅更新 NAV + 对账, 不下单",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="每个组件调仓前需人工确认",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="输出 DEBUG 级别日志",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    profile = cfg.get("profile_name", "live_combo")
    _setup_logging(profile, verbose=args.verbose)

    engine = LiveEngineEOD(args.config)
    engine.run(dry_run=args.dry_run, confirm=args.confirm)


def _setup_logging(profile: str, verbose: bool = False) -> None:
    """配置文件 + 控制台双输出日志."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    root = logging.getLogger()
    root.setLevel(level)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(ch)
    today = datetime.now(_CN_TZ).strftime("%Y%m%d")
    fh = logging.FileHandler(
        _LOG_DIR / f"{profile}_{today}.log", encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(fh)

    # §14.2 告警即生命线：WARNING+ 自动推送飞书
    from aipha.monitor.alert_handler import setup_alert_logging
    setup_alert_logging()


if __name__ == "__main__":
    main()
