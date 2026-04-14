"""双均线突破策略测试"""

from dataclasses import dataclass
from datetime import datetime, date, timedelta

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext, IntentType
from aipha.strategy.dual_ma import DualMAStrategy


@pytest.fixture
def strategy() -> DualMAStrategy:
    """创建测试策略"""
    return DualMAStrategy(
        name="TestDualMA",
        short_window=3,  # 测试用较小的窗口
        long_window=5,
        position_pct=0.20,
        max_positions=2,
        target_symbols=["000001.SZ", "000002.SZ"],
    )


def make_bar(symbol: str, close: float, dt: datetime) -> Bar:
    """创建测试 Bar"""
    return Bar(
        symbol=symbol,
        start_ts=dt,
        end_ts=dt,
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=1_000_000.0,
    )


class TestDualMAStrategy:
    """双均线策略测试类"""

    def test_initialization(self, strategy: DualMAStrategy):
        """测试策略初始化"""
        assert strategy.name == "TestDualMA"
        assert strategy.short_window == 3
        assert strategy.long_window == 5
        assert strategy.max_positions == 2

    def test_on_start_resets_state(self, strategy: DualMAStrategy):
        """测试 on_start 重置状态"""
        strategy._price_history = {"000001.SZ": [(date(2024, 1, 1), 10.0)]}
        strategy._prev_ma_state = {"000001.SZ": "above"}
        strategy._bar_count = 100

        strategy.on_start()

        assert strategy._price_history == {}
        assert strategy._prev_ma_state == {}
        assert strategy._bar_count == 0

    def test_accumulates_price_history(self, strategy: DualMAStrategy):
        """测试价格历史累积"""
        strategy.on_start()

        for i in range(3):
            dt = datetime(2024, 1, i + 1)
            ctx = StrategyContext(
                current_time=dt,
                positions={},
                cash=100_000.0,
                nav=100_000.0,
            )
            bars = {"000001.SZ": make_bar("000001.SZ", 10.0 + i, dt)}
            strategy.on_bar(bars, ctx)

        assert len(strategy._price_history["000001.SZ"]) == 3

    def test_no_signal_before_long_window(self, strategy: DualMAStrategy):
        """测试长窗口期内不产生信号"""
        strategy.on_start()

        for i in range(strategy.long_window - 1):
            dt = datetime(2024, 1, i + 1)
            ctx = StrategyContext(
                current_time=dt,
                positions={},
                cash=100_000.0,
                nav=100_000.0,
            )
            bars = {"000001.SZ": make_bar("000001.SZ", 10.0, dt)}
            intents = strategy.on_bar(bars, ctx)
            assert intents == []

    def test_golden_cross_buy_signal(self, strategy: DualMAStrategy):
        """测试金叉买入信号"""
        strategy.on_start()

        # 构造下降趋势（短均线在下）
        prices = [12.0, 11.0, 10.0, 9.0, 8.0]
        for i, price in enumerate(prices):
            dt = datetime(2024, 1, i + 1)
            ctx = StrategyContext(
                current_time=dt,
                positions={},
                cash=100_000.0,
                nav=100_000.0,
            )
            bars = {"000001.SZ": make_bar("000001.SZ", price, dt)}
            strategy.on_bar(bars, ctx)

        # 此时 short MA = (10+9+8)/3 = 9.0
        #      long MA = (12+11+10+9+8)/5 = 10.0
        # 短 < 长，状态为 "below"

        # 价格反弹，触发金叉
        dt = datetime(2024, 1, 6)
        ctx = StrategyContext(
            current_time=dt,
            positions={},
            cash=100_000.0,
            nav=100_000.0,
        )
        # 新价格 15.0
        # short MA = (9+8+15)/3 = 10.67
        # long MA = (11+10+9+8+15)/5 = 10.6
        # 短 > 长，状态变为 "above" -> 金叉
        bars = {"000001.SZ": make_bar("000001.SZ", 15.0, dt)}
        intents = strategy.on_bar(bars, ctx)

        buy_intents = [i for i in intents if i.side == Side.BUY]
        assert len(buy_intents) >= 1
        assert buy_intents[0].symbol == "000001.SZ"

    def test_death_cross_sell_signal(self, strategy: DualMAStrategy):
        """测试死叉卖出信号"""
        strategy.on_start()

        # 构造上升趋势（短均线在上）
        prices = [8.0, 9.0, 10.0, 11.0, 12.0]
        for i, price in enumerate(prices):
            dt = datetime(2024, 1, i + 1)
            ctx = StrategyContext(
                current_time=dt,
                positions={},
                cash=100_000.0,
                nav=100_000.0,
            )
            bars = {"000001.SZ": make_bar("000001.SZ", price, dt)}
            strategy.on_bar(bars, ctx)

        # 此时 short MA = (10+11+12)/3 = 11.0
        #      long MA = (8+9+10+11+12)/5 = 10.0
        # 短 > 长，状态为 "above"

        # 价格下跌，触发死叉
        dt = datetime(2024, 1, 6)
        ctx = StrategyContext(
            current_time=dt,
            positions={"000001.SZ": 1000},  # 持有仓位
            cash=90_000.0,
            nav=100_000.0,
        )
        # 新价格 5.0
        # short MA = (11+12+5)/3 = 9.33
        # long MA = (9+10+11+12+5)/5 = 9.4
        # 短 < 长，状态变为 "below" -> 死叉
        bars = {"000001.SZ": make_bar("000001.SZ", 5.0, dt)}
        intents = strategy.on_bar(bars, ctx)

        sell_intents = [i for i in intents if i.side == Side.SELL]
        assert len(sell_intents) >= 1
        assert sell_intents[0].qty == 1000

    def test_calculate_ma(self, strategy: DualMAStrategy):
        """测试均线计算"""
        strategy.on_start()
        strategy._price_history["000001.SZ"] = [
            (date(2024, 1, i + 1), float(10 + i))
            for i in range(5)
        ]  # [10, 11, 12, 13, 14]

        # 3日均线 = (12+13+14)/3 = 13.0
        ma3 = strategy._calculate_ma("000001.SZ", 3)
        assert ma3 == pytest.approx(13.0)

        # 5日均线 = (10+11+12+13+14)/5 = 12.0
        ma5 = strategy._calculate_ma("000001.SZ", 5)
        assert ma5 == pytest.approx(12.0)

    def test_calculate_ma_insufficient_data(self, strategy: DualMAStrategy):
        """测试数据不足返回 None"""
        strategy.on_start()
        strategy._price_history["000001.SZ"] = [
            (date(2024, 1, 1), 10.0),
            (date(2024, 1, 2), 11.0),
        ]

        # 只有2条数据，无法计算3日均线
        ma3 = strategy._calculate_ma("000001.SZ", 3)
        assert ma3 is None

    def test_max_positions_limit(self, strategy: DualMAStrategy):
        """测试最大持仓限制"""
        strategy.on_start()
        strategy.max_positions = 1

        # 为两只股票设置金叉条件
        for symbol in ["000001.SZ", "000002.SZ"]:
            strategy._price_history[symbol] = [
                (date(2024, 1, i + 1), 10.0 - i * 0.5)
                for i in range(5)
            ]
            strategy._prev_ma_state[symbol] = "below"
        strategy._bar_count = 5

        dt = datetime(2024, 1, 6)
        ctx = StrategyContext(
            current_time=dt,
            positions={},
            cash=100_000.0,
            nav=100_000.0,
        )
        # 两只股票都反弹
        bars = {
            "000001.SZ": make_bar("000001.SZ", 15.0, dt),
            "000002.SZ": make_bar("000002.SZ", 15.0, dt),
        }
        intents = strategy.on_bar(bars, ctx)

        # 最多只能买1只
        buy_intents = [i for i in intents if i.side == Side.BUY]
        assert len(buy_intents) <= 1


    def test_all_identical_prices_no_crossover(self, strategy: DualMAStrategy):
        """所有价格完全相同时不应产生任何交叉信号"""
        strategy.on_start()

        for i in range(strategy.long_window + 2):
            dt = datetime(2024, 1, i + 1)
            ctx = StrategyContext(
                current_time=dt,
                positions={},
                cash=100_000.0,
                nav=100_000.0,
            )
            bars = {"000001.SZ": make_bar("000001.SZ", 10.0, dt)}
            intents = strategy.on_bar(bars, ctx)
            # 短均线 == 长均线 → 永远不会交叉
            assert intents == []


class TestDualMARegistry:
    """测试策略注册"""

    def test_dual_ma_registered(self):
        """测试双均线策略已注册"""
        from aipha.strategy.registry import list_strategies

        strategies = list_strategies()
        assert "demo_dual_ma" in strategies
        assert "moving_average" in strategies

    def test_create_from_registry(self):
        """测试从注册表创建策略"""
        from aipha.strategy.registry import create_strategy

        strategy = create_strategy(
            "demo_dual_ma",
            short_window=10,
            long_window=30,
        )
        assert isinstance(strategy, DualMAStrategy)
        assert strategy.short_window == 10
        assert strategy.long_window == 30
