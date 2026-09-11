"""多 Pillar 组合编排器 (RED-1: R90)

解决 R90 审计 RED-1 (P0)：PillarOrchestrator 缺失导致
5 组件组合无法运行，Paper Engine 降级为单策略。

架构：
    PillarOrchestrator 继承 Strategy，作为 PaperEngine 的唯一策略实例。
    内部为每个 pillar (CB/ML/ETF/Bond/Gold) 创建独立的策略实例，
    路由 bars、聚合 intents、追踪 per-pillar NAV。

    PaperEngine
      └── LiveRunner(strategy=PillarOrchestrator)
            └── PillarOrchestrator.on_bar()
                  ├── cb_strategy.on_bar()      → intents (lot=10, T+0)
                  ├── ml_strategy.on_bar()      → intents (lot=100, T+1)
                  ├── etf_strategy.on_bar()     → intents (lot=100, T+1)
                  ├── bond_strategy.on_bar()    → intents (lot=100, T+1)
                  └── gold_strategy.on_bar()    → intents (lot=100, T+1)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from aipha.domain.models import Side
from aipha.execution.trading_ops import (
    FallbackPolicy,
    FeeParams,
    calc_nav,
    calc_trade_cost,
    get_fee_params,
)
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond
from aipha.market.rules_etf import MarketRulesETF
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext

logger = logging.getLogger(__name__)

# §13.1: 从 MarketRules* 权威源派生交易规则，禁止独立维护副本
_EQUITY_RULES = MarketRulesCNEquity()
_CB_RULES = MarketRulesConvertibleBond()
_ETF_RULES = MarketRulesETF()


def _rules_dict(rules: object) -> dict[str, int | float]:
    """从 MarketRules 实例提取标准字段字典。"""
    return {
        "lot_size": int(rules.lot_size),
        "t_plus_n": int(rules.t_plus),
        "stamp_tax_sell_rate": float(rules.stamp_tax_rate),
        "transfer_fee_rate": float(getattr(rules, "transfer_fee_rate", 0)),
    }


MARKET_RULES: dict[str, dict[str, int | float]] = {
    "convertible_bond": _rules_dict(_CB_RULES),
    "a_stock": _rules_dict(_EQUITY_RULES),
    "etf": _rules_dict(_ETF_RULES),
    # bond: 暂复用 ETF 规则（无独立 MarketRulesBond 类，T+1 免印花税）
    "bond": _rules_dict(_ETF_RULES),
}


@dataclass
class PillarState:
    """单个 Pillar 的运行状态（虚拟资金隔离）

    会计模型 (R92 P0-A 修复):
        cash: 可用现金（买入减少、卖出增加）
        positions: 持仓数量
        nav = cash + positions_value（由 PillarOrchestrator._compute_nav 计算）
        prev_nav: 前一交易日收盘时的真实 NAV（用于日收益率计算）
    """

    name: str
    capital: float              # 初始分配资金
    cash: float                 # 当前可用现金
    prev_nav: float             # 前一日 NAV（真实 NAV = cash + 持仓市值）
    positions: dict[str, int] = field(default_factory=dict)
    buy_dates: dict[str, date] = field(default_factory=dict)  # T+1 强制
    avg_costs: dict[str, float] = field(default_factory=dict)  # 持仓均价（缺价回退用）
    lot_size: int = 100
    t_plus_n: int = 1


class PillarOrchestrator(Strategy):
    """多 Pillar 组合编排器。

    管理 CB/ML/ETF/Bond/Gold 五个 pillar 的独立策略实例，
    提供 per-pillar NAV 追踪、C2/C3 接口。
    """

    name = "pillar_orchestrator"

    def __init__(
        self,
        pillar_configs: dict[str, Any],
        portfolio_weights: dict[str, float],
        total_capital: float = 1_000_000.0,
        **kwargs: Any,
    ) -> None:
        self._pillar_configs = pillar_configs
        self._portfolio_weights = dict(portfolio_weights)
        self._total_capital = total_capital

        # per-pillar 策略实例
        self._strategies: dict[str, Strategy] = {}
        # per-pillar 运行状态
        self._pillar_states: dict[str, PillarState] = {}
        # symbol → pillar 归属（on_fill 路由）
        self._symbol_pillar: dict[str, str] = {}
        # R92 T2-3: 静态标的集合（init 时注册，on_bar 中不可覆盖）
        self._static_symbol_set: set[str] = set()
        # symbol → 最后成交价（持仓估值用）
        self._last_prices: dict[str, float] = {}

        # 初始化各 pillar
        self._initialize_pillars()

    def _initialize_pillars(self) -> None:
        """为每个 pillar 创建策略实例。"""
        for name, pcfg in self._pillar_configs.items():
            weight = self._portfolio_weights.get(name, pcfg.weight)
            capital = self._total_capital * weight
            rules = MARKET_RULES.get(pcfg.market_type, MARKET_RULES["a_stock"])

            strategy = self._create_pillar_strategy(name, pcfg)
            if strategy is None:
                logger.warning(f"Pillar '{name}' 无法创建策略，跳过")
                continue

            self._strategies[name] = strategy
            self._pillar_states[name] = PillarState(
                name=name,
                capital=capital,
                cash=capital,
                prev_nav=capital,
                lot_size=rules["lot_size"],
                t_plus_n=rules["t_plus_n"],
            )

            # 静态标的归属（优先级最高，不可被动态 intent 覆盖）
            for sym in (pcfg.static_symbols or []):
                self._symbol_pillar[sym] = name
                self._static_symbol_set.add(sym)

            logger.info(
                f"Pillar '{name}': weight={weight:.0%}, "
                f"capital={capital:,.0f}, "
                f"lot_size={rules['lot_size']}, "
                f"T+{rules['t_plus_n']}, "
                f"strategy={strategy.name}"
            )

    def _create_pillar_strategy(
        self, name: str, pcfg: Any,
    ) -> Strategy | None:
        """根据 pillar 配置创建策略实例。

        路由逻辑：
        - CB pillar (sub_strategies): CBCompositeStrategy
        - ML pillar (model_dir): ml_predict 策略
        - ETF pillar (strategy_name): etf_rotation
        - Bond/Gold (static_symbols): StaticHoldStrategy
        """
        from aipha.strategy.registry import StrategyRegistry
        registry = StrategyRegistry()

        # 1) CB pillar — 多子策略混合
        if pcfg.sub_strategies:
            sub_list = pcfg.sub_strategies
            return CBCompositeStrategy(sub_configs=sub_list, registry=registry)

        # 2) ML pillar — ml_predict 策略
        if pcfg.model_dir:
            cls = registry.get("ml_predict")
            if cls:
                params = dict(pcfg.raw) if pcfg.raw else {}
                params["model_path"] = pcfg.model_dir
                try:
                    return cls(**params)
                except Exception as e:
                    logger.error(f"ML 策略创建失败: {e}")
            return None

        # 3) ETF 策略 — 先尝试配置名再回退
        if pcfg.strategy_name:
            cls = registry.get(pcfg.strategy_name)
            if cls is None:
                fallback = "etf_rotation"
                logger.warning(
                    f"Pillar '{name}': 策略 '{pcfg.strategy_name}' 未注册，"
                    f"降级为 '{fallback}'。回测⇔模拟盘策略可能不一致。"
                )
                cls = registry.get(fallback)
            if cls:
                params = dict(pcfg.raw) if pcfg.raw else {}
                params.pop("strategy_name", None)
                params.pop("market_type", None)
                params.pop("weight", None)
                try:
                    return cls(**params)
                except Exception as e:
                    logger.error(f"ETF 策略创建失败: {e}")
            return None

        # 4) 静态持仓 — Bond/Gold
        if pcfg.static_symbols:
            rebalance = pcfg.raw.get("rebalance_days", 20) if pcfg.raw else 20
            weights_map: dict[str, float] = {}
            for bs in (pcfg.bond_symbols or []):
                weights_map[bs.code] = bs.weight
            return StaticHoldStrategy(
                symbols=pcfg.static_symbols,
                symbol_weights=weights_map,
                rebalance_days=rebalance,
            )

        logger.warning(f"Pillar '{name}' 无法推断策略类型")
        return None

    def on_bar(
        self,
        bars: dict[str, Any],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """路由 bars 到各 pillar 策略并聚合 intents。

        每个 pillar 策略收到完整 bars 字典（自行过滤标的），
        返回的 intents 经过 lot_size 取整和 T+1 过滤后聚合返回。
        """
        all_intents: list[OrderIntent] = []
        today = context.current_time.date()

        for pname, strategy in self._strategies.items():
            state = self._pillar_states[pname]

            # 构建 per-pillar 虚拟 context（R92: nav = cash + 持仓市值）
            computed_nav = self._compute_nav(state)
            pillar_ctx = StrategyContext(
                current_time=context.current_time,
                positions=dict(state.positions),
                cash=max(0.0, state.cash),
                nav=computed_nav,
            )

            try:
                intents = strategy.on_bar(bars, pillar_ctx)
            except Exception as e:
                logger.error(f"Pillar '{pname}' on_bar 异常: {e}")
                continue

            if not intents:
                continue

            for intent in intents:
                # T+1 过滤：不允许当日买入的标的当日卖出
                if state.t_plus_n > 0 and intent.side.name == "SELL":
                    buy_dt = state.buy_dates.get(intent.symbol)
                    if buy_dt and buy_dt >= today:
                        continue

                # lot_size 取整
                rounded_qty = self._round_qty(intent.qty, state.lot_size)
                if rounded_qty <= 0:
                    continue

                # 用 metadata 标记 pillar 归属
                meta = dict(intent.metadata) if intent.metadata else {}
                meta["pillar"] = pname
                new_intent = OrderIntent(
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=rounded_qty,
                    intent_type=intent.intent_type,
                    limit_price=intent.limit_price,
                    reason=intent.reason,
                    signal_strength=intent.signal_strength,
                    metadata=meta,
                )
                all_intents.append(new_intent)

                # 更新 symbol → pillar 映射
                # R92 T2-3: 静态标的归属优先于动态归属，防止 Bond/ETF 重叠冲突
                if intent.symbol not in self._static_symbol_set:
                    self._symbol_pillar[intent.symbol] = pname

        return all_intents

    def on_fill(
        self,
        symbol: str,
        side: Any,
        qty: int,
        price: float,
        commission: float = 0.0,
        market_date: date | None = None,
    ) -> None:
        """归属 fill 到正确 pillar 并更新现金/持仓（含交易成本）。

        R92 P0-A 修复: 修改 state.cash 而非旧 state.nav。
        R92 P0-C 修复: 使用 market_date 而非 date.today()。

        Args:
            symbol: 标的代码
            side: 买卖方向
            qty: 成交数量
            price: 成交价格
            commission: **被忽略**。§13.7/§13.2 规定费用只有一个来源
                ``calc_trade_cost()``；网关上报的这个值通常只含佣金，
                不含印花税/过户费，采信它会让模拟盘现金系统性偏高。
                参数保留只为兼容既有调用方签名。
            market_date: 市场日期（用于 T+1 记录），None 时降级为 date.today()
        """
        pillar = self._symbol_pillar.get(symbol)
        if not pillar or pillar not in self._pillar_states:
            return

        state = self._pillar_states[pillar]
        current_qty = state.positions.get(symbol, 0)

        # 数量为 0/负的「成交」不是成交，直接返回。
        # 不能往下走：calc_trade_cost(0, ...) 会返回**最低佣金 5 元**
        # （commission = max(amount * rate, commission_min)），于是一笔
        # 什么都没成交的事件照样扣 5 元现金。重复 fill 事件、或持仓已被
        # 手工清掉后迟到的卖出回报，都会走到这里。
        if qty <= 0:
            logger.warning(
                f"Pillar '{pillar}' 收到 qty={qty} 的 fill ({symbol})，已忽略"
            )
            return

        trade_value = qty * price
        fill_date = market_date or date.today()

        # §13.7 / §13.2: 委托 trading_ops.calc_trade_cost() 计算完整交易成本
        # 忽略 gateway 预计算的 commission 参数（可能不含印花税/过户费）
        pcfg = self._pillar_configs.get(pillar)
        mtype = pcfg.market_type if pcfg else "a_stock"
        _MARKET_TO_ASSET = {
            "a_stock": "cn_equity",
            "convertible_bond": "convertible_bond",
            "etf": "etf",
            "bond": "etf",  # bond 复用 ETF 费率
        }
        asset_type = _MARKET_TO_ASSET.get(mtype, "cn_equity")
        fee_params = get_fee_params(asset_type)
        side_str = "buy" if getattr(side, "name", str(side)) == "BUY" else "sell"
        td_str = fill_date.strftime("%Y%m%d") if fill_date else None
        total_cost = calc_trade_cost(
            trade_value, side_str, fee_params, trade_date=td_str,
        )

        if side_str == "buy":
            # 更新加权平均成本
            old_cost = state.avg_costs.get(symbol, 0.0)
            new_total_qty = current_qty + qty
            if new_total_qty > 0:
                state.avg_costs[symbol] = (
                    old_cost * current_qty + price * qty
                ) / new_total_qty
            state.positions[symbol] = new_total_qty
            state.cash -= trade_value + total_cost
            state.buy_dates[symbol] = fill_date
            self._last_prices[symbol] = price
        else:
            # 防止超卖: 限制卖出量不超过持仓
            actual_sell = min(qty, current_qty)
            # 一股都卖不出（持仓已为 0）时不动现金。
            # 同上：calc_trade_cost(0, "sell", ...) 会收最低佣金 5 元。
            if actual_sell <= 0:
                logger.warning(
                    f"Pillar '{pillar}' 卖出 {symbol} {qty} 股但当前持仓为 "
                    f"{current_qty}，已忽略"
                )
                return
            remaining = current_qty - actual_sell
            actual_value = actual_sell * price
            # 若实际卖出量 < 原始 qty，需按实际金额重算费用
            if actual_sell != qty:
                total_cost = calc_trade_cost(
                    actual_value, "sell", fee_params, trade_date=td_str,
                )
            if remaining > 0:
                state.positions[symbol] = remaining
            else:
                state.positions.pop(symbol, None)
                state.buy_dates.pop(symbol, None)
                state.avg_costs.pop(symbol, None)
            state.cash += actual_value - total_cost
            self._last_prices[symbol] = price

        # 同步到子策略
        strategy = self._strategies.get(pillar)
        if strategy is not None:
            try:
                strategy.on_fill(symbol, side, qty, price)
            except Exception as e:
                logger.error(f"Pillar '{pillar}' on_fill 异常: {e}")

    def on_start(self) -> None:
        """启动所有 pillar 策略。"""
        for pname, strategy in self._strategies.items():
            try:
                strategy.on_start()
            except Exception as e:
                logger.error(f"Pillar '{pname}' on_start 异常: {e}")

    def on_end(self) -> None:
        """停止所有 pillar 策略。"""
        for pname, strategy in self._strategies.items():
            try:
                strategy.on_end()
            except Exception as e:
                logger.error(f"Pillar '{pname}' on_end 异常: {e}")

    # --- C2/C3 接口 ---

    def get_pillar_daily_return(self, pillar_name: str) -> float:
        """返回指定 pillar 的日收益率（C2/C3 使用）。

        R92 P0-A 修复: 使用 _compute_nav() 而非旧 state.nav。
        """
        state = self._pillar_states.get(pillar_name)
        if state is None or state.prev_nav <= 0:
            return 0.0
        return self._compute_nav(state) / state.prev_nav - 1.0

    def update_pillar_weight(
        self, pillar_name: str, new_weight: float,
    ) -> None:
        """动态更新 pillar 权重（C2/B1 使用）。"""
        if pillar_name not in self._pillar_states:
            return
        old_weight = self._portfolio_weights.get(pillar_name, 0.0)
        self._portfolio_weights[pillar_name] = new_weight
        self._pillar_states[pillar_name].capital = self._total_capital * new_weight
        logger.info(
            f"Pillar 权重更新: {pillar_name} {old_weight:.1%}→{new_weight:.1%}"
        )

    def switch_ml_model(
        self, model_dir: str,
    ) -> bool:
        """C3: 切换 ML pillar 的模型。

        优先使用公开方法 reload_model()，
        回退到 config.model_path + _load_model()。
        """
        strategy = self._strategies.get("ml")
        if strategy is None:
            return False

        try:
            # 优先: 公开接口 reload_model()
            if hasattr(strategy, "reload_model"):
                return strategy.reload_model(model_dir)

            # 回退: 通过 config.model_path + _load_model()
            if not hasattr(strategy, "_load_model"):
                logger.warning("ML pillar 策略不支持动态切换模型")
                return False

            cfg = getattr(strategy, "config", None) or getattr(strategy, "_config", None)
            if cfg is not None and hasattr(cfg, "model_path"):
                cfg.model_path = model_dir
            else:
                logger.warning(
                    f"ML 策略无可设置的 model_path 属性，"
                    f"尝试直接调用 _load_model"
                )

            strategy._load_model()
            logger.info(f"ML pillar 模型已切换到: {model_dir}")
            return True
        except Exception as e:
            logger.error(f"ML pillar 模型切换失败: {e}")
            return False

    @property
    def pillar_states(self) -> dict[str, PillarState]:
        """获取所有 pillar 状态（只读）。"""
        return getattr(self, "_pillar_states", {})

    @property
    def active_pillars(self) -> list[str]:
        """已激活的 pillar 名称列表。"""
        return list(getattr(self, "_strategies", {}).keys())

    # --- 内部工具 ---

    @staticmethod
    def _round_qty(qty: int, lot_size: int) -> int:
        """按 lot_size 向下取整（始终返回 >= 0）。"""
        if qty <= 0:
            return 0
        if lot_size <= 1:
            return qty
        return (qty // lot_size) * lot_size

    def _positions_value(self, state: PillarState) -> float:
        """估算 pillar 持仓市值 — 委托 trading_ops.calc_nav() (§13.11)。

        CB pillar (lot_size=10) 缺价时用面值 100 回退；
        其他 pillar 缺价时用均价回退。
        """
        if not state.positions:
            return 0.0
        pos_list = [
            {
                "code": sym,
                "shares": qty,
                "avg_cost": state.avg_costs.get(sym, 0.0),
            }
            for sym, qty in state.positions.items()
        ]
        fallback = FallbackPolicy.FACE_VALUE if state.lot_size == 10 else FallbackPolicy.AVG_COST
        result = calc_nav(
            cash=0.0,  # 仅计算持仓市值
            positions=pos_list,
            prices=self._last_prices,
            fallback=fallback,
            last_prices=self._last_prices,
        )
        if result.unpriced_codes:
            for sym in result.unpriced_codes:
                logger.warning(
                    "Pillar '%s' %s 缺行情, fallback=%s",
                    state.name, sym, fallback.value,
                )
        return result.position_value

    def _compute_nav(self, state: PillarState) -> float:
        """计算 pillar 真实 NAV = cash + 持仓市值。

        R92 P0-A 核心修复: NAV 不再存储，每次按需计算。
        """
        return state.cash + self._positions_value(state)

    def snapshot_prev_navs(self) -> None:
        """将当前 NAV 记为下一周期的前值。"""
        for state in self._pillar_states.values():
            state.prev_nav = self._compute_nav(state)

    def get_pillar_nav(self, pillar_name: str) -> float:
        """返回 pillar 当前 NAV (cash + 持仓市值)。"""
        state = self._pillar_states.get(pillar_name)
        return self._compute_nav(state) if state else 0.0


# =====================================================================
# CBCompositeStrategy — CB pillar 内多子策略混合
# =====================================================================

class CBCompositeStrategy(Strategy):
    """CB Pillar 内多子策略混合。

    将 triple_low:50% + small_size:50% 等子策略的 intents 合并。
    每个子策略按权重分配虚拟资金，intents 按比例聚合。
    """

    name = "cb_composite"

    def __init__(
        self,
        sub_configs: list[dict],
        registry: Any = None,
    ) -> None:
        from aipha.strategy.registry import StrategyRegistry

        self._registry = registry or StrategyRegistry()
        self._allocations: list[tuple[Strategy, float]] = []

        for cfg in sub_configs:
            name = cfg.get("name")
            cls = self._registry.get(name)
            if cls is None:
                logger.warning(f"CB 子策略 '{name}' 未注册，跳过")
                continue

            params = {
                k: v for k, v in cfg.items()
                if k not in {"name", "weight"}
            }
            try:
                strategy = cls(**params)
                weight = float(cfg.get("weight", 0.0))
                self._allocations.append((strategy, weight))
            except Exception as e:
                logger.error(f"CB 子策略 '{name}' 创建失败: {e}")

        total_weight = sum(w for _, w in self._allocations)
        if total_weight > 0:
            self._allocations = [
                (s, w / total_weight) for s, w in self._allocations
            ]

    def on_bar(
        self,
        bars: dict[str, Any],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """合并多个 CB 子策略的 intents。"""
        merged: list[OrderIntent] = []

        for strategy, weight in self._allocations:
            sub_ctx = StrategyContext(
                current_time=context.current_time,
                positions=dict(context.positions),
                cash=context.cash * weight,
                nav=context.nav * weight,
                metadata={"sub_weight": weight},
            )
            try:
                intents = strategy.on_bar(bars, sub_ctx)
            except Exception as e:
                logger.error(f"CB 子策略 '{strategy.name}' on_bar 异常: {e}")
                continue

            for intent in intents:
                scaled_qty = max(0, int(intent.qty * weight))
                if scaled_qty <= 0:
                    continue
                meta = dict(intent.metadata) if intent.metadata else {}
                meta["cb_sub_strategy"] = strategy.name
                merged.append(
                    OrderIntent(
                        symbol=intent.symbol,
                        side=intent.side,
                        qty=scaled_qty,
                        intent_type=intent.intent_type,
                        limit_price=intent.limit_price,
                        reason=intent.reason,
                        signal_strength=intent.signal_strength,
                        metadata=meta,
                    )
                )

        return merged

    def on_start(self) -> None:
        for strategy, _weight in self._allocations:
            strategy.on_start()

    def on_end(self) -> None:
        for strategy, _weight in self._allocations:
            strategy.on_end()


# =====================================================================
# StaticHoldStrategy — 静态持仓买入持有 (Bond/Gold)
# =====================================================================

class StaticHoldStrategy(Strategy):
    """静态持仓策略：按目标权重买入并定期再平衡。

    Pillar: bond (多久期 ETF)、gold (单标的)。
    """

    name = "static_hold"

    def __init__(
        self,
        symbols: list[str],
        symbol_weights: dict[str, float] | None = None,
        rebalance_days: int = 20,
        **kwargs: Any,
    ) -> None:
        self._symbols = list(symbols)
        self._symbol_weights = dict(symbol_weights or {})
        self._rebalance_days = max(1, int(rebalance_days))
        self._last_rebalance_date: date | None = None
        self._day_count = 0

        if not self._symbol_weights:
            equal_weight = 1.0 / len(self._symbols) if self._symbols else 0.0
            self._symbol_weights = {sym: equal_weight for sym in self._symbols}

    def on_bar(
        self,
        bars: dict[str, Any],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """检查是否需要再平衡，生成调仓指令。

        使用日期驱动（非 bar 计次），避免 paper 引擎分钟级 bar 导致
        过度再平衡。每个自然日只计数一次。
        """
        today = context.current_time.date()

        # 新日期才计数，同一天内多次 on_bar 不重复计数
        if self._last_rebalance_date != today:
            self._day_count += 1

        # 判断是否为再平衡日
        is_rebalance = (
            self._last_rebalance_date is None  # 首次
            or (
                self._last_rebalance_date != today
                and self._day_count % self._rebalance_days == 1
            )
        )

        if not is_rebalance or self._last_rebalance_date == today:
            return []

        self._last_rebalance_date = today

        intents: list[OrderIntent] = []
        if not self._symbols:
            return intents

        total_nav = max(context.nav, 0.0)
        for sym in self._symbols:
            bar = bars.get(sym)
            if bar is None or getattr(bar, "close", 0) <= 0:
                continue

            price = float(bar.close)
            target_weight = self._symbol_weights.get(sym, 0.0)
            target_value = total_nav * target_weight
            current_qty = context.positions.get(sym, 0)
            current_value = current_qty * price
            diff_value = target_value - current_value
            # P2-12 修复: 按 lot_size 取整
            raw_qty = int(diff_value / price)
            lot = MARKET_RULES.get("etf", {}).get("lot_size", 100)
            qty = (raw_qty // lot) * lot

            if qty > 0:
                intents.append(OrderIntent(symbol=sym, side=Side.BUY, qty=qty))
            elif qty < 0:
                intents.append(OrderIntent(symbol=sym, side=Side.SELL, qty=abs(qty)))

        return intents
