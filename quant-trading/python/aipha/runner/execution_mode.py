"""ExecutionMode Protocol — Phase R4.

ADR-V2-8: PaperMode 薄包装 ComboEODRunner (零修改 Paper 路径),
           LiveMode 持有 ComboState + LiveDelegate。
_run_daily_inner 无 if-live 分支，统一通过 ExecutionMode 交互。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── 共享数据结构 ─────────────────────────────────────────────
@dataclass
class FillResult:
    """fill_pending_and_get_nav() 返回值."""
    fills: int = 0
    nav: float = 0.0


@dataclass
class RebalanceResult:
    """execute_rebalance() 返回值."""
    nav: float = 0.0
    component_results: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ── Protocol ─────────────────────────────────────────────────
@runtime_checkable
class ExecutionMode(Protocol):
    """Paper / Live 执行模式统一接口.

    combo_orchestrator._run_daily_inner 通过此接口交互，
    无需关心底层是模拟撮合还是 QMT 委托。
    """

    def fill_pending_and_get_nav(
        self, trade_date: date, bars: dict[str, Any],
    ) -> FillResult:
        """撮合/同步待成交订单，返回当前 NAV."""
        ...

    def execute_rebalance(
        self, trade_date: date, bars: dict[str, Any],
        component_picks: dict[str, list[str]],
    ) -> RebalanceResult:
        """执行调仓（Paper 撮合 / Live 委托）."""
        ...

    def save_state(self, path: Path) -> None:
        """持久化状态."""
        ...

    def get_positions_codes(self) -> list[str]:
        """返回当前持仓的标的代码列表."""
        ...


# ── PaperMode ────────────────────────────────────────────────
class PaperMode:
    """薄包装 ComboEODRunner — Paper 路径真正零修改."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner

    def fill_pending_and_get_nav(
        self, trade_date: date, bars: dict[str, Any],
    ) -> FillResult:
        result = self._runner.run_day(trade_date, bars, component_picks=None)
        return FillResult(fills=result.fills, nav=result.nav)

    def execute_rebalance(
        self, trade_date: date, bars: dict[str, Any],
        component_picks: dict[str, list[str]],
    ) -> RebalanceResult:
        result = self._runner.run_day(trade_date, bars, component_picks)
        return RebalanceResult(
            nav=result.nav,
            component_results=result.component_results,
            errors=result.errors,
        )

    def save_state(self, path: Path) -> None:
        self._runner.save_state(path)

    def get_positions_codes(self) -> list[str]:
        codes: set[str] = set()
        for pm in self._runner.position_managers.values():
            codes.update(pm.get_codes())
        # P0 fix: include pending order codes so bars are fetched for fill
        for gw in self._runner.gateways.values():
            codes.update(gw.get_pending_order_codes())
        return list(codes)


# ── LiveMode ─────────────────────────────────────────────────
class LiveMode:
    """持有 ComboState + LiveDelegate — Live 路径."""

    def __init__(self, state: Any, delegate: Any, cfg: dict,
                 gw: Any | None = None) -> None:
        from aipha.runner.daily_utils import ComboState
        from aipha.runner.live_delegate import LiveDelegate

        self._state: ComboState = state
        self._delegate: LiveDelegate = delegate
        self._cfg = cfg
        self._gw = gw

    def fill_pending_and_get_nav(
        self, trade_date: date, bars: dict[str, Any],
    ) -> FillResult:
        """Live: 从 QMT 同步持仓 + 计算 NAV."""
        from aipha.execution.trading_ops import calc_nav, FallbackPolicy

        # 同步 QMT 真实持仓
        self._delegate.sync_positions(self._state)
        # NAV 从真实持仓计算
        prices = {code: bar.close for code, bar in bars.items()
                  if hasattr(bar, "close")}
        nav_result = calc_nav(
            self._state.cash, self._state.positions, prices,
            fallback=FallbackPolicy.AVG_COST,
        )
        return FillResult(fills=0, nav=nav_result.nav)

    def execute_rebalance(
        self, trade_date: date, bars: dict[str, Any],
        component_picks: dict[str, list[str]],
    ) -> RebalanceResult:
        """Live: component_rebalance → intents → submit_orders."""
        from aipha.execution.trading_ops import (
            build_market_state,
            calc_nav,
            FallbackPolicy,
        )
        from aipha.runner.component_rebalance import execute_component_rebalance

        prices = {code: bar.close for code, bar in bars.items()
                  if hasattr(bar, "close")}
        nav_result = calc_nav(
            self._state.cash, self._state.positions, prices,
            fallback=FallbackPolicy.AVG_COST,
        )
        total_nav = nav_result.nav
        errors: list[str] = []
        comp_results: dict[str, Any] = {}

        components = self._cfg.get("components", [])
        for comp_cfg in components:
            comp_name = comp_cfg.get("name", comp_cfg.get("component", ""))
            picks = component_picks.get(comp_name)
            if picks is None:
                continue  # 无调仓信号
            weight = comp_cfg.get("weight", 0.0)
            target_value = total_nav * weight

            try:
                intents = execute_component_rebalance(
                    gw=self._gw,  # Live: 使用构造时传入的 gw
                    state=self._state,
                    comp_name=comp_name,
                    comp_cfg=comp_cfg,
                    target_value=target_value,
                    prices=prices,
                    trade_date=trade_date.strftime("%Y%m%d"),
                    cfg=self._cfg,
                    market_state_provider=build_market_state,
                )
                # 拆分 sell / buy
                sells = [i for i in intents if i.get("side") == "sell"]
                buys = [i for i in intents if i.get("side") == "buy"]
                submitted = self._delegate.submit_orders(
                    sells, buys, prices,
                    trade_date.strftime("%Y%m%d"),
                )
                comp_results[comp_name] = {
                    "intents": len(intents),
                    "submitted": len(submitted),
                }
            except Exception as exc:
                logger.error("[%s] 实盘调仓失败: %s", comp_name, exc)
                errors.append(f"{comp_name}: {exc}")

        return RebalanceResult(
            nav=total_nav,
            component_results=comp_results,
            errors=errors,
        )

    def save_state(self, path: Path) -> None:
        from dataclasses import asdict
        from aipha.execution.state_manager import save_state_json_atomic
        data = asdict(self._state)
        path.parent.mkdir(parents=True, exist_ok=True)
        save_state_json_atomic(path, data)

    def get_positions_codes(self) -> list[str]:
        return list({p.get("code", "") for p in self._state.positions
                     if p.get("code")})
