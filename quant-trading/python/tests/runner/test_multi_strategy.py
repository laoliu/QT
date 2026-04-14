"""
多策略运行器测试

测试覆盖：
- 资金分配
- 多策略并行
- 风控止损
- 组合状态
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Side
from aipha.runner.multi_strategy import (
    MultiStrategyRunner,
    PortfolioConfig,
    StrategyAllocation,
)
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext


class BuyOnceStrategy(Strategy):
    """买入一次策略"""

    name = "buy_once"

    def __init__(self, strategy_name: str = "buy_once"):
        self.name = strategy_name
        self._bought = False

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        if not self._bought and bars:
            self._bought = True
            bar = list(bars.values())[0]
            return [
                OrderIntent(
                    symbol=bar.symbol,
                    side=Side.BUY,
                    qty=100,
                    limit_price=bar.close,
                )
            ]
        return []


class PassiveStrategy(Strategy):
    """被动策略：什么都不做"""

    name = "passive"

    def __init__(self, strategy_name: str = "passive"):
        self.name = strategy_name

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        return []


def make_bar(
    symbol: str = "000001.SZ",
    dt: datetime | None = None,
    price: float = 10.0,
) -> Bar:
    """创建测试 Bar"""
    if dt is None:
        dt = datetime(2024, 1, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=dt.replace(hour=9, minute=30),
        end_ts=dt,
        open=price,
        high=price * 1.02,
        low=price * 0.98,
        close=price,
        volume=100000.0,
    )


@pytest.fixture
def config() -> PortfolioConfig:
    """标准测试配置"""
    return PortfolioConfig(
        total_capital=Decimal("100000"),
        lot_size=100,
        t_plus_n=1,
    )


class TestMultiStrategyRunner:
    """多策略运行器测试"""

    def test_add_strategies(self, config: PortfolioConfig):
        """测试添加策略"""
        runner = MultiStrategyRunner(config)

        runner.add_strategy(PassiveStrategy("s1"), weight=0.5)
        runner.add_strategy(PassiveStrategy("s2"), weight=0.5)

        assert len(runner._allocations) == 2

    def test_invalid_weight_zero(self, config: PortfolioConfig):
        """测试无效权重 - 零"""
        runner = MultiStrategyRunner(config)

        with pytest.raises(ValueError, match="Weight"):
            runner.add_strategy(PassiveStrategy(), weight=0)

    def test_invalid_weight_over_one(self, config: PortfolioConfig):
        """测试无效权重 - 超过1"""
        runner = MultiStrategyRunner(config)

        with pytest.raises(ValueError, match="Weight"):
            runner.add_strategy(PassiveStrategy(), weight=1.5)

    def test_chain_add(self, config: PortfolioConfig):
        """测试链式添加"""
        runner = MultiStrategyRunner(config)

        result = (
            runner.add_strategy(PassiveStrategy("s1"), weight=0.5)
            .add_strategy(PassiveStrategy("s2"), weight=0.5)
        )

        assert result is runner
        assert len(runner._allocations) == 2

    def test_start_stop(self, config: PortfolioConfig):
        """测试启动和停止"""
        runner = MultiStrategyRunner(config)
        runner.add_strategy(PassiveStrategy("s1"), weight=0.5)
        runner.add_strategy(PassiveStrategy("s2"), weight=0.5)

        runner.start()
        assert runner._is_running is True
        assert len(runner._runners) == 2

        runner.stop()
        assert runner._is_running is False

    def test_no_strategies_error(self, config: PortfolioConfig):
        """测试无策略时启动错误"""
        runner = MultiStrategyRunner(config)

        with pytest.raises(ValueError, match="No strategies"):
            runner.start()


class TestStrategyAllocation:
    """策略分配测试"""

    def test_valid_allocation(self):
        """测试有效分配"""
        alloc = StrategyAllocation(
            strategy=PassiveStrategy(),
            weight=0.5,
            max_drawdown=0.1,
        )

        assert alloc.weight == 0.5
        assert alloc.max_drawdown == 0.1
        assert alloc.enabled is True

    def test_invalid_weight(self):
        """测试无效权重"""
        with pytest.raises(ValueError, match="Weight"):
            StrategyAllocation(
                strategy=PassiveStrategy(),
                weight=0,
                max_drawdown=0.1,
            )

    def test_invalid_max_drawdown(self):
        """测试无效最大回撤"""
        with pytest.raises(ValueError, match="max_drawdown"):
            StrategyAllocation(
                strategy=PassiveStrategy(),
                weight=0.5,
                max_drawdown=0,
            )


class TestPortfolioConfig:
    """组合配置测试"""

    def test_default_config(self):
        """测试默认配置"""
        config = PortfolioConfig()

        assert config.total_capital == Decimal("1000000")
        assert config.lot_size == 100
        assert config.portfolio_max_drawdown == 0.25

    def test_custom_config(self):
        """测试自定义配置"""
        config = PortfolioConfig(
            total_capital=Decimal("500000"),
            lot_size=100,
            commission_rate=Decimal("0.0002"),
        )

        assert config.total_capital == Decimal("500000")
        assert config.commission_rate == Decimal("0.0002")
