"""实盘委托代理 — Phase R3.

从 live_engine_eod.py 抽取 QMT 特有逻辑:
  - 连接/断开 QMT 网关
  - 三层安全门控
  - 持仓同步 (QMT → ComboState)
  - 实盘下单 (先卖后买, 限价单)

LiveDelegate 是 ExecutionMode Protocol (Phase R4) 的 Live 侧依赖。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import yaml

from aipha.runner.daily_utils import ComboState, _send_alert
from aipha.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_PROJ = PROJECT_ROOT


class LiveDelegate:
    """QMT 实盘委托代理."""

    def __init__(self, cfg: dict) -> None:
        self._cfg = cfg
        self._profile = cfg.get("profile_name", "unknown")
        self._qmt: Any | None = None
        self._risk_limits: dict = {}

    # -- QMT 连接 ---------------------------------------------------------
    def _ensure_qmt(self) -> Any:
        """懒初始化并连接 QMT 网关."""
        if self._qmt is not None:
            return self._qmt
        from aipha.execution.qmt_gateway import QMTConfig, QMTGateway

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

    def disconnect(self) -> None:
        """断开 QMT 连接."""
        if self._qmt is not None:
            try:
                self._qmt.disconnect()
            except Exception as exc:
                logger.warning("QMT 断开异常: %s", exc)
            self._qmt = None

    # -- 安全门控 ---------------------------------------------------------
    def safety_preflight(self) -> bool:
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
        # 2) 检查 profile
        disabled = settings.get("disabled_portfolios", {})
        if self._profile in disabled:
            logger.error("组合 %s 已被禁用: %s",
                         self._profile, disabled[self._profile])
            return False
        selected = settings.get("selected_portfolios", [])
        if selected and self._profile not in selected:
            logger.warning("组合 %s 不在 selected_portfolios 中", self._profile)
        # 3) 风控限制
        self._risk_limits = settings.get("risk_limits", {})
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

    # -- 持仓同步 ---------------------------------------------------------
    def sync_positions(self, state: ComboState) -> Any:
        """从 QMT 读取真实持仓并与内部状态对账."""
        from aipha.execution.qmt_account_sync import (
            AccountSyncer, PositionRecord, ReconcileResult,
        )
        qmt = self._ensure_qmt()
        # 1) QMT 实际持仓
        raw_positions = qmt.query_positions()
        external: dict[str, PositionRecord] = {}
        for p in raw_positions:
            code = p.get("stock_code", "")
            vol = p.get("volume", 0)
            if code and vol > 0:
                external[code] = PositionRecord(
                    ts_code=code, shares=vol,
                    avg_cost=p.get("avg_price", 0.0),
                    market_value=p.get("market_value", 0.0),
                    frozen_shares=p.get("frozen_volume", 0),
                )
        # 2) 内部状态
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
                f"[{self._profile}] 持仓对账差异",
                f"差异数: {len(result.diffs)}, "
                f"内部独有: {result.internal_only}, "
                f"QMT独有: {result.external_only}",
                level="WARNING",
            )
        # 4) 以 QMT 为准
        synced = syncer.apply_external(internal, external)
        state.positions = [
            {
                "code": rec.ts_code, "shares": rec.shares,
                "avg_cost": rec.avg_cost,
                "component": self._find_component(state, rec.ts_code),
            }
            for rec in synced.values()
        ]
        # 5) 同步资金
        account = qmt.query_account()
        state.cash = account.get("cash", state.cash)
        logger.info("持仓同步完成: %d 只持仓, cash=%.2f",
                     len(state.positions), state.cash)
        return result

    @staticmethod
    def _find_component(state: ComboState, ts_code: str) -> str:
        for p in state.positions:
            if p.get("code") == ts_code:
                return p.get("component", "")
        return ""

    # -- 实盘下单 ---------------------------------------------------------
    def submit_orders(
        self,
        sells: list[dict],
        buys: list[dict],
        prices: dict[str, float],
        trade_date: str,
    ) -> list[dict]:
        """通过 QMT 发起实盘委托 (先卖后买, 限价单).

        Returns:
            提交的订单记录列表
        """
        from aipha.execution.gateway import GatewayOrder

        qmt = self._ensure_qmt()
        submitted: list[dict] = []
        max_order_amt = self._risk_limits.get("max_order_amount", 2_000_000)
        order_interval = self._risk_limits.get("order_interval_seconds", 1.0)

        for side_label, orders, side_tag in [
            ("卖出", sells, "SELL"), ("买入", buys, "BUY"),
        ]:
            for o in orders:
                code = o["code"]
                shares = o["shares"]
                price = prices.get(code, 0)
                if price <= 0:
                    logger.warning("%s %s 无价格, 跳过", side_label, code)
                    continue
                amount = shares * price
                if amount > max_order_amt:
                    logger.warning(
                        "%s %s 金额 %.0f > 上限 %.0f, 拆单",
                        side_label, code, amount, max_order_amt,
                    )
                order = GatewayOrder(
                    order_id=f"{side_tag}_{code}_{trade_date}_"
                             f"{int(time.time()*1000)}",
                    symbol=code, side=side_tag, qty=shares,
                    order_type="LIMIT", limit_price=price,
                    metadata={
                        "strategy_name": self._profile,
                        "component": o.get("component", ""),
                        "remark": f"EOD_{side_tag}_{trade_date}",
                    },
                )
                try:
                    oid = qmt.submit_order(order)
                    submitted.append({
                        "order_id": oid, "code": code, "side": side_tag,
                        "shares": shares, "price": price,
                        "component": o.get("component", ""),
                        "status": "submitted",
                    })
                    logger.info("%s委托: %s x%d @ %.3f → %s",
                                side_label, code, shares, price, oid)
                except Exception as exc:
                    logger.error("%s %s 委托失败: %s", side_label, code, exc)
                    submitted.append({
                        "order_id": "", "code": code, "side": side_tag,
                        "shares": shares, "price": price,
                        "status": "failed", "error": str(exc),
                    })
                if order_interval > 0:
                    time.sleep(order_interval)
        return submitted
