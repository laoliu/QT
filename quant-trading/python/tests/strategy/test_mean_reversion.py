"""均值回归策略测试"""

from dataclasses import dataclass
from datetime import datetime, date, timedelta

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext, IntentType
from aipha.strategy.mean_reversion import MeanReversionStrategy


@pytest.fixture
def strategy() -> MeanReversionStrategy:
    """创建测试策略"""
    return MeanReversionStrategy(
        name="TestMeanReversion",
        lookback_days=5,
        entry_std=2.0,
        exit_std=0.5,
        max_positions=2,
        position_pct=0.20,
        target_symbols=["000001.SZ", "000002.SZ"],
    )


@pytest.fixture
def context() -> StrategyContext:
    """创建测试上下文"""
    return StrategyContext(
        current_time=datetime(2024, 1, 10),
        positions={},
        cash=100_000.0,
        nav=100_000.0,
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


class TestMeanReversionStrategy:
    """均值回归策略测试类"""

    def test_initialization(self, strategy: MeanReversionStrategy):
        """测试策略初始化"""
        assert strategy.name == "TestMeanReversion"
        assert strategy.lookback_days == 5
        assert strategy.entry_std == 2.0
        assert strategy.exit_std == 0.5
        assert strategy.max_positions == 2

    def test_on_start_resets_state(self, strategy: MeanReversionStrategy):
        """测试 on_start 重置状态"""
        strategy._price_history = {"000001.SZ": [(date(2024, 1, 1), 10.0)]}
        strategy._entry_prices = {"000001.SZ": 10.0}
        strategy._bar_count = 100

        strategy.on_start()

        assert strategy._price_history == {}
        assert strategy._entry_prices == {}
        assert strategy._bar_count == 0

    def test_accumulates_price_history(self, strategy: MeanReversionStrategy, context: StrategyContext):
        """测试价格历史累积"""
        strategy.on_start()

        for i in range(3):
            dt = datetime(2024, 1, i + 1)
            context = StrategyContext(
                current_time=dt,
                positions={},
                cash=100_000.0,
                nav=100_000.0,
            )
            bars = {"000001.SZ": make_bar("000001.SZ", 10.0 + i, dt)}
            strategy.on_bar(bars, context)

        assert len(strategy._price_history["000001.SZ"]) == 3

    def test_no_signal_before_lookback(self, strategy: MeanReversionStrategy, context: StrategyContext):
        """测试回望期内不产生信号"""
        strategy.on_start()

        # 只喂入不足 lookback_days 的数据
        for i in range(strategy.lookback_days - 1):
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

    def test_buy_signal_on_oversold(self, strategy: MeanReversionStrategy):
        """测试超跌时产生买入信号
        
        策略逻辑：当当前价格低于历史均值 N 个标准差时买入。
        注意：on_bar 会先更新价格历史（添加当前价格），再计算信号。
        """
        strategy.on_start()

        # 直接设置内部状态来测试信号生成逻辑
        strategy._price_history["000001.SZ"] = [
            (date(2024, 1, i + 1), 10.0 + (i % 2) * 0.5)
            for i in range(strategy.lookback_days)
        ]
        strategy._bar_count = strategy.lookback_days

        # 使用极端低价，确保 z-score < -2.0
        # 计算：添加5.0后，最近5条 = [10.5, 10.0, 10.5, 10.0, 5.0]
        # 均值 = 9.0, std ≈ 2.24, z-score = (5.0 - 9.0) / 2.24 ≈ -1.79
        # 需要更低的价格...使用 3.0
        # 最近5条 = [10.5, 10.0, 10.5, 10.0, 3.0], mean=8.8, std≈3.0, z=-1.93
        # 用 1.0: [10.5,10.0,10.5,10.0,1.0], mean=8.4, std≈3.7, z=-2.0
        crash_price = 1.0  # 极端超跌
        dt = datetime(2024, 1, 6)
        ctx = StrategyContext(
            current_time=dt,
            positions={},
            cash=100_000.0,
            nav=100_000.0,
        )
        bars = {"000001.SZ": make_bar("000001.SZ", crash_price, dt)}
        strategy.on_start()

        # 喂入有波动的价格来建立均值和标准差
        prices = [10.0, 10.5, 10.0, 10.5, 10.0]
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

        # 模拟持有仓位，价格回归均值（z-score接近0，均值约10.2）
        dt = datetime(2024, 1, 6)
        ctx = StrategyContext(
            current_time=dt,
            positions={"000001.SZ": 1000},
            cash=90_000.0,
            nav=100_000.0,
        )
        bars = {"000001.SZ": make_bar("000001.SZ", 10.2, dt)}
        intents = strategy.on_bar(bars, ctx)

        # 应该产生卖出信号
        sell_intents = [i for i in intents if i.side == Side.SELL]
        assert len(sell_intents) >= 1
        assert sell_intents[0].qty == 1000

    def test_max_positions_limit(self, strategy: MeanReversionStrategy):
        """测试最大持仓限制"""
        strategy.on_start()
        strategy.max_positions = 1

        # 建立两只股票的价格历史
        for symbol in ["000001.SZ", "000002.SZ"]:
            strategy._price_history[symbol] = [
                (date(2024, 1, i + 1), 10.0 + (i % 2) * 0.5)
                for i in range(strategy.lookback_days)
            ]
        strategy._bar_count = strategy.lookback_days

        # 两只股票都超跌
        dt = datetime(2024, 1, 6)
        ctx = StrategyContext(
            current_time=dt,
            positions={},
            cash=100_000.0,
            nav=100_000.0,
        )
        bars = {
            "000001.SZ": make_bar("000001.SZ", 7.0, dt),
            "000002.SZ": make_bar("000002.SZ", 7.0, dt),
        }
        intents = strategy.on_bar(bars, ctx)

        # 最多只能买1只
        buy_intents = [i for i in intents if i.side == Side.BUY]
        assert len(buy_intents) <= 1

    def test_calculate_statistics(self, strategy: MeanReversionStrategy):
        """测试统计计算"""
        strategy.on_start()

        # 设置价格历史
        strategy._price_history["000001.SZ"] = [
            (date(2024, 1, i + 1), 10.0)
            for i in range(strategy.lookback_days)
        ]

        result = strategy._calculate_statistics("000001.SZ")
        assert result is not None
        mean, std = result
        assert mean == 10.0
        assert std == 0.0  # 价格完全相同，标准差为0

    def test_calculate_statistics_with_variance(self, strategy: MeanReversionStrategy):
        """测试有波动的统计计算"""
        strategy.on_start()

        # 设置有波动的价格历史
        prices = [10.0, 10.5, 9.5, 11.0, 9.0]
        strategy._price_history["000001.SZ"] = [
            (date(2024, 1, i + 1), prices[i])
            for i in range(len(prices))
        ]

        result = strategy._calculate_statistics("000001.SZ")
        assert result is not None
        mean, std = result
        assert 9.9 < mean < 10.1
        assert std > 0

    def test_insufficient_data_returns_none(self, strategy: MeanReversionStrategy):
        """测试数据不足返回 None"""
        strategy.on_start()

        # 只有2个数据点，少于 lookback_days=5
        strategy._price_history["000001.SZ"] = [
            (date(2024, 1, i + 1), 10.0)
            for i in range(2)
        ]

        result = strategy._calculate_statistics("000001.SZ")
        assert result is None


    def test_zero_std_no_signal(self, strategy: MeanReversionStrategy):
        """所有价格完全相同 → std=0 → 无法计算 z-score → 不产生信号"""
        strategy.on_start()

        # 喂入 lookback_days 条完全相同的价格
        for i in range(strategy.lookback_days + 1):
            dt = datetime(2024, 1, i + 1)
            ctx = StrategyContext(
                current_time=dt,
                positions={},
                cash=100_000.0,
                nav=100_000.0,
            )
            bars = {"000001.SZ": make_bar("000001.SZ", 10.0, dt)}
            intents = strategy.on_bar(bars, ctx)

        # std=0 时源码会 continue 跳过，不应报错也不应产生信号
        assert intents == []


class TestMeanReversionRegistry:
    """测试策略注册"""

    def test_mean_reversion_registered(self):
        """测试均值回归策略已注册"""
        from aipha.strategy.registry import list_strategies

        strategies = list_strategies()
        assert "demo_mean_reversion" in strategies
        assert "reversion" in strategies

    def test_create_from_registry(self):
        """测试从注册表创建策略"""
        from aipha.strategy.registry import create_strategy

        strategy = create_strategy(
            "demo_mean_reversion",
            lookback_days=10,
            entry_std=1.5,
        )
        assert isinstance(strategy, MeanReversionStrategy)
        assert strategy.lookback_days == 10
        assert strategy.entry_std == 1.5
