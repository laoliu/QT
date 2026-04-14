"""
动量策略单元测试
"""

from datetime import date, datetime, timedelta

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import IntentType, StrategyContext
from aipha.strategy.momentum import MomentumStrategy


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def symbols() -> list[str]:
    """测试股票池"""
    return ["000001.SZ", "000002.SZ", "600000.SH", "600519.SH", "000858.SZ"]


@pytest.fixture
def momentum_strategy(symbols: list[str]) -> MomentumStrategy:
    """创建动量策略实例"""
    return MomentumStrategy(
        name="TestMomentum",
        lookback_days=5,
        top_k=2,
        rebalance_days=3,
        target_symbols=symbols,
    )


def make_bar(symbol: str, dt: datetime, close: float) -> Bar:
    """创建测试 Bar"""
    return Bar(
        symbol=symbol,
        start_ts=dt.replace(hour=9, minute=30),
        end_ts=dt.replace(hour=15),
        open=close * 0.99,
        high=close * 1.01,
        low=close * 0.98,
        close=close,
        volume=1_000_000,
    )


def make_context(
    dt: datetime,
    positions: dict[str, int] | None = None,
    cash: float = 1_000_000,
) -> StrategyContext:
    """创建测试 Context"""
    return StrategyContext(
        current_time=dt,
        positions=positions or {},
        cash=cash,
        nav=cash + sum(
            qty * 10.0 for qty in (positions or {}).values()
        ),
    )


# ============================================================================
# 基础测试
# ============================================================================


class TestMomentumStrategyBasic:
    """动量策略基础测试"""

    def test_strategy_creation(self, momentum_strategy: MomentumStrategy):
        """创建策略实例"""
        assert momentum_strategy.name == "TestMomentum"
        assert momentum_strategy.lookback_days == 5
        assert momentum_strategy.top_k == 2
        assert momentum_strategy.rebalance_days == 3

    def test_strategy_repr(self, momentum_strategy: MomentumStrategy):
        """策略字符串表示"""
        repr_str = repr(momentum_strategy)
        assert "TestMomentum" in repr_str

    def test_on_start_resets_state(self, momentum_strategy: MomentumStrategy):
        """on_start 重置状态"""
        momentum_strategy._bar_count = 10
        momentum_strategy._last_rebalance = date(2025, 1, 1)
        momentum_strategy._price_history = {"000001.SZ": [(date(2025, 1, 1), 10.0)]}

        momentum_strategy.on_start()

        assert momentum_strategy._bar_count == 0
        assert momentum_strategy._last_rebalance is None
        assert momentum_strategy._price_history == {}


# ============================================================================
# 动量计算测试
# ============================================================================


class TestMomentumCalculation:
    """动量计算测试"""

    def test_no_intents_before_lookback(
        self, momentum_strategy: MomentumStrategy, symbols: list[str]
    ):
        """回望期内不生成订单"""
        dt = datetime(2025, 1, 6, 15, 0)
        bars = {s: make_bar(s, dt, 10.0 + i) for i, s in enumerate(symbols)}
        context = make_context(dt)

        # 前几天不应该生成订单
        for day in range(momentum_strategy.lookback_days - 1):
            dt = datetime(2025, 1, 6 + day, 15, 0)
            bars = {s: make_bar(s, dt, 10.0 + i) for i, s in enumerate(symbols)}
            intents = momentum_strategy.on_bar(bars, make_context(dt))
            assert intents == []

    def test_generates_intents_after_lookback(
        self, momentum_strategy: MomentumStrategy, symbols: list[str]
    ):
        """回望期后生成订单"""
        momentum_strategy.on_start()

        # 模拟价格历史：000001 涨最多，000002 次之
        base_prices = {
            "000001.SZ": [10.0, 10.5, 11.0, 11.5, 12.0],  # +20%
            "000002.SZ": [15.0, 15.3, 15.6, 15.9, 16.2],  # +8%
            "600000.SH": [20.0, 19.8, 19.6, 19.4, 19.2],  # -4%
            "600519.SH": [100.0, 99.0, 98.0, 97.0, 96.0], # -4%
            "000858.SZ": [50.0, 50.5, 50.0, 50.5, 50.0],  # 0%
        }

        for day in range(5):
            dt = datetime(2025, 1, 6 + day, 15, 0)
            bars = {
                s: make_bar(s, dt, base_prices[s][day])
                for s in symbols
            }
            intents = momentum_strategy.on_bar(bars, make_context(dt))

        # 第5天应该生成订单（第一次调仓）
        assert len(intents) > 0

        # 应该买入涨幅最大的两只：000001.SZ 和 000002.SZ
        buy_symbols = [i.symbol for i in intents if i.side == Side.BUY]
        assert "000001.SZ" in buy_symbols
        assert "000002.SZ" in buy_symbols

    def test_rebalance_interval(
        self, momentum_strategy: MomentumStrategy, symbols: list[str]
    ):
        """调仓间隔测试"""
        momentum_strategy.on_start()

        # 先填充历史
        for day in range(5):
            dt = datetime(2025, 1, 6 + day, 15, 0)
            bars = {s: make_bar(s, dt, 10.0 + i) for i, s in enumerate(symbols)}
            momentum_strategy.on_bar(bars, make_context(dt))

        # 记录第一次调仓日期
        first_rebalance = momentum_strategy._last_rebalance

        # 接下来2天不应调仓
        for day in range(5, 7):
            dt = datetime(2025, 1, 6 + day, 15, 0)
            bars = {s: make_bar(s, dt, 10.0 + i) for i, s in enumerate(symbols)}
            intents = momentum_strategy.on_bar(bars, make_context(dt))
            assert intents == []  # 不应有新订单

        # 第8天（3天后）应该再次调仓
        dt = datetime(2025, 1, 14, 15, 0)
        bars = {s: make_bar(s, dt, 10.0 + i) for i, s in enumerate(symbols)}
        intents = momentum_strategy.on_bar(bars, make_context(dt))
        # 可能有订单，取决于当前持仓


# ============================================================================
# 持仓调整测试
# ============================================================================


class TestRebalancing:
    """调仓逻辑测试"""

    def test_sells_non_target_positions(self, symbols: list[str]):
        """卖出非目标持仓"""
        strategy = MomentumStrategy(
            name="Test",
            lookback_days=3,
            top_k=2,
            rebalance_days=1,
            target_symbols=symbols,
        )
        strategy.on_start()

        # 填充历史使 000001 和 000002 成为 top 2
        prices = {
            "000001.SZ": [10.0, 11.0, 12.0],  # +20%
            "000002.SZ": [15.0, 16.0, 17.0],  # +13%
            "600000.SH": [20.0, 19.0, 18.0],  # -10%
            "600519.SH": [100.0, 99.0, 98.0],
            "000858.SZ": [50.0, 49.0, 48.0],
        }

        for day in range(3):
            dt = datetime(2025, 1, 6 + day, 15, 0)
            bars = {s: make_bar(s, dt, prices[s][day]) for s in symbols}
            strategy.on_bar(bars, make_context(dt))

        # 模拟当前持有 600000（非 top 2）
        dt = datetime(2025, 1, 9, 15, 0)
        bars = {s: make_bar(s, dt, prices[s][-1] + 0.1) for s in symbols}
        context = make_context(
            dt,
            positions={"600000.SH": 1000},
            cash=500_000,
        )

        intents = strategy.on_bar(bars, context)

        # 应该有卖出 600000 的订单
        sell_intents = [i for i in intents if i.side == Side.SELL]
        sell_symbols = [i.symbol for i in sell_intents]
        assert "600000.SH" in sell_symbols

    def test_lot_size_rounding(self, symbols: list[str]):
        """买入数量取整到 100 股"""
        strategy = MomentumStrategy(
            name="Test",
            lookback_days=3,
            top_k=1,
            rebalance_days=1,
            target_symbols=["000001.SZ"],
        )
        strategy.on_start()

        # 填充历史
        for day in range(3):
            dt = datetime(2025, 1, 6 + day, 15, 0)
            bars = {"000001.SZ": make_bar("000001.SZ", dt, 10.0 + day)}
            strategy.on_bar(bars, make_context(dt))

        # 生成买入订单
        dt = datetime(2025, 1, 9, 15, 0)
        bars = {"000001.SZ": make_bar("000001.SZ", dt, 12.0)}
        intents = strategy.on_bar(bars, make_context(dt, cash=100_000))

        for intent in intents:
            if intent.side == Side.BUY:
                assert intent.qty % 100 == 0


# ============================================================================
# 边界条件测试
# ============================================================================


class TestEdgeCases:
    """边界条件测试"""

    def test_empty_bars(self, momentum_strategy: MomentumStrategy):
        """空 bars 处理"""
        context = make_context(datetime(2025, 1, 6, 15, 0))
        intents = momentum_strategy.on_bar({}, context)
        assert intents == []

    def test_missing_symbol_in_bars(self, momentum_strategy: MomentumStrategy):
        """部分标的缺失"""
        momentum_strategy.on_start()
        dt = datetime(2025, 1, 6, 15, 0)
        # 只提供部分标的
        bars = {"000001.SZ": make_bar("000001.SZ", dt, 10.0)}
        context = make_context(dt)

        # 不应报错
        intents = momentum_strategy.on_bar(bars, context)
        assert isinstance(intents, list)

    def test_zero_old_price_excluded_from_scores(self):
        """起始价格为 0 时不应参与动量排名（除零保护）"""
        strategy = MomentumStrategy(
            name="Test",
            lookback_days=3,
            top_k=1,
            rebalance_days=1,
            target_symbols=["ZERO.SZ", "GOOD.SZ"],
        )
        strategy.on_start()

        for day in range(3):
            dt = datetime(2025, 1, 6 + day, 15, 0)
            bars = {
                "ZERO.SZ": make_bar("ZERO.SZ", dt, 0.0),  # 全 0 价格
                "GOOD.SZ": make_bar("GOOD.SZ", dt, 10.0 + day),
            }
            strategy.on_bar(bars, make_context(dt))

        scores = strategy._calculate_momentum_scores()
        # ZERO.SZ old_price==0 → 应被排除
        assert "ZERO.SZ" not in scores
        assert "GOOD.SZ" in scores

    def test_price_history_pruning(self, momentum_strategy: MomentumStrategy):
        """价格历史裁剪"""
        momentum_strategy.on_start()

        # 添加大量历史
        base_date = date(2025, 1, 1)
        for day in range(50):
            dt = datetime.combine(base_date + timedelta(days=day), datetime.min.time().replace(hour=15))
            bars = {"000001.SZ": make_bar("000001.SZ", dt, 10.0)}
            momentum_strategy.on_bar(bars, make_context(dt))

        # 历史不应无限增长
        history = momentum_strategy._price_history.get("000001.SZ", [])
        assert len(history) <= momentum_strategy.lookback_days + 10
