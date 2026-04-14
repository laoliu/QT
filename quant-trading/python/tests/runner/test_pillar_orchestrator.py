from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from aipha.domain.models import Bar, Side
from aipha.runner.paper_engine import PaperEngineConfig, PillarConfig, run_paper_trading
from aipha.runner.pillar_orchestrator import PillarOrchestrator
from aipha.strategy.base import OrderIntent, StrategyContext


class DummyStrategy:
    name = "dummy"

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model_reloaded = False

    def on_start(self):
        pass

    def on_end(self):
        pass

    def on_fill(self, symbol, side, qty, price):
        pass

    def on_bar(self, bars, context):
        sym = next(iter(bars))
        return [OrderIntent(symbol=sym, side=Side.BUY, qty=123)]

    def _load_model(self):
        self.model_reloaded = True


class DummyRegistry:
    def get(self, name):
        return DummyStrategy


def make_bar(symbol: str, close: float = 100.0) -> Bar:
    now = datetime(2026, 3, 18, 10, 0, 0)
    return Bar(
        symbol=symbol,
        start_ts=now,
        end_ts=now,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1000,
    )


def test_pillar_orchestrator_aggregates_and_rounds_intents():
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.3, market_type="convertible_bond",
                            sub_strategies=[{"name": "triple_low", "weight": 1.0}]),
        "gold": PillarConfig(name="gold", weight=0.2, market_type="etf",
                              static_symbols=["518880.SH"]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orchestrator = PillarOrchestrator(pillars, {"cb": 0.3, "gold": 0.2}, 1_000_000)

    bars = {"110001.SH": make_bar("110001.SH"), "518880.SH": make_bar("518880.SH", 5.0)}
    ctx = StrategyContext(current_time=datetime(2026, 3, 18, 10, 0, 0), positions={}, cash=1_000_000, nav=1_000_000)
    intents = orchestrator.on_bar(bars, ctx)

    assert len(intents) == 2
    assert intents[0].qty % 10 == 0
    assert intents[1].qty % 100 == 0
    assert {i.metadata["pillar"] for i in intents} == {"cb", "gold"}


def test_pillar_orchestrator_updates_nav_and_switches_ml_model():
    pillars = {
        "ml": PillarConfig(name="ml", weight=0.3, market_type="a_stock", model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orchestrator = PillarOrchestrator(pillars, {"ml": 0.3}, 1_000_000)

    orchestrator._symbol_pillar["000001.SZ"] = "ml"
    orchestrator.on_fill("000001.SZ", "BUY", 100, 10.0)
    ml_state = orchestrator.pillar_states["ml"]
    assert ml_state.positions["000001.SZ"] == 100
    # R92: cash 减少（买入消耗资金），computed NAV = cash + positions_value
    assert ml_state.cash < ml_state.prev_nav

    current_nav = orchestrator.get_pillar_nav("ml")
    ml_state.prev_nav = current_nav - 500.0
    assert orchestrator.get_pillar_daily_return("ml") > 0
    assert orchestrator.switch_ml_model("models/b") is True


def test_run_paper_trading_uses_pillar_orchestrator(tmp_path: Path):
    cfg = PaperEngineConfig(
        strategy_name="plan_a_5comp",
        symbols=["518880.SH"],
        pillar_configs={"gold": PillarConfig(name="gold", weight=0.2, market_type="etf", static_symbols=["518880.SH"])} ,
        portfolio_weights={"gold": 0.2},
        state_dir=tmp_path / "state",
        audit_log_dir=tmp_path / "audit",
    )

    with patch("aipha.runner.paper_engine.PaperEngineConfig.from_yaml", return_value=cfg), \
         patch("aipha.runner.paper_engine.PaperEngine") as engine_cls, \
         patch("aipha.runner.paper_engine.asyncio.run") as asyncio_run:
        engine_cls.return_value.run = MagicMock(return_value="done")
        run_paper_trading(tmp_path / "dummy.yml")

    strategy = engine_cls.call_args.args[1]
    assert isinstance(strategy, PillarOrchestrator)
    assert asyncio_run.called


# ===================================================================
# 增强测试 — CBCompositeStrategy / StaticHoldStrategy / NAV 追踪
# ===================================================================

from aipha.runner.pillar_orchestrator import (
    CBCompositeStrategy,
    StaticHoldStrategy,
    PillarState,
    MARKET_RULES,
)


def test_cb_composite_merges_sub_strategies():
    """CBCompositeStrategy 应合并多个子策略 intents。"""
    sub_configs = [
        {"name": "triple_low", "weight": 0.5, "top_k": 20},
        {"name": "small_size", "weight": 0.5, "top_k": 4},
    ]
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        cb = CBCompositeStrategy(sub_configs=sub_configs)

    assert len(cb._allocations) == 2
    # 权重归一化后应各 0.5
    assert abs(cb._allocations[0][1] - 0.5) < 0.01

    bars = {"110001.SH": make_bar("110001.SH")}
    ctx = StrategyContext(
        current_time=datetime(2026, 3, 18), positions={},
        cash=100_000, nav=100_000,
    )
    intents = cb.on_bar(bars, ctx)
    assert len(intents) == 2
    for i in intents:
        assert "cb_sub_strategy" in i.metadata


def test_static_hold_first_bar_rebalances():
    """StaticHoldStrategy 首 bar 应产生买入指令。"""
    sh = StaticHoldStrategy(
        symbols=["518880.SH", "511010.SH"],
        symbol_weights={"518880.SH": 0.6, "511010.SH": 0.4},
        rebalance_days=20,
    )
    bars = {
        "518880.SH": make_bar("518880.SH", 5.0),
        "511010.SH": make_bar("511010.SH", 1.0),
    }
    ctx = StrategyContext(
        current_time=datetime(2026, 3, 18), positions={},
        cash=100_000, nav=100_000,
    )
    intents = sh.on_bar(bars, ctx)
    assert len(intents) == 2
    # 应该是 BUY 指令
    assert all(i.side == Side.BUY for i in intents)


def test_static_hold_skips_non_rebalance_bars():
    """非调仓日不产生指令，同一天多次 bar 也不重复。"""
    sh = StaticHoldStrategy(symbols=["518880.SH"], rebalance_days=5)
    bars = {"518880.SH": make_bar("518880.SH", 5.0)}
    ctx_day1 = StrategyContext(
        current_time=datetime(2026, 3, 18, 10, 0), positions={},
        cash=100_000, nav=100_000,
    )
    # 第一个 bar (day1, 首次) → 触发 rebalance
    intents1 = sh.on_bar(bars, ctx_day1)
    assert len(intents1) >= 1
    # 同一天第二个 bar → 不触发
    ctx_day1_bar2 = StrategyContext(
        current_time=datetime(2026, 3, 18, 10, 1), positions={},
        cash=100_000, nav=100_000,
    )
    intents2 = sh.on_bar(bars, ctx_day1_bar2)
    assert len(intents2) == 0
    # 第二天 → 不触发（day_count=2, 2%5!=1）
    ctx_day2 = StrategyContext(
        current_time=datetime(2026, 3, 19, 10, 0), positions={},
        cash=100_000, nav=100_000,
    )
    intents3 = sh.on_bar(bars, ctx_day2)
    assert len(intents3) == 0


def test_pillar_orchestrator_on_fill_routes_correctly():
    """on_fill 应正确归属到对应 pillar 并更新持仓（含交易成本）。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.3, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.3}, 1_000_000)

    orch._symbol_pillar["110001.SH"] = "cb"
    orch.on_fill("110001.SH", Side.BUY, 50, 100.0, commission=2.5)

    state = orch.pillar_states["cb"]
    assert state.positions["110001.SH"] == 50
    # R92: 买入后 cash = capital - trade_value - commission
    expected_cash = 300_000 - 50 * 100.0 - 2.5
    assert abs(state.cash - expected_cash) < 0.01
    # computed NAV = cash + positions_value (50 * 100.0)
    expected_nav = expected_cash + 50 * 100.0
    assert abs(orch.get_pillar_nav("cb") - expected_nav) < 0.01

    # 卖出应减少持仓，cash 增加（扣费用）
    orch.on_fill("110001.SH", Side.SELL, 30, 105.0, commission=1.5)
    assert state.positions["110001.SH"] == 20
    expected_cash2 = expected_cash + 30 * 105.0 - 1.5
    assert abs(state.cash - expected_cash2) < 0.01


def test_pillar_orchestrator_on_fill_unknown_symbol():
    """on_fill 收到未知 symbol 应安全忽略。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.3, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.3}, 1_000_000)

    # 不应抛异常
    orch.on_fill("UNKNOWN.SH", Side.BUY, 100, 50.0)


def test_pillar_daily_return_calculation():
    """get_pillar_daily_return 应基于 prev_nav / nav 计算。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.3, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.3}, 1_000_000)

    state = orch.pillar_states["cb"]
    state.prev_nav = 300_000
    # R92: NAV = cash + positions_value；设置 cash=315000 无持仓 → NAV=315000
    state.cash = 315_000  # +5%
    assert abs(orch.get_pillar_daily_return("cb") - 0.05) < 1e-6

    # 不存在的 pillar
    assert orch.get_pillar_daily_return("nonexistent") == 0.0


def test_pillar_weight_update():
    """update_pillar_weight 应更新权重和虚拟资金。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.3, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.3}, 1_000_000)

    assert orch._portfolio_weights["cb"] == 0.3
    orch.update_pillar_weight("cb", 0.15)
    assert orch._portfolio_weights["cb"] == 0.15
    assert orch.pillar_states["cb"].capital == 150_000

    # 不存在的 pillar 应安全忽略
    orch.update_pillar_weight("nonexistent", 0.5)


def test_snapshot_prev_navs():
    """snapshot_prev_navs 应将所有 pillar 的当前 NAV 设为 prev_nav。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.3, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.3}, 1_000_000)

    state = orch.pillar_states["cb"]
    # R92: 设置 cash=320000 无持仓 → NAV=320000
    state.cash = 320_000
    orch.snapshot_prev_navs()
    assert state.prev_nav == 320_000


def test_market_rules_correct():
    """MARKET_RULES 应正确映射资产类型。"""
    assert MARKET_RULES["convertible_bond"]["lot_size"] == 10
    assert MARKET_RULES["convertible_bond"]["t_plus_n"] == 0
    assert MARKET_RULES["a_stock"]["lot_size"] == 100
    assert MARKET_RULES["a_stock"]["t_plus_n"] == 1
    assert MARKET_RULES["etf"]["lot_size"] == 100


def test_round_qty():
    """_round_qty 应按 lot_size 向下取整，负数返回 0。"""
    assert PillarOrchestrator._round_qty(123, 10) == 120
    assert PillarOrchestrator._round_qty(123, 100) == 100
    assert PillarOrchestrator._round_qty(99, 100) == 0
    assert PillarOrchestrator._round_qty(50, 1) == 50
    # NEW-8: 负数防护
    assert PillarOrchestrator._round_qty(-50, 100) == 0
    assert PillarOrchestrator._round_qty(-1, 10) == 0
    assert PillarOrchestrator._round_qty(0, 100) == 0


def test_cb_composite_skips_unregistered():
    """CBCompositeStrategy 应跳过未注册的子策略。"""
    class EmptyRegistry:
        def get(self, name):
            return None

    cb = CBCompositeStrategy(
        sub_configs=[{"name": "nonexistent", "weight": 1.0}],
        registry=EmptyRegistry(),
    )
    assert len(cb._allocations) == 0


def test_active_pillars_property():
    """active_pillars 应返回已激活的名称列表。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.3, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
        "gold": PillarConfig(name="gold", weight=0.15, market_type="etf",
                              static_symbols=["518880.SH"]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.3, "gold": 0.15}, 1_000_000)

    assert set(orch.active_pillars) == {"cb", "gold"}


def test_on_start_on_end_called():
    """on_start/on_end 应传播到所有子策略。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.3, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.3}, 1_000_000)

    # 不应抛出异常
    orch.on_start()
    orch.on_end()


# ===================================================================
# R91 NEW-9: 关键缺失测试场景补全
# ===================================================================


def test_on_fill_with_commission_deducted_from_nav():
    """NEW-1 修复验证：on_fill 应从 NAV 中扣除交易费用。"""
    pillars = {
        "ml": PillarConfig(name="ml", weight=0.3, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"ml": 0.3}, 1_000_000)

    initial_cash = orch.pillar_states["ml"].cash  # 300,000
    orch._symbol_pillar["000001.SZ"] = "ml"

    # R92: 买入 → cash -= trade_value + commission
    orch.on_fill("000001.SZ", Side.BUY, 100, 30.0, commission=10.0)
    expected_cash = initial_cash - 100 * 30.0 - 10.0
    assert abs(orch.pillar_states["ml"].cash - expected_cash) < 0.01
    # NAV = cash + positions_value，佣金是真正的成本
    expected_nav = expected_cash + 100 * 30.0  # position value at fill price
    assert abs(orch.get_pillar_nav("ml") - expected_nav) < 0.01

    # R92: 卖出 → cash += trade_value - commission - stamp_tax (A股 0.05%)
    orch.on_fill("000001.SZ", Side.SELL, 50, 35.0, commission=8.5)
    stamp_tax = 50 * 35.0 * 0.0005  # A股卖出印花税
    expected_cash2 = expected_cash + 50 * 35.0 - 8.5 - stamp_tax
    assert abs(orch.pillar_states["ml"].cash - expected_cash2) < 0.01


def test_positions_value_uses_last_price():
    """NEW-3 修复验证：_positions_value 应使用最后成交价而非硬编码。"""
    pillars = {
        "ml": PillarConfig(name="ml", weight=0.3, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"ml": 0.3}, 1_000_000)

    state = orch.pillar_states["ml"]
    orch._symbol_pillar["000001.SZ"] = "ml"

    # 买入记录最后价格
    orch.on_fill("000001.SZ", Side.BUY, 200, 50.0)
    value = orch._positions_value(state)
    assert abs(value - 200 * 50.0) < 0.01  # 非旧的 200*10=2000


def test_t1_buy_date_tracking():
    """T+1 验证：on_fill BUY 记录 buy_dates，SELL 清除 buy_dates。
    CB(t_plus_n=0) 和 A股(t_plus_n=1) 状态都会记录，但过滤逻辑仅在 t_plus_n>0 时生效。
    """
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.5, market_type="convertible_bond",
                            sub_strategies=[{"name": "triple_low", "weight": 1.0}]),
        "ml": PillarConfig(name="ml", weight=0.5, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.5, "ml": 0.5}, 1_000_000)

    orch._symbol_pillar["128001.SZ"] = "cb"
    orch._symbol_pillar["000001.SZ"] = "ml"

    # 买入 → buy_dates 被记录
    orch.on_fill("128001.SZ", Side.BUY, 100, 110.0)
    orch.on_fill("000001.SZ", Side.BUY, 200, 30.0)

    assert "128001.SZ" in orch.pillar_states["cb"].buy_dates
    assert "000001.SZ" in orch.pillar_states["ml"].buy_dates

    # CB: t_plus_n=0, A股: t_plus_n=1
    assert orch.pillar_states["cb"].t_plus_n == 0
    assert orch.pillar_states["ml"].t_plus_n == 1

    # 卖出 → buy_dates 被清除
    orch.on_fill("128001.SZ", Side.SELL, 100, 115.0)
    assert "128001.SZ" not in orch.pillar_states["cb"].buy_dates
    assert orch.pillar_states["cb"].positions.get("128001.SZ", 0) == 0


def test_multi_pillar_same_symbol_on_fill():
    """多 pillar 持有同一标的（如 ETF 511010.SH），on_fill 路由到正确的 pillar。"""
    pillars = {
        "etf": PillarConfig(name="etf", weight=0.4, market_type="etf",
                            static_symbols=["511010.SH"]),
        "bond": PillarConfig(name="bond", weight=0.6, market_type="bond",
                             static_symbols=["511260.SH"]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"etf": 0.4, "bond": 0.6}, 1_000_000)

    # 两个 pillar 映射同一 symbol —— 实际中这取决于谁先挂单
    # 但 _symbol_pillar 只存一份，后者覆盖前者
    orch._symbol_pillar["511010.SH"] = "etf"
    orch.on_fill("511010.SH", Side.BUY, 1000, 1.0, commission=1.0)

    assert orch.pillar_states["etf"].positions.get("511010.SH") == 1000
    assert orch.pillar_states["bond"].positions.get("511010.SH", 0) == 0


def test_gateway_lot_size_cb_compatible():
    """NEW-2 修复验证：CB pillar lot_size=10，可以交易 qty=30/50 等非 100 整数。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "triple_low", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry", return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 500_000)

    state = orch.pillar_states["cb"]
    assert state.lot_size == 10  # CB lot_size 应为 10

    # 30 张 CB 应能正确取整（30 // 10 * 10 = 30）
    assert orch._round_qty(30, 10) == 30
    assert orch._round_qty(35, 10) == 30  # 35 → 30
    assert orch._round_qty(9, 10) == 0    # 不足 1 手 → 0


def test_reload_model_preferred_path():
    """NEW-5 修复验证：switch_ml_model 优先调用策略的 reload_model() 方法。"""
    pillars = {
        "ml": PillarConfig(name="ml", weight=1.0, market_type="a_stock",
                            model_dir="models/a"),
    }

    class ReloadableStrategy(DummyStrategy):
        """支持 reload_model 公开接口的策略。"""
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.reloaded_dir = None

        def reload_model(self, model_dir: str) -> bool:
            self.reloaded_dir = model_dir
            return True

    class ReloadableRegistry:
        def get(self, name: str):
            return ReloadableStrategy

    with patch("aipha.strategy.registry.StrategyRegistry", return_value=ReloadableRegistry()):
        orch = PillarOrchestrator(pillars, {"ml": 1.0}, 500_000)

    ok = orch.switch_ml_model("models/b")
    assert ok is True
    strategy = orch._strategies["ml"]
    assert strategy.reloaded_dir == "models/b"


def test_etf_fallback_warning(caplog):
    """NEW-6 修复验证：ETF 策略名不存在时，降级并记录 warning 日志。"""
    pillars = {
        "etf": PillarConfig(name="etf", weight=1.0, market_type="etf",
                            strategy_name="nonexistent_etf_v99"),
    }

    class FallbackRegistry:
        def get(self, name: str):
            if name == "nonexistent_etf_v99":
                return None  # 模拟未注册
            return DummyStrategy

    import logging
    with caplog.at_level(logging.WARNING):
        with patch("aipha.strategy.registry.StrategyRegistry",
                   return_value=FallbackRegistry()):
            orch = PillarOrchestrator(pillars, {"etf": 1.0}, 500_000)

    # 应有降级 warning
    assert any("降级" in r.message or "未注册" in r.message for r in caplog.records)


# ===================================================================
# R92 新增测试：_compute_nav / 印花税 / market_date / 超卖保护
# ===================================================================


def test_compute_nav_returns_cash_plus_positions_value():
    """R92 T1-1: _compute_nav = cash + positions_value。"""
    pillars = {
        "ml": PillarConfig(name="ml", weight=1.0, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"ml": 1.0}, 1_000_000)

    state = orch.pillar_states["ml"]
    # 无持仓时 NAV == cash
    assert abs(orch.get_pillar_nav("ml") - 1_000_000) < 0.01

    # 模拟买入后: cash 减少, 持仓增加
    orch._symbol_pillar["000001.SZ"] = "ml"
    orch.on_fill("000001.SZ", Side.BUY, 200, 25.0)
    expected_cash = 1_000_000 - 200 * 25.0
    expected_nav = expected_cash + 200 * 25.0  # cash + positions
    assert abs(orch.get_pillar_nav("ml") - expected_nav) < 0.01


def test_a_stock_sell_stamp_tax():
    """R92 T1-2: A股卖出应扣印花税（千分之0.5），买入不扣。"""
    pillars = {
        "ml": PillarConfig(name="ml", weight=1.0, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"ml": 1.0}, 500_000)

    orch._symbol_pillar["600036.SH"] = "ml"
    state = orch.pillar_states["ml"]

    # 买入: 无印花税
    orch.on_fill("600036.SH", Side.BUY, 100, 40.0, commission=5.0)
    buy_cash = 500_000 - 100 * 40.0 - 5.0
    assert abs(state.cash - buy_cash) < 0.01

    # 卖出: 有印花税 (0.05%)
    orch.on_fill("600036.SH", Side.SELL, 100, 42.0, commission=5.0)
    stamp_tax = 100 * 42.0 * 0.0005
    sell_cash = buy_cash + 100 * 42.0 - 5.0 - stamp_tax
    assert abs(state.cash - sell_cash) < 0.01


def test_cb_sell_no_stamp_tax():
    """R92 T1-2: CB卖出不收印花税。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 300_000)

    orch._symbol_pillar["110001.SH"] = "cb"
    state = orch.pillar_states["cb"]

    orch.on_fill("110001.SH", Side.BUY, 50, 100.0)
    orch.on_fill("110001.SH", Side.SELL, 50, 105.0, commission=1.0)
    # CB 无印花税: cash = initial - buy_value + sell_value - commission
    expected = 300_000 - 50 * 100.0 + 50 * 105.0 - 1.0
    assert abs(state.cash - expected) < 0.01


def test_on_fill_market_date_parameter():
    """R92 T1-3: on_fill 应使用 market_date 记录买入日期。"""
    from datetime import date as _date
    pillars = {
        "ml": PillarConfig(name="ml", weight=1.0, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"ml": 1.0}, 1_000_000)

    orch._symbol_pillar["000001.SZ"] = "ml"
    test_date = _date(2026, 1, 15)
    orch.on_fill("000001.SZ", Side.BUY, 100, 10.0, market_date=test_date)

    state = orch.pillar_states["ml"]
    assert state.buy_dates["000001.SZ"] == test_date


def test_on_fill_sell_overcount_protection():
    """R92 P1-5: 卖出数量不能超过持仓量。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 500_000)

    orch._symbol_pillar["110001.SH"] = "cb"
    # 买入 30 张
    orch.on_fill("110001.SH", Side.BUY, 30, 100.0)
    state = orch.pillar_states["cb"]
    assert state.positions["110001.SH"] == 30

    # 尝试卖出 50 张（超过持仓），应只卖出 30 张
    cash_before = state.cash
    orch.on_fill("110001.SH", Side.SELL, 50, 105.0)
    # 持仓应清零
    assert "110001.SH" not in state.positions
    # 现金只增加 30 张 * 105
    expected_cash = cash_before + 30 * 105.0
    assert abs(state.cash - expected_cash) < 0.01


def test_static_symbol_not_overridden_by_dynamic():
    """R92 T2-3: 静态标的的 pillar 归属不被动态 intent 覆盖。"""
    pillars = {
        "bond": PillarConfig(name="bond", weight=0.1, market_type="bond",
                              static_symbols=["511010.SH"]),
        "etf": PillarConfig(name="etf", weight=0.15, market_type="etf",
                             strategy_name="dummy_etf"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"bond": 0.1, "etf": 0.15}, 1_000_000)

    # 511010.SH 应归属 bond (静态注册)
    assert orch._symbol_pillar.get("511010.SH") == "bond"
    assert "511010.SH" in orch._static_symbol_set

    # 模拟 ETF 策略想发出 511010.SH 的 intent — 归属不应改变
    # (在 on_bar 中 intent.symbol 不会覆盖 static)
    # 直接验证集合机制
    assert orch._static_symbol_set == {"511010.SH"}


def test_market_rules_stamp_tax_rates():
    """R92 T1-2: MARKET_RULES 包含正确的印花税率。"""
    assert MARKET_RULES["a_stock"]["stamp_tax_sell_rate"] == 0.0005
    assert MARKET_RULES["convertible_bond"]["stamp_tax_sell_rate"] == 0.0
    assert MARKET_RULES["etf"]["stamp_tax_sell_rate"] == 0.0
    assert MARKET_RULES["bond"]["stamp_tax_sell_rate"] == 0.0


def test_etf_sell_no_stamp_tax():
    """R92: ETF 卖出不收印花税。"""
    pillars = {
        "etf": PillarConfig(name="etf", weight=1.0, market_type="etf",
                             static_symbols=["159915.SZ"]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"etf": 1.0}, 200_000)

    state = orch.pillar_states["etf"]
    orch.on_fill("159915.SZ", Side.BUY, 1000, 3.5)
    orch.on_fill("159915.SZ", Side.SELL, 1000, 3.6, commission=2.0)
    expected = 200_000 - 1000 * 3.5 + 1000 * 3.6 - 2.0
    assert abs(state.cash - expected) < 0.01


def test_multi_pillar_nav_isolation():
    """R92: 各 pillar 的 NAV 应互相隔离。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=0.5, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
        "ml": PillarConfig(name="ml", weight=0.5, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 0.5, "ml": 0.5}, 1_000_000)

    # 各 pillar 初始 NAV = capital
    assert abs(orch.get_pillar_nav("cb") - 500_000) < 0.01
    assert abs(orch.get_pillar_nav("ml") - 500_000) < 0.01

    # CB 买入不影响 ML
    orch._symbol_pillar["110001.SH"] = "cb"
    orch.on_fill("110001.SH", Side.BUY, 50, 100.0, commission=5.0)
    cb_nav = orch.get_pillar_nav("cb")
    ml_nav = orch.get_pillar_nav("ml")
    assert cb_nav < 500_000  # CB 扣了佣金
    assert abs(ml_nav - 500_000) < 0.01  # ML 不受影响


def test_snapshot_stores_computed_nav():
    """R92: snapshot_prev_navs 应存储 cash + positions_value。"""
    pillars = {
        "ml": PillarConfig(name="ml", weight=1.0, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"ml": 1.0}, 1_000_000)

    orch._symbol_pillar["000001.SZ"] = "ml"
    orch.on_fill("000001.SZ", Side.BUY, 100, 50.0, commission=10.0)
    # NAV = cash(994990) + pos(5000) = 999990
    orch.snapshot_prev_navs()
    state = orch.pillar_states["ml"]
    expected_nav = state.cash + 100 * 50.0
    assert abs(state.prev_nav - expected_nav) < 0.01


def test_get_pillar_nav_nonexistent_pillar():
    """R92: 不存在的 pillar 返回 0.0。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 1_000_000)

    assert orch.get_pillar_nav("nonexistent") == 0.0


def test_on_fill_sell_empty_position():
    """R92: 卖出持仓为0的标的应安全处理。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 300_000)

    orch._symbol_pillar["110001.SH"] = "cb"
    cash_before = orch.pillar_states["cb"].cash
    # 卖出不存在的持仓：actual_sell=min(100,0)=0
    orch.on_fill("110001.SH", Side.SELL, 100, 100.0)
    # 现金应不变（actual_value=0, no stamp_tax）
    assert abs(orch.pillar_states["cb"].cash - cash_before) < 0.01


def test_on_fill_zero_commission():
    """R92: commission=0 应正常工作。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 100_000)

    orch._symbol_pillar["110001.SH"] = "cb"
    orch.on_fill("110001.SH", Side.BUY, 10, 100.0, commission=0.0)
    state = orch.pillar_states["cb"]
    assert abs(state.cash - (100_000 - 10 * 100.0)) < 0.01


def test_daily_return_zero_prev_nav():
    """R92: prev_nav=0 时日收益应返回 0.0 避免除零。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 0.0)

    assert orch.get_pillar_daily_return("cb") == 0.0


def test_multiple_buys_accumulate_position():
    """R92: 多次买入应累加持仓。"""
    pillars = {
        "ml": PillarConfig(name="ml", weight=1.0, market_type="a_stock",
                            model_dir="models/a"),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"ml": 1.0}, 1_000_000)

    orch._symbol_pillar["000001.SZ"] = "ml"
    orch.on_fill("000001.SZ", Side.BUY, 100, 10.0)
    orch.on_fill("000001.SZ", Side.BUY, 200, 11.0)
    state = orch.pillar_states["ml"]
    assert state.positions["000001.SZ"] == 300
    # last_prices 应是最后一次成交价
    assert orch._last_prices["000001.SZ"] == 11.0


def test_partial_sell_leaves_remaining():
    """R92: 部分卖出后应保留剩余持仓。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 500_000)

    orch._symbol_pillar["110001.SH"] = "cb"
    orch.on_fill("110001.SH", Side.BUY, 100, 100.0)
    orch.on_fill("110001.SH", Side.SELL, 40, 105.0)

    state = orch.pillar_states["cb"]
    assert state.positions["110001.SH"] == 60
    # buy_dates 不应被清除（仍有持仓）
    assert "110001.SH" in state.buy_dates


def test_full_sell_clears_position_and_buy_dates():
    """R92: 全部卖出应清除持仓和 buy_dates。"""
    pillars = {
        "cb": PillarConfig(name="cb", weight=1.0, market_type="convertible_bond",
                            sub_strategies=[{"name": "x", "weight": 1.0}]),
    }
    with patch("aipha.strategy.registry.StrategyRegistry",
               return_value=DummyRegistry()):
        orch = PillarOrchestrator(pillars, {"cb": 1.0}, 500_000)

    orch._symbol_pillar["110001.SH"] = "cb"
    orch.on_fill("110001.SH", Side.BUY, 50, 100.0)
    orch.on_fill("110001.SH", Side.SELL, 50, 105.0)

    state = orch.pillar_states["cb"]
    assert "110001.SH" not in state.positions
    assert "110001.SH" not in state.buy_dates


# ===================================================================
# R92 Gateway Order TTL 测试
# ===================================================================


def test_gateway_order_ttl_auto_cancel():
    """R92 T3-1: 超过 TTL bars 的挂单应自动取消。"""
    from aipha.execution.paper_gateway import PaperTradingGateway
    from aipha.domain.models import Bar
    from decimal import Decimal
    from datetime import datetime

    gw = PaperTradingGateway(
        initial_capital=Decimal("500000"),
        order_ttl_bars=3,
        lot_size=10,
    )
    gw.connect()

    # 提交限价单（不会立即成交）
    from aipha.execution.paper_gateway import GatewayOrder
    order = GatewayOrder(
        order_id="test_ttl_1",
        symbol="110001.SH",
        side="BUY",
        qty=10,
        order_type="LIMIT",
        limit_price=95.0,  # 低于市价
    )
    gw.submit_order(order)
    assert len(gw.get_active_orders()) == 1

    # 发送 4 个 bar（高于限价，不成交），第 4 个 bar 后超过 TTL
    for i in range(4):
        bar = Bar(
            symbol="110001.SH",
            open=100.0, high=102.0, low=99.0, close=101.0,
            volume=10000,
            start_ts=datetime(2026, 1, 1, 9, 30 + i),
            end_ts=datetime(2026, 1, 1, 9, 31 + i),
        )
        gw.process_bar(bar)

    # TTL=3, 提交于 bar 0, 第 4 bar 后 (counter - create > 3) → 取消
    assert len(gw.get_active_orders()) == 0
