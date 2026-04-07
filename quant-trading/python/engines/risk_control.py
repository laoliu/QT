"""
risk_control — Live trading risk control middleware (交易风控).

Provides:
  - Position limits (单票持仓上限)
  - Daily loss limits (单日亏损上限)
  - Order frequency limits (下单频率限制)
  - Drawdown circuit breaker (最大回撤熔断)
  - Sector concentration limits (板块集中度限制)
  - Pre-trade risk check (下单前风控检查)

Called from TypeScript live_trading tool or directly by live_engine.
Input:
  {
    "action": "check_order" | "status" | "set_limits" | "reset",
    "order": { "symbol", "direction", "quantity", "price" },
    "portfolio": { "totalAssets", "positions": [...] },
    "limits": { ... }
  }

Output:
  check_order → { allowed: bool, reason?, warnings? }
  status      → { limits, currentExposure, breaches }
  set_limits  → { ok: true }
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from typing import Any, Optional


@dataclass
class RiskLimits:
    """Configurable risk limits."""
    max_position_pct: float = 20.0       # Max single stock % of total assets
    max_sector_pct: float = 40.0         # Max single sector % of total assets
    max_daily_loss_pct: float = 3.0      # Max daily loss % of total assets
    max_drawdown_pct: float = 10.0       # Max drawdown from peak
    max_orders_per_minute: int = 5       # Max orders per minute
    max_orders_per_day: int = 50         # Max orders per day
    min_order_interval_sec: float = 3.0  # Min seconds between orders
    max_single_order_pct: float = 10.0   # Max single order % of total assets
    blacklist: list[str] = field(default_factory=list)  # Blacklisted symbols
    whitelist: list[str] = field(default_factory=list)  # If set, only these are allowed


class RiskController:
    """Stateful risk controller for live trading sessions."""

    def __init__(self, limits: Optional[RiskLimits] = None):
        self.limits = limits or RiskLimits()
        self._order_timestamps: list[float] = []
        self._daily_orders: int = 0
        self._daily_pnl: float = 0.0
        self._peak_equity: float = 0.0
        self._current_equity: float = 0.0
        self._last_order_time: float = 0.0
        self._today: str = date.today().isoformat()
        self._breaches: list[dict] = []

    def _reset_daily(self) -> None:
        """Reset daily counters if date changed."""
        today = date.today().isoformat()
        if today != self._today:
            self._today = today
            self._daily_orders = 0
            self._daily_pnl = 0.0
            self._order_timestamps.clear()

    def update_equity(self, equity: float) -> None:
        """Update current equity for drawdown tracking."""
        self._current_equity = equity
        if equity > self._peak_equity:
            self._peak_equity = equity

    def check_order(self, order: dict, portfolio: dict) -> dict:
        """Pre-trade risk check. Returns {allowed, reason, warnings}."""
        self._reset_daily()

        symbol = order.get("symbol", "")
        direction = order.get("direction", "buy")
        quantity = order.get("quantity", 0)
        price = order.get("price", 0)
        order_value = quantity * price

        total_assets = portfolio.get("totalAssets", 0)
        positions = portfolio.get("positions", [])

        warnings: list[str] = []

        # 1. Blacklist check
        if symbol in self.limits.blacklist:
            return {"allowed": False, "reason": f"股票 {symbol} 在黑名单中"}

        # 2. Whitelist check
        if self.limits.whitelist and symbol not in self.limits.whitelist:
            return {"allowed": False, "reason": f"股票 {symbol} 不在白名单中"}

        # 3. Single order size check
        if total_assets > 0 and order_value > 0:
            order_pct = order_value / total_assets * 100
            if order_pct > self.limits.max_single_order_pct:
                return {
                    "allowed": False,
                    "reason": f"单笔订单占总资产 {order_pct:.1f}%，超过限制 {self.limits.max_single_order_pct}%",
                }

        # 4. Position concentration check (only for buys)
        if direction == "buy" and total_assets > 0:
            existing_value = 0
            for pos in positions:
                if str(pos.get("symbol", "")) == symbol:
                    existing_value = pos.get("marketValue", 0) or (
                        pos.get("quantity", 0) * pos.get("currentPrice", 0)
                    )
                    break
            new_position_value = existing_value + order_value
            position_pct = new_position_value / total_assets * 100
            if position_pct > self.limits.max_position_pct:
                return {
                    "allowed": False,
                    "reason": f"持仓 {symbol} 将达 {position_pct:.1f}%，超过限制 {self.limits.max_position_pct}%",
                }
            if position_pct > self.limits.max_position_pct * 0.8:
                warnings.append(f"持仓 {symbol} 接近上限: {position_pct:.1f}%")

        # 5. Order frequency check
        now = time.time()
        # Clean old timestamps
        self._order_timestamps = [t for t in self._order_timestamps if now - t < 60]

        if len(self._order_timestamps) >= self.limits.max_orders_per_minute:
            return {
                "allowed": False,
                "reason": f"每分钟下单次数({len(self._order_timestamps)})已达上限({self.limits.max_orders_per_minute})",
            }

        if now - self._last_order_time < self.limits.min_order_interval_sec:
            return {
                "allowed": False,
                "reason": f"下单间隔太短，请等待 {self.limits.min_order_interval_sec:.0f} 秒",
            }

        # 6. Daily order count
        if self._daily_orders >= self.limits.max_orders_per_day:
            return {
                "allowed": False,
                "reason": f"今日下单次数({self._daily_orders})已达上限({self.limits.max_orders_per_day})",
            }

        # 7. Daily loss limit
        if total_assets > 0 and self._daily_pnl < 0:
            loss_pct = abs(self._daily_pnl) / total_assets * 100
            if loss_pct >= self.limits.max_daily_loss_pct:
                return {
                    "allowed": False,
                    "reason": f"今日亏损 {loss_pct:.1f}% 已达上限 {self.limits.max_daily_loss_pct}%，停止交易",
                }
            if loss_pct >= self.limits.max_daily_loss_pct * 0.7:
                warnings.append(f"今日亏损接近上限: {loss_pct:.1f}%")

        # 8. Max drawdown check
        if self._peak_equity > 0 and self._current_equity > 0:
            drawdown = (self._peak_equity - self._current_equity) / self._peak_equity * 100
            if drawdown >= self.limits.max_drawdown_pct:
                return {
                    "allowed": False,
                    "reason": f"最大回撤 {drawdown:.1f}% 已达熔断线 {self.limits.max_drawdown_pct}%",
                }
            if drawdown >= self.limits.max_drawdown_pct * 0.7:
                warnings.append(f"回撤接近熔断线: {drawdown:.1f}%")

        # All checks passed — record this order
        self._order_timestamps.append(now)
        self._last_order_time = now
        self._daily_orders += 1

        result: dict[str, Any] = {"allowed": True}
        if warnings:
            result["warnings"] = warnings
        return result

    def record_pnl(self, pnl: float) -> None:
        """Record realized P&L for daily tracking."""
        self._reset_daily()
        self._daily_pnl += pnl

    def get_status(self, portfolio: dict) -> dict:
        """Get current risk status."""
        self._reset_daily()
        total_assets = portfolio.get("totalAssets", 0)
        positions = portfolio.get("positions", [])

        # Position concentration
        position_exposures = []
        for pos in positions:
            value = pos.get("marketValue", 0) or (
                pos.get("quantity", 0) * pos.get("currentPrice", 0)
            )
            pct = value / total_assets * 100 if total_assets > 0 else 0
            position_exposures.append({
                "symbol": pos.get("symbol", ""),
                "name": pos.get("name", ""),
                "marketValue": value,
                "pct": round(pct, 2),
                "overLimit": pct > self.limits.max_position_pct,
            })

        position_exposures.sort(key=lambda p: p["pct"], reverse=True)

        # Drawdown
        drawdown = 0
        if self._peak_equity > 0 and self._current_equity > 0:
            drawdown = round(
                (self._peak_equity - self._current_equity) / self._peak_equity * 100, 2
            )

        daily_loss_pct = 0
        if total_assets > 0 and self._daily_pnl < 0:
            daily_loss_pct = round(abs(self._daily_pnl) / total_assets * 100, 2)

        return {
            "action": "status",
            "limits": asdict(self.limits),
            "dailyOrders": self._daily_orders,
            "dailyPnl": round(self._daily_pnl, 2),
            "dailyLossPct": daily_loss_pct,
            "drawdown": drawdown,
            "peakEquity": round(self._peak_equity, 2),
            "currentEquity": round(self._current_equity, 2),
            "positionExposures": position_exposures[:10],
            "breaches": self._breaches[-10:],
        }


# ── Singleton for the running session ────────────────────────────

_CONTROLLER: RiskController | None = None


def _get_controller() -> RiskController:
    global _CONTROLLER
    if _CONTROLLER is None:
        _CONTROLLER = RiskController()
    return _CONTROLLER


def run_risk_control(config: dict) -> dict:
    """Dispatch risk control actions."""
    action = config.get("action", "status")

    if action == "check_order":
        order = config.get("order", {})
        portfolio = config.get("portfolio", {})
        if not order:
            return {"error": "请提供 order 参数"}
        ctrl = _get_controller()
        if portfolio.get("totalAssets"):
            ctrl.update_equity(portfolio["totalAssets"])
        return ctrl.check_order(order, portfolio)

    elif action == "status":
        portfolio = config.get("portfolio", {})
        ctrl = _get_controller()
        if portfolio.get("totalAssets"):
            ctrl.update_equity(portfolio["totalAssets"])
        return ctrl.get_status(portfolio)

    elif action == "set_limits":
        limits_cfg = config.get("limits", {})
        ctrl = _get_controller()
        for key, val in limits_cfg.items():
            if hasattr(ctrl.limits, key):
                setattr(ctrl.limits, key, val)
        return {"ok": True, "limits": asdict(ctrl.limits)}

    elif action == "reset":
        global _CONTROLLER
        _CONTROLLER = RiskController()
        return {"ok": True, "message": "风控参数已重置"}

    elif action == "record_pnl":
        pnl = config.get("pnl", 0)
        ctrl = _get_controller()
        ctrl.record_pnl(pnl)
        return {"ok": True, "dailyPnl": round(ctrl._daily_pnl, 2)}

    else:
        return {"error": f"未知action: {action}"}


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_risk_control(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
