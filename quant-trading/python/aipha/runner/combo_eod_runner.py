"""多组件 EOD 组合执行引擎。

设计：
- 一个共享 Account（统一资金池）
- 每个组件独立的 PaperGateway + PositionManager（资产类型隔离）
- 组件级调仓频率控制 + 权重分配
- 原子化状态持久化

日执行周期：
  1. fill_pending()   — 各组件网关撮合昨日挂单
  2. update_nav()     — 汇总所有组件持仓市值 + 现金
  3. rebalance()      — 按权重逐组件调仓（卖出优先释放资金）
  4. day_end()        — 各组件 T+N 解锁
  5. save_state()     — 原子化持久化

§4.3: 回测-Paper-Live 一致性契约
§13.7: 共享基础设施
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from aipha.domain.models import Bar, Side
from aipha.execution.fees import FeeModelCNEquity
from aipha.execution.fill_models import NextBarOpenFillModel, get_fill_model
from aipha.execution.gateway import GatewayOrder, OrderStatus
from aipha.execution.paper_gateway import PaperGateway
from aipha.execution.slippage import create_slippage_model
from aipha.portfolio.account import Account
from aipha.portfolio.position_manager import PositionManager

try:
    from aipha.risk.audit_log import AuditLogger, AuditLogConfig
    HAS_AUDIT_LOG = True
except ImportError:
    HAS_AUDIT_LOG = False

if TYPE_CHECKING:
    from aipha.portfolio.compliance_engine import ComplianceEngine
    from aipha.risk.constraints import CompositeRiskManager

logger = logging.getLogger(__name__)


def normalize_rebalance_freq(value: int | str | None) -> int:
    """Normalize legacy string frequencies to trade-day intervals."""
    if value is None:
        return 1

    if isinstance(value, int):
        return max(1, value)

    raw = str(value).strip().lower()
    if not raw:
        return 1
    if raw.isdigit():
        return max(1, int(raw))

    aliases = {
        "daily": 1,
        "weekly": 5,
        "biweekly": 10,
        "monthly": 20,
        "quarterly": 60,
    }
    if raw in aliases:
        return aliases[raw]

    match = re.fullmatch(r"every_(\d+)d", raw)
    if match:
        return max(1, int(match.group(1)))

    raise ValueError(f"unsupported rebalance_freq: {value!r}")


@dataclass
class ComponentConfig:
    """单个组件配置。"""

    name: str  # "ml", "cb", "etf"
    asset_type: str  # "cn_equity", "convertible_bond", "etf"
    weight: float  # 0.55, 0.20, 0.25
    rebalance_freq: int | str = 1  # 交易日间隔（1=daily, 5=weekly）
    lot_size: int = 100
    slippage_bps: float = -1.0
    slippage_model_name: str = "fixed"
    slippage_params: dict[str, Any] = field(default_factory=dict)
    # §13.1: 费率默认值由 __post_init__ 从 MarketRules 派生
    commission_rate: float = 0.0
    commission_min: float = 0.0
    # §F-1/F-3: FillModel + T+N 配置驱动（-1 表示自动从 MarketRules 派生）
    fill_model_name: str = "next_bar_open"
    t_plus_n: int = -1
    extra: dict[str, Any] = field(default_factory=dict)

    # §13.1: asset_type 标准化映射 — 别名→标准值
    _ASSET_TYPE_NORMALIZE: ClassVar[dict[str, str]] = {
        "cn_equity": "cn_equity",
        "a_stock": "cn_equity",
        "convertible_bond": "convertible_bond",
        "cb": "convertible_bond",
        "etf": "etf",
        "etf_cross": "etf",
    }

    def __post_init__(self) -> None:
        """从 MarketRules 派生费率/T+N 默认值 (§13.1 单一来源)."""
        # §13.1: 标准化 asset_type (a_stock→cn_equity, cb→convertible_bond)
        self.asset_type = self._ASSET_TYPE_NORMALIZE.get(
            self.asset_type, self.asset_type
        )
        self.rebalance_freq = normalize_rebalance_freq(self.rebalance_freq)

        rules = _get_market_rules(self.asset_type)
        if self.commission_rate == 0.0 and self.commission_min == 0.0:
            self.commission_rate = float(rules.commission_rate)
            self.commission_min = float(rules.commission_min)
        if self.slippage_bps < 0:
            self.slippage_bps = float(rules.slippage_bps_default)
        # §F-3: T+N 从 MarketRules.t_plus 派生
        if self.t_plus_n < 0:
            self.t_plus_n = int(getattr(rules, 't_plus', 1))


@dataclass
class ComboNavRecord:
    """组合 NAV 记录。"""

    trade_date: str
    nav: float
    cash: float
    positions_value: float
    component_values: dict[str, float]
    n_positions: int


@dataclass
class ComponentResult:
    """单组件调仓结果。"""

    component: str
    sells: int = 0
    buys: int = 0
    skipped: int = 0


@dataclass
class ComboDayResult:
    """组合单日执行结果。"""

    trade_date: str
    fills: int = 0
    nav: float = 0.0
    component_results: dict[str, ComponentResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Factory: 按资产类型创建 PaperGateway 组件
# ---------------------------------------------------------------------------

def _get_market_rules(asset_type: str):
    """按资产类型返回 MarketRules 实例。"""
    if asset_type == "convertible_bond":
        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond
        return MarketRulesConvertibleBond()
    if asset_type == "etf":
        from aipha.market.rules_etf import MarketRulesETF
        return MarketRulesETF()
    # 默认 A 股
    from aipha.market.rules_cn_equity import MarketRulesCNEquity
    return MarketRulesCNEquity()


def _create_fee_model(cfg: ComponentConfig) -> FeeModelCNEquity:
    """按组件配置创建费用模型。

    §13.7: 委托 trading_ops.create_fee_model() 共享工厂。
    """
    from aipha.execution.trading_ops import create_fee_model
    return create_fee_model(
        cfg.asset_type,
        commission_rate=cfg.commission_rate if cfg.commission_rate > 0 else None,
        commission_min=cfg.commission_min if cfg.commission_min > 0 else None,
    )


def create_component_gateway(
    cfg: ComponentConfig,
    account: Account,
) -> tuple[PaperGateway, PositionManager]:
    """创建组件级 PaperGateway + PositionManager。

    所有组件共享同一 Account（统一资金池），
    各自拥有独立的 PositionManager（T+N 隔离）。
    """
    rules = _get_market_rules(cfg.asset_type)
    pm = PositionManager(default_t_plus_n=cfg.t_plus_n)
    fill_model = get_fill_model(cfg.fill_model_name)
    fee_model = _create_fee_model(cfg)
    slippage_model = create_slippage_model(
        cfg.slippage_model_name,
        slippage_bps=cfg.slippage_bps,
        slippage_params=cfg.slippage_params,
    )
    gw = PaperGateway(
        account=account, position_mgr=pm,
        fill_model=fill_model, fee_model=fee_model,
        slippage_model=slippage_model, rules=rules,
    )
    gw.connect()
    return gw, pm


# ---------------------------------------------------------------------------
# ComboEODRunner — 多组件 EOD 执行引擎
# ---------------------------------------------------------------------------

class ComboEODRunner:
    """多组件 EOD 组合执行引擎。

    每个组件拥有独立的 PaperGateway + PositionManager，
    共享同一 Account（统一资金池）。

    Args:
        account: 共享资金账户
        components: 组件配置列表
        state_dir: 状态持久化目录
        cash_reserve_pct: 现金保留比例（防止调仓耗尽）
    """

    def __init__(
        self,
        *,
        account: Account,
        components: list[ComponentConfig],
        state_dir: Path | None = None,
        cash_reserve_pct: float = 0.03,
        risk_manager: CompositeRiskManager | None = None,
        compliance_engine: ComplianceEngine | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self._account = account
        self._components = {c.name: c for c in components}
        self._state_dir = state_dir
        self._cash_reserve_pct = cash_reserve_pct
        self._risk_manager = risk_manager
        self._compliance_engine = compliance_engine
        self._audit_logger = audit_logger

        # 创建每个组件的 Gateway + PM
        self._gateways: dict[str, PaperGateway] = {}
        self._position_mgrs: dict[str, PositionManager] = {}
        for cfg in components:
            gw, pm = create_component_gateway(cfg, account)
            self._gateways[cfg.name] = gw
            self._position_mgrs[cfg.name] = pm

        self._nav_history: list[ComboNavRecord] = []
        self._last_rebalance: dict[str, str] = {}
        self._order_counter = 0
        self._trade_day_count = 0

    # -- 公开属性 ---------------------------------------------------------------

    @property
    def nav_history(self) -> list[ComboNavRecord]:
        return self._nav_history

    @property
    def account(self) -> Account:
        return self._account

    @property
    def gateways(self) -> dict[str, PaperGateway]:
        return self._gateways

    @property
    def position_managers(self) -> dict[str, PositionManager]:
        return self._position_mgrs

    @property
    def components(self) -> dict[str, ComponentConfig]:
        return self._components

    # -- 主流程 ---------------------------------------------------------------

    def fill_pending_and_update_nav(
        self,
        trade_date: date,
        bars: dict[str, Bar],
    ) -> ComboDayResult:
        """仅执行 Phase A (撮合昨日挂单) + Phase B (NAV 更新).

        不递增 _trade_day_count, 不执行 day_end / expire / reconcile。
        供 combo_orchestrator Step 7 使用, 避免 double-run_day 问题。
        """
        result = ComboDayResult(trade_date=trade_date.isoformat())

        # Phase A: 各组件撮合昨日挂单
        result.fills = self._fill_all_pending(bars)

        # 更新各网关的最新价格
        prices = {sym: bar.close for sym, bar in bars.items()}
        for gw in self._gateways.values():
            gw._latest_prices.update(prices)

        # Phase B: 更新组合 NAV
        result.nav = self._update_nav(trade_date, prices)

        # Phase B+: 喂养风控约束
        self._feed_risk_constraints(trade_date, result.nav)

        return result

    def run_day(
        self,
        trade_date: date,
        bars: dict[str, Bar],
        component_picks: dict[str, list[str]] | None = None,
        ma5_prices: dict[str, float] | None = None,
    ) -> ComboDayResult:
        """执行组合单日交易周期。

        Args:
            trade_date: 交易日
            bars: symbol → Bar 映射（所有组件共用）
            component_picks: 组件名 → 目标标的列表（按评分排序）
                             为 None 时跳过调仓
            ma5_prices: symbol → 5日均价（§14.1 Layer ② fat-finger 保护）

        Returns:
            ComboDayResult
        """
        result = ComboDayResult(trade_date=trade_date.isoformat())
        self._trade_day_count += 1

        # Phase A: 各组件撮合昨日挂单
        result.fills = self._fill_all_pending(bars)

        # 更新各网关的最新价格
        prices = {sym: bar.close for sym, bar in bars.items()}
        for gw in self._gateways.values():
            gw._latest_prices.update(prices)

        # Phase B: 更新组合 NAV
        result.nav = self._update_nav(trade_date, prices)

        # Phase B+: 喂养风控约束（§13.15 / §4.3.1 — 与 BacktestEngine 对齐）
        self._feed_risk_constraints(trade_date, result.nav)

        # Phase C: 逐组件调仓（按权重降序，大组件优先）
        if component_picks is not None:
            sorted_comps = sorted(
                self._components.values(), key=lambda c: -c.weight,
            )
            for cfg in sorted_comps:
                picks = component_picks.get(cfg.name)
                if picks is None:
                    continue
                if not self._should_rebalance(cfg.name, trade_date):
                    continue
                cr = self._rebalance_component(
                    cfg, picks, result.nav, trade_date, prices, bars,
                    ma5_prices=ma5_prices,
                )
                result.component_results[cfg.name] = cr
                self._last_rebalance[cfg.name] = trade_date.isoformat()

        # Phase D: 各组件日终
        for gw in self._gateways.values():
            gw.day_end(trade_date)

        # Phase E: EOD 自动取消未成交订单 — 释放冻结资金
        # next_bar_open 语义: 当日 Phase C 提交的订单保留到次日 Phase A 撮合,
        # 仅过期昨日及更早提交但今天仍未成交的"stale"订单。
        from datetime import datetime as _dt
        day_start = _dt.combine(trade_date, _dt.min.time())
        total_expired = 0
        for name, gw in self._gateways.items():
            expired = gw.expire_active_orders(before=day_start)
            if expired > 0:
                total_expired += expired
                logger.warning(
                    "[%s] EOD 自动取消 %d 个 stale 订单 (释放冻结资金)",
                    name, expired,
                )
        # F-12: 大量订单超时时升级告警
        if total_expired >= 3:
            try:
                from aipha.monitor.feishu_alert import send_feishu_alert
                send_feishu_alert(
                    f"EOD 订单超时: {total_expired} 个 stale 订单被自动取消 "
                    f"(trade_date={trade_date})",
                    level="WARNING",
                )
            except Exception as e:  # silent-pass-exempt
                logger.debug("feishu alert failed: %s", e)

        # Phase F: 持仓对账 (§R168 P0-C: Paper 路径持仓一致性校验)
        self._reconcile_positions(trade_date, prices)

        return result

    # -- Phase A: 撮合 -------------------------------------------------------

    def _reconcile_positions(
        self, trade_date: date, prices: dict[str, float],
    ) -> None:
        """P0-C: Paper 路径持仓对账 — 网关持仓 vs PositionManager 一致性校验.

        检查每个组件的 PaperGateway 内部持仓与 PositionManager 是否一致。
        偏差时记录审计日志并告警。
        """
        for name in self._components:
            gw = self._gateways.get(name)
            pm = self._position_mgrs.get(name)
            if gw is None or pm is None:
                continue
            # 从 gateway 获取持仓
            gw_positions = gw.get_positions_dict()
            # 从 position manager 获取持仓
            pm_positions = {s: p.total_qty for s, p in pm.positions.items() if p.total_qty > 0}

            # 对比
            all_syms = set(gw_positions.keys()) | set(pm_positions.keys())
            diffs: list[str] = []
            for sym in sorted(all_syms):
                gw_qty = gw_positions.get(sym, 0)
                pm_qty = pm_positions.get(sym, 0)
                if gw_qty != pm_qty:
                    diffs.append(f"{sym}: gw={gw_qty} pm={pm_qty}")

            if diffs:
                diff_str = "; ".join(diffs[:10])
                msg = (
                    f"[{name}] 持仓对账不一致 ({len(diffs)} 标的): {diff_str}"
                )
                logger.error(msg)
                if self._audit_logger:
                    self._audit_logger.log_risk_check(
                        passed=False, symbol="RECONCILIATION",
                        qty=len(diffs),
                        violation_type="POSITION_MISMATCH",
                    )
                # §13.13: 告警即生命线
                try:
                    from aipha.monitor.feishu_alert import send_feishu_alert
                    send_feishu_alert(
                        f"[Paper] 持仓对账异常: {name}",
                        f"日期: {trade_date}\n偏差: {diff_str}\n需人工检查!",
                        level="CRITICAL",
                    )
                except Exception as e:  # noqa: silent-pass-exempt — alert failure non-fatal
                    logger.warning("飞书告警发送失败(对账): %s", e)

        # NAV 交叉校验: sum(市值) + cash ≈ 上次 _update_nav 计算值
        total_mkt = sum(
            prices.get(sym, 0) * pos.total_qty
            for pm in self._position_mgrs.values()
            for sym, pos in pm.positions.items()
            if pos.total_qty > 0
        )
        expected_nav = total_mkt + float(self._account.cash)
        actual_nav = self._nav_history[-1].nav if self._nav_history else 0.0
        if actual_nav > 0:
            nav_deviation = abs(expected_nav - actual_nav) / actual_nav
            if nav_deviation > 0.005:  # 0.5% 偏差阈值
                logger.error(
                    "NAV 交叉校验失败: expected=%.2f actual=%.2f dev=%.4f",
                    expected_nav, actual_nav, nav_deviation,
                )
                try:
                    from aipha.monitor.feishu_alert import send_feishu_alert
                    send_feishu_alert(
                        "[Paper] NAV 交叉校验偏差 > 0.5%",
                        f"日期: {trade_date}\n"
                        f"计算值: {expected_nav:.2f}\n"
                        f"记录值: {actual_nav:.2f}\n"
                        f"偏差: {nav_deviation:.4%}",
                        level="WARNING",
                    )
                except Exception as e:  # noqa: silent-pass-exempt — alert failure non-fatal
                    logger.warning("飞书告警发送失败(NAV): %s", e)

    def _fill_all_pending(self, bars: dict[str, Bar]) -> int:
        """各组件网关撮合昨日挂单。"""
        total_fills = 0
        for name, gw in self._gateways.items():
            results = gw.fill_pending(bars)
            filled = sum(1 for r in results if r.fill is not None)
            total_fills += filled
            if filled:
                logger.info("fill_pending[%s]: %d filled", name, filled)
            # §P1-5: 审计日志——成交回报 + 风控约束 record_trade
            for r in results:
                if r.fill is not None:
                    notional = r.fill.notional
                    # TurnoverLimit: 记录交易金额
                    if self._risk_manager:
                        for c in self._risk_manager.constraints:
                            if hasattr(c, 'record_trade'):
                                c.record_trade(notional)
                    # §P2-19: 成交价偏离检测 — 成交价 vs 参考价(open) > 2% 告警
                    self._check_fill_price_deviation(
                        r.fill.symbol, float(r.fill.price), bars, name,
                    )
                    if self._audit_logger:
                        self._audit_logger.log_order_filled(
                            order_id=r.fill.order_id,
                            symbol=r.fill.symbol,
                            side=r.fill.side.value if hasattr(r.fill.side, 'value') else str(r.fill.side),
                            filled_qty=r.fill.qty,
                            fill_price=float(r.fill.price),
                            commission=0.0,
                        )
        return total_fills

    # -- Fill price deviation detection (§P2-19) ---

    _FILL_DEVIATION_THRESHOLD = 0.02  # 2%

    def _check_fill_price_deviation(
        self, symbol: str, fill_price: float,
        bars: dict[str, "Bar"], comp_name: str,
    ) -> None:
        """成交价偏离参考价(open) > 2% 时记录告警。"""
        bar = bars.get(symbol)
        if bar is None or fill_price <= 0:
            return
        ref_price = bar.open if bar.open and bar.open > 0 else bar.close
        if not ref_price or ref_price <= 0:
            return
        deviation = abs(fill_price - ref_price) / ref_price
        if deviation > self._FILL_DEVIATION_THRESHOLD:
            msg = (
                f"[{comp_name}] {symbol} 成交价偏离: "
                f"fill={fill_price:.4f} ref={ref_price:.4f} "
                f"dev={deviation:.2%} > {self._FILL_DEVIATION_THRESHOLD:.0%}"
            )
            logger.warning(msg)
            if self._audit_logger:
                self._audit_logger.log_risk_check(
                    passed=False, symbol=symbol, qty=0,
                    violation_type=f"FILL_PRICE_DEVIATION_{deviation:.2%}",
                )

    # -- Phase B: NAV --------------------------------------------------------

    def _update_nav(
        self, trade_date: date, prices: dict[str, float],
    ) -> float:
        """汇总所有组件市值 + 现金，记录 NAV 历史。

        委托 trading_ops.calc_nav() 计算，确保 Paper/Live/Backtest
        NAV 语义一致（§13.2 单一实现原则 / §13.11 缺价回退）。
        """
        from aipha.execution.trading_ops import (
            FallbackPolicy, calc_nav,
        )

        comp_values: dict[str, float] = {}
        all_positions: list[dict[str, Any]] = []
        total_positions = 0
        for name, pm in self._position_mgrs.items():
            for symbol, pos in pm.positions.items():
                all_positions.append({
                    "code": symbol,
                    "shares": pos.total_qty,
                    "avg_cost": float(pos.avg_cost),
                })
            # 组件级市值用于监控分解
            comp_values[name] = pm.get_total_market_value(prices)
            total_positions += len(pm.positions)

        nav_result = calc_nav(
            cash=float(self._account.cash),
            positions=all_positions,
            prices=prices,
            fallback=FallbackPolicy.AVG_COST,
        )

        new_record = ComboNavRecord(
            trade_date=trade_date.isoformat(),
            nav=nav_result.nav,
            cash=nav_result.cash,
            positions_value=nav_result.position_value,
            component_values=comp_values,
            n_positions=total_positions,
        )

        # 同日多次调用时更新而非追加（Step7 fill + Step10 run_day 两次调用）
        if (self._nav_history
                and self._nav_history[-1].trade_date == trade_date.isoformat()):
            self._nav_history[-1] = new_record
        else:
            self._nav_history.append(new_record)

        if nav_result.unpriced_codes:
            logger.warning(
                "NAV 计算有 %d 个缺价标的(fallback=avg_cost): %s",
                nav_result.unpriced_count,
                nav_result.unpriced_codes[:5],
            )

        return nav_result.nav

    # -- Phase B+: 风控约束喂养 (§13.15 / §4.3.1) ----------------------------

    def _feed_risk_constraints(self, trade_date: date, nav: float) -> None:
        """每日喂养风控约束状态，与 BacktestEngine 对齐。"""
        rm = self._risk_manager
        if rm is None:
            return
        td_str = str(trade_date)
        # 1. TurnoverLimit 等日内计数器重置
        for c in rm.constraints:
            if hasattr(c, 'reset_daily'):
                c.reset_daily(td_str)
        # 2. DrawdownLimit NAV 更新
        for c in rm.constraints:
            if hasattr(c, 'update_nav'):
                c.update_nav(nav)
        # 3. DrawdownLimit 持仓状态（peak_nav 重置判断）
        has_pos = any(
            len(pm.positions) > 0 for pm in self._position_mgrs.values()
        )
        for c in rm.constraints:
            if hasattr(c, 'update_position_state'):
                c.update_position_state(has_pos)
        # 4. ForceRedeemExposureLimit CB 转股价值（暂用最新价格代理）
        for c in rm.constraints:
            if hasattr(c, 'update_cb_values'):
                latest = {}
                for gw in self._gateways.values():
                    latest.update(gw._latest_prices)
                c.update_cb_values(latest)
                break  # 只需喂一次

    # -- 调仓频率控制 ---------------------------------------------------------

    def _should_rebalance(self, comp_name: str, trade_date: date) -> bool:
        """判断组件是否需要调仓。"""
        cfg = self._components[comp_name]
        if cfg.rebalance_freq <= 1:
            return True  # daily
        last = self._last_rebalance.get(comp_name)
        if last is None:
            return True  # 首次
        # 简化：基于交易日计数（精确版应用交易日历）
        return self._trade_day_count % cfg.rebalance_freq == 1

    # -- Phase C: 组件级调仓 --------------------------------------------------

    def _rebalance_component(
        self,
        cfg: ComponentConfig,
        target_symbols: list[str],
        nav: float,
        trade_date: date,
        prices: dict[str, float],
        bars: dict[str, Bar] | None = None,
        ma5_prices: dict[str, float] | None = None,
    ) -> ComponentResult:
        """单个组件的调仓：先卖后买。"""
        gw = self._gateways[cfg.name]
        pm = self._position_mgrs[cfg.name]
        result = ComponentResult(component=cfg.name)

        target_value = nav * cfg.weight
        cash_floor = Decimal(str(nav * self._cash_reserve_pct))
        target_set = set(target_symbols)
        current_set = set(pm.positions.keys())

        # §审计: ORDER_INTENT — 记录调仓意图（卖出+买入目标）
        if self._audit_logger and target_symbols:
            sells_intent = sorted(current_set - target_set)
            buys_intent = sorted(target_set - current_set)
            for sym in sells_intent:
                qty = pm.get_available_to_sell(sym)
                if qty > 0:
                    self._audit_logger.log_order_intent(
                        symbol=sym, side="SELL", qty=qty, strategy_name=cfg.name,
                    )
            for sym in buys_intent:
                self._audit_logger.log_order_intent(
                    symbol=sym, side="BUY", qty=0, strategy_name=cfg.name,
                    reason="target_rebalance",
                )

        # --- SELL: 当前持仓不在目标中 ---
        for sym in current_set - target_set:
            qty = pm.get_available_to_sell(sym)
            if qty <= 0:
                continue
            oid = self._next_order_id()
            order = GatewayOrder(
                order_id=oid, symbol=sym, side="SELL",
                qty=qty, order_type="MARKET",
            )
            try:
                gw.submit_order(order)
                result.sells += 1
                if self._audit_logger:
                    self._audit_logger.log_order_submit(
                        order_id=oid, symbol=sym, side="SELL", qty=qty,
                    )
            except Exception as e:
                logger.warning("[%s] Sell failed %s: %s", cfg.name, sym, e)
                result.skipped += 1
                if self._audit_logger:
                    self._audit_logger.log_order_rejected(
                        order_id=oid, symbol=sym, reason=str(e),
                    )

        # --- BUY: 目标中不在当前持仓 ---
        n_targets = len(target_symbols)
        if n_targets == 0:
            return result
        per_stock = target_value / n_targets

        # §13 防重复下单: 排除已有 pending BUY 订单的标的 (P0 duplicate guard)
        pending_codes = set(gw.get_pending_order_codes())
        if pending_codes:
            logger.info(
                "[%s] 排除 %d 只已有 pending 订单的标的: %s",
                cfg.name, len(pending_codes),
                sorted(pending_codes)[:5],
            )

        # 先收集所有候选买单
        buy_candidates: list[tuple[str, int, float]] = []  # (sym, shares, price)
        for sym in target_symbols:
            if sym in current_set or sym in pending_codes:
                continue
            price = prices.get(sym)
            if not price or price <= 0:
                result.skipped += 1
                continue
            shares = int(per_stock / price / cfg.lot_size) * cfg.lot_size
            if shares <= 0:
                result.skipped += 1
                continue
            buy_candidates.append((sym, shares, price))

        # §13.15: 前交易风控检查 (§13.18 Fail-Closed: risk_manager=None → raise)
        if buy_candidates:
            if self._risk_manager is None:
                raise ValueError(
                    "P0-A: risk_manager=None — 禁止无风控交易。"
                    "必须在 ComboEODRunner 初始化时传入 CompositeRiskManager 实例。"
                )
            from aipha.execution.pretrade import run_pretrade_checks

            pos_dict = {s: p.total_qty for s, p in pm.positions.items()}
            buy_intents = [(s, q, "BUY") for s, q, _ in buy_candidates]
            pt_result = run_pretrade_checks(
                intents=buy_intents,
                current_positions=pos_dict,
                nav=nav,
                prices=prices,
                risk_manager=self._risk_manager,
            )
            # 构建审批后的 symbol→qty 映射
            approved_map = {s: q for s, q, _ in pt_result.approved}
            result.skipped += len(pt_result.rejected)
            # §P1-5: 审计日志——风控检查
            # PretradeResult.rejected = list[tuple[symbol, qty, side, reason]] (4-tuple)
            if self._audit_logger:
                for s, q, _side, reason in pt_result.rejected:
                    self._audit_logger.log_risk_check(
                        passed=False, symbol=s, qty=q,
                        violation_type=reason,
                    )
            # 过滤候选列表
            buy_candidates = [
                (s, approved_map.get(s, q), p)
                for s, q, p in buy_candidates
                if s in approved_map
            ]

        # §13.15: 合规检查（停牌/涨跌停/参与率/CB强赎）(§13.18 Fail-Closed)
        if buy_candidates:
            if self._compliance_engine is None:
                raise ValueError(
                    "P0-A: compliance_engine=None — 禁止无合规检查交易。"
                    "必须在 ComboEODRunner 初始化时传入 ComplianceEngine 实例。"
                )
            from aipha.execution.pretrade import run_compliance_checks

            # 从 bars 构建 market_state
            mkt_state: dict[str, dict] = {}
            if bars:
                for sym, bar in bars.items():
                    meta = getattr(bar, "metadata", {}) or {}
                    mkt_state[sym] = {
                        "is_halted": bar.is_suspended,
                        "is_limit_up": bar.is_limit_up or False,
                        "is_limit_down": bar.is_limit_down or False,
                        "volume": bar.volume,
                        "is_st": meta.get("is_st", False),
                        "list_status": meta.get("list_status", "L"),
                    }
            at = "cb" if cfg.asset_type == "convertible_bond" else "equity"
            buy_intents = [(s, q, "BUY") for s, q, _ in buy_candidates]
            cc_result = run_compliance_checks(
                intents=buy_intents,
                prices=prices,
                compliance_engine=self._compliance_engine,
                market_state=mkt_state,
                portfolio_total=nav,
                asset_type=at,
            )
            cc_approved = {s: q for s, q, _ in cc_result.approved}
            result.skipped += len(cc_result.rejected)
            buy_candidates = [
                (s, cc_approved.get(s, q), p)
                for s, q, p in buy_candidates
                if s in cc_approved
            ]

        # §14.1 Layer ②: Fat-finger MA5 price deviation check
        if buy_candidates and ma5_prices:
            from aipha.execution.pretrade import run_fat_finger_checks

            ff_intents = [(s, q, "BUY") for s, q, _ in buy_candidates]
            ff_result = run_fat_finger_checks(
                intents=ff_intents,
                prices=prices,
                nav=nav,
                ma5=ma5_prices,
            )
            ff_approved = {s for s, _, _ in ff_result.approved}
            for s, q, _side, reason in ff_result.rejected:
                logger.warning(
                    "[%s] Fat-finger Layer② 拦截 %s: %s", cfg.name, s, reason,
                )
                result.skipped += 1
                if self._audit_logger:
                    self._audit_logger.log_risk_check(
                        passed=False, symbol=s, qty=q,
                        violation_type=f"fat_finger_ma5: {reason}",
                    )
            buy_candidates = [
                (s, q, p) for s, q, p in buy_candidates if s in ff_approved
            ]

        # 提交买单
        for sym, shares, price in buy_candidates:
            # 资金充足性检查
            est_cost = Decimal(str(price * shares)) * Decimal("1.01")
            if self._account.available_cash - est_cost < cash_floor:
                logger.info(
                    "[%s] Skip buy %s: insufficient cash", cfg.name, sym,
                )
                result.skipped += 1
                continue
            oid = self._next_order_id()
            order = GatewayOrder(
                order_id=oid, symbol=sym, side="BUY",
                qty=shares, order_type="MARKET",
            )
            try:
                gw.submit_order(order)
                result.buys += 1
                if self._audit_logger:
                    self._audit_logger.log_order_submit(
                        order_id=oid, symbol=sym, side="BUY", qty=shares,
                    )
            except Exception as e:
                logger.warning("[%s] Buy failed %s: %s", cfg.name, sym, e)
                result.skipped += 1
                if self._audit_logger:
                    self._audit_logger.log_order_rejected(
                        order_id=oid, symbol=sym, reason=str(e),
                    )

        return result

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"COMBO-{self._order_counter:06d}"

    # -- 状态持久化 -----------------------------------------------------------

    def save_state(self, path: Path | None = None) -> None:
        """原子化保存状态（先写 .tmp，再 rename）。"""
        path = path or (self._state_dir / "state.json" if self._state_dir else None)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)

        comp_positions: dict[str, list[dict]] = {}
        for name, pm in self._position_mgrs.items():
            positions = []
            # 获取该组件网关的最新价格用于 mark-to-market 持久化
            gw = self._gateways.get(name)
            gw_prices = getattr(gw, "_latest_prices", {}) if gw else {}
            for sym, pos in pm.positions.items():
                pos_dict = {
                    "ts_code": sym,
                    "shares": pos.total_qty,
                    "avg_cost": float(pos.avg_cost),
                    "today_bought_qty": pos.today_bought_qty,
                    "frozen_qty": pos.frozen_qty,
                    "t_plus_n": pos.t_plus_n,
                }
                # Mark-to-Market: 持久化最新价格 (§13.11 NAV 缺价回退)
                if sym in gw_prices:
                    pos_dict["last_price"] = float(gw_prices[sym])
                positions.append(pos_dict)
            comp_positions[name] = positions

        state = {
            "state_version": 1,
            "cash": float(self._account.cash),
            "frozen_cash": float(self._account.frozen_cash),
            "initial_cash": float(self._account.initial_cash),
            "component_positions": comp_positions,
            "last_rebalance": self._last_rebalance,
            "trade_day_count": self._trade_day_count,
            "order_counter": self._order_counter,
            "nav_history": [
                {
                    "trade_date": r.trade_date,
                    "nav": r.nav,
                    "cash": r.cash,
                    "positions_value": r.positions_value,
                    "component_values": r.component_values,
                    "n_positions": r.n_positions,
                }
                for r in self._nav_history[-500:]
            ],
            # P0 fix: persist pending orders for next_bar_open cross-day fill
            "pending_orders": self._serialize_pending_orders(),
        }

        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.replace(path)
        logger.info("State saved: %s", path)

        # R167 A3 + Audit-Fix: 同步写出子目录 state.json 和 nav_history.json
        # 监控消费方（daily_monitor / paper_monitor / s4_validator）
        # 期望读 data/paper_state/{profile}/state.json
        # 以及  data/paper_state/{profile}/nav_history.json
        try:
            profile_name = path.stem.removesuffix("_state")
            # 处理自定义 state_dir 的双层嵌套：
            # 若 state_dir 已是 profile 子目录 (v5a_s4/v5a_s4_state.json)
            # 则 profile_dir = state_dir 本身，不再嵌套
            if path.parent.name == profile_name:
                profile_dir = path.parent
            else:
                profile_dir = path.parent / profile_name
            profile_dir.mkdir(parents=True, exist_ok=True)

            # 写 state.json — 让监控管线能找到
            subdir_state = profile_dir / "state.json"
            sub_tmp = subdir_state.with_suffix(".tmp")
            sub_tmp.write_text(
                json.dumps(state, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            sub_tmp.replace(subdir_state)

            # 写 nav_history.json — governance/scripts 消费方依赖
            nav_path = profile_dir / "nav_history.json"
            nav_records = state["nav_history"]
            # Fix #6: 写出前按 trade_date 去重（保留最后一条）
            seen_dates: dict[str, dict] = {}
            for rec in nav_records:
                seen_dates[rec["trade_date"]] = rec
            nav_deduped = list(seen_dates.values())
            nav_tmp = nav_path.with_suffix(".tmp")
            nav_tmp.write_text(
                json.dumps(nav_deduped, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            nav_tmp.replace(nav_path)
        except Exception as exc:
            logger.warning("Failed to write profile dir artifacts: %s", exc)

    def load_state(self, path: Path) -> None:
        """从 JSON 恢复状态。"""
        from aipha.portfolio.position import Position

        data = json.loads(path.read_text(encoding="utf-8"))

        # 恢复 Account
        self._account.cash = Decimal(str(data["cash"]))
        self._account.frozen_cash = Decimal(str(data.get("frozen_cash", 0)))

        # 恢复各组件的 PositionManager
        comp_positions = data.get("component_positions", {})
        for name, pm in self._position_mgrs.items():
            pm.positions.clear()
            for p in comp_positions.get(name, []):
                pos = Position(
                    symbol=p["ts_code"],
                    total_qty=p["shares"],
                    avg_cost=Decimal(str(p["avg_cost"])),
                    today_bought_qty=p.get("today_bought_qty", 0),
                    frozen_qty=p.get("frozen_qty", 0),
                    t_plus_n=p.get("t_plus_n", pm.default_t_plus_n),
                )
                pm.positions[pos.symbol] = pos

        # 恢复元数据
        self._last_rebalance = data.get("last_rebalance", {})
        self._trade_day_count = data.get("trade_day_count", 0)
        self._order_counter = data.get("order_counter", 0)
        self._nav_history = [
            ComboNavRecord(**r) for r in data.get("nav_history", [])
        ]

        # P0 fix: restore pending orders into gateways
        self._restore_pending_orders(data.get("pending_orders", {}))

        # Orphan frozen_cash cleanup: if no pending orders exist but frozen_cash > 0
        total_pending = sum(
            len([o for o in gw._orders.values() if o.is_active])
            for gw in self._gateways.values()
        )
        if total_pending == 0 and self._account.frozen_cash > 0:
            logger.warning(
                "Orphan frozen_cash detected (%.2f) with 0 pending orders — resetting",
                self._account.frozen_cash,
            )
            self._account.frozen_cash = Decimal("0")

        logger.info("State loaded: %s (%d components)", path, len(comp_positions))

    # -- Pending order persistence helpers -----------------------------------

    def _serialize_pending_orders(self) -> dict[str, list[dict]]:
        """Serialize active orders from each gateway for state persistence."""
        result: dict[str, list[dict]] = {}
        for name, gw in self._gateways.items():
            orders = []
            for oid, gw_order in gw._orders.items():
                if not gw_order.is_active:
                    continue
                orders.append({
                    "order_id": gw_order.order_id,
                    "symbol": gw_order.symbol,
                    "side": gw_order.side,
                    "qty": gw_order.qty,
                    "order_type": gw_order.order_type,
                    "limit_price": gw_order.limit_price,
                    "submitted_at": gw_order.submitted_at.isoformat() if gw_order.submitted_at else None,
                    "frozen_cash": str(gw_order.metadata.get("frozen_cash", 0)),
                })
            if orders:
                result[name] = orders
        return result

    def _restore_pending_orders(self, pending: dict[str, list[dict]]) -> None:
        """Restore pending orders into gateways from saved state."""
        from datetime import datetime as _dt
        from aipha.execution.gateway import GatewayOrder, OrderStatus
        from aipha.domain.models import Order, Side, OrderType

        for name, orders in pending.items():
            gw = self._gateways.get(name)
            if gw is None:
                logger.warning("Cannot restore orders for unknown component: %s", name)
                continue
            for o in orders:
                submitted = (
                    _dt.fromisoformat(o["submitted_at"])
                    if o.get("submitted_at") else _dt.now()
                )
                gw_order = GatewayOrder(
                    order_id=o["order_id"],
                    symbol=o["symbol"],
                    side=o["side"],
                    qty=o["qty"],
                    order_type=o["order_type"],
                    limit_price=o.get("limit_price"),
                    status=OrderStatus.SUBMITTED,
                    submitted_at=submitted,
                    metadata={"frozen_cash": Decimal(o.get("frozen_cash", "0"))},
                )
                # Inject into gateway internal state
                gw._orders[o["order_id"]] = gw_order
                # Also create domain order for fill_pending
                side = Side.BUY if o["side"] == "BUY" else Side.SELL
                domain_order = Order(
                    order_id=o["order_id"],
                    symbol=o["symbol"],
                    side=side,
                    qty=o["qty"],
                    order_type=OrderType.MARKET if o["order_type"] == "MARKET" else OrderType.LIMIT,
                    t_submit=submitted,
                    limit_price=o.get("limit_price"),
                )
                gw._domain_orders[o["order_id"]] = domain_order
            if orders:
                logger.info(
                    "Restored %d pending orders for [%s]", len(orders), name,
                )
