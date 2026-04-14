"""
因子驱动策略测试
"""

from datetime import date, datetime
import math

import pytest

from aipha.domain.models import Bar, Side
from aipha.factors.base import Factor, FactorData, FactorResult
from aipha.strategy.factor_strategy import (
    FactorBasedStrategy,
    FactorStrategyConfig,
)
from aipha.strategy.base import StrategyContext, OrderIntent, IntentType


# ============================================================================
# Mock Factor for Testing
# ============================================================================


class MockFactor(Factor):
    """测试用因子"""

    def __init__(self, name: str = "MOCK", values: dict[str, float] = None):
        self._name = name
        self._values = values or {}

    @property
    def name(self) -> str:
        return self._name

    def compute(self, data: FactorData) -> FactorResult:
        value = self._values.get(data.symbol, float("nan"))
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
        )


def create_bars(prices: dict[str, float], trade_date: date = None) -> dict[str, Bar]:
    """创建测试用 Bar 数据"""
    trade_date = trade_date or date(2025, 1, 6)
    bars = {}
    for symbol, price in prices.items():
        ts = datetime.combine(trade_date, datetime.min.time())
        bars[symbol] = Bar(
            symbol=symbol,
            start_ts=ts,
            end_ts=ts,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=10000.0,
        )
    return bars


def create_context(
    trade_date: date = None,
    positions: dict[str, int] = None,
    cash: float = 1000000.0,
    nav: float = 1000000.0,
) -> StrategyContext:
    """创建测试用策略上下文"""
    trade_date = trade_date or date(2025, 1, 6)
    return StrategyContext(
        current_time=datetime.combine(trade_date, datetime.min.time()),
        positions=positions or {},
        cash=cash,
        nav=nav,
    )


# ============================================================================
# FactorStrategyConfig Tests
# ============================================================================


class TestFactorStrategyConfig:
    """因子策略配置测试"""

    def test_default_config(self):
        """默认配置"""
        config = FactorStrategyConfig()
        assert config.top_k == 10
        assert config.top_pct is None
        assert config.rebalance_days == 20

    def test_top_k_config(self):
        """top_k 配置"""
        config = FactorStrategyConfig(top_k=20)
        assert config.top_k == 20

    def test_top_pct_config(self):
        """top_pct 配置"""
        config = FactorStrategyConfig(top_k=None, top_pct=0.1)
        assert config.top_pct == 0.1

    def test_both_top_k_and_top_pct_raises(self):
        """同时指定 top_k 和 top_pct 报错"""
        with pytest.raises(ValueError):
            FactorStrategyConfig(top_k=10, top_pct=0.1)

    def test_neither_top_k_nor_top_pct_raises(self):
        """都不指定报错"""
        with pytest.raises(ValueError):
            FactorStrategyConfig(top_k=None, top_pct=None)


# ============================================================================
# FactorBasedStrategy Tests
# ============================================================================


class TestFactorBasedStrategy:
    """因子驱动策略测试"""

    def test_strategy_creation(self):
        """创建策略"""
        factor = MockFactor("F1")
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=5),
            name="TEST_STRATEGY",
        )
        assert strategy.name == "TEST_STRATEGY"

    def test_single_factor_selection(self):
        """单因子选股"""
        # 因子值: A=100, B=80, C=60, D=40, E=20
        # 默认 ascending=False，因子值越大越好
        factor = MockFactor("F1", {"A": 100, "B": 80, "C": 60, "D": 40, "E": 20})
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=3, rebalance_days=1),
        )

        bars = create_bars({"A": 10, "B": 10, "C": 10, "D": 10, "E": 10})
        context = create_context(nav=100000.0)

        intents = strategy.on_bar(bars, context)

        # 应该选择 A, B, C（因子值最大的 3 个）
        buy_symbols = {i.symbol for i in intents if i.side == Side.BUY}
        assert buy_symbols == {"A", "B", "C"}

    def test_ascending_selection(self):
        """升序选股（因子值越小越好）"""
        factor = MockFactor("F1", {"A": 100, "B": 80, "C": 60, "D": 40, "E": 20})
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=3, ascending=True, rebalance_days=1),
        )

        bars = create_bars({"A": 10, "B": 10, "C": 10, "D": 10, "E": 10})
        context = create_context(nav=100000.0)

        intents = strategy.on_bar(bars, context)

        # 应该选择 E, D, C（因子值最小的 3 个）
        buy_symbols = {i.symbol for i in intents if i.side == Side.BUY}
        assert buy_symbols == {"C", "D", "E"}

    def test_equal_weight_intents(self):
        """等权重生成的订单意图"""
        factor = MockFactor("F1", {"A": 100, "B": 80, "C": 60})
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=3, equal_weight=True, rebalance_days=1),
        )

        # NAV = 100000，每只股票权重 1/3 约 33333
        # 价格 = 10，每只股票买入 33333/10 = 3333，取整到手 = 3300
        bars = create_bars({"A": 10, "B": 10, "C": 10})
        context = create_context(nav=100000.0)

        intents = strategy.on_bar(bars, context)

        assert len(intents) == 3
        for intent in intents:
            assert intent.side == Side.BUY
            assert intent.qty == 3300  # 每只股票买入 3300 股

    def test_rebalance_interval(self):
        """调仓间隔控制"""
        factor = MockFactor("F1", {"A": 100, "B": 80})
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=2, rebalance_days=5),
        )

        bars = create_bars({"A": 10, "B": 10})
        context = create_context(nav=100000.0)

        # 第一次调仓
        intents1 = strategy.on_bar(bars, context)
        assert len(intents1) > 0

        # 第二次不应该调仓（间隔不够）
        intents2 = strategy.on_bar(bars, context)
        assert len(intents2) == 0

        # 再调用 3 次，总共 5 次（1 + 1 + 3 = 5），第 5 次达到间隔
        strategy.on_bar(bars, context)  # 3rd, _days=2
        strategy.on_bar(bars, context)  # 4th, _days=3
        strategy.on_bar(bars, context)  # 5th, _days=4

        # 第 6 次应该调仓（_days=5 >= rebalance_days=5）
        intents3 = strategy.on_bar(bars, context)
        assert len(intents3) > 0

    def test_sell_positions_not_in_target(self):
        """卖出不在目标中的持仓"""
        factor = MockFactor("F1", {"A": 100, "B": 80, "C": 60})
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=2, rebalance_days=1),
        )

        bars = create_bars({"A": 10, "B": 10, "C": 10})
        # 当前持有 C，但 C 不在 top_k=2 中
        context = create_context(
            nav=100000.0,
            positions={"C": 1000},
        )

        intents = strategy.on_bar(bars, context)

        # 应该卖出 C
        sell_intents = [i for i in intents if i.side == Side.SELL]
        assert len(sell_intents) == 1
        assert sell_intents[0].symbol == "C"
        assert sell_intents[0].qty == 1000

    def test_multi_factor_strategy(self):
        """多因子策略"""
        factor1 = MockFactor("F1", {"A": 100, "B": 80, "C": 60, "D": 40})
        factor2 = MockFactor("F2", {"A": 20, "B": 40, "C": 60, "D": 80})
        
        strategy = FactorBasedStrategy(
            factors=[factor1, factor2],
            weights={"F1": 0.5, "F2": 0.5},
            config=FactorStrategyConfig(top_k=2, rebalance_days=1),
        )

        bars = create_bars({"A": 10, "B": 10, "C": 10, "D": 10})
        context = create_context(nav=100000.0)

        intents = strategy.on_bar(bars, context)

        # 多因子组合后，B 和 C 应该排名靠前
        buy_symbols = {i.symbol for i in intents if i.side == Side.BUY}
        assert len(buy_symbols) == 2

    def test_nan_factor_values(self):
        """处理 NaN 因子值"""
        # A 有有效值，B 和 C 是 NaN
        factor = MockFactor("F1", {"A": 100})  # B, C 将返回 NaN
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=3, rebalance_days=1),
        )

        bars = create_bars({"A": 10, "B": 10, "C": 10})
        context = create_context(nav=100000.0)

        intents = strategy.on_bar(bars, context)

        # 只有 A 有有效因子值
        buy_symbols = {i.symbol for i in intents if i.side == Side.BUY}
        assert buy_symbols == {"A"}

    def test_empty_universe(self):
        """空股票池"""
        factor = MockFactor("F1", {})
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=3, rebalance_days=1),
        )

        bars: dict[str, Bar] = {}
        context = create_context(nav=100000.0)

        intents = strategy.on_bar(bars, context)

        assert len(intents) == 0

    def test_reset(self):
        """重置策略状态"""
        factor = MockFactor("F1", {"A": 100})
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=1, rebalance_days=5),
        )

        bars = create_bars({"A": 10})
        context = create_context(nav=100000.0)

        # 首次调仓
        strategy.on_bar(bars, context)
        
        # 未到调仓时间
        intents = strategy.on_bar(bars, context)
        assert len(intents) == 0

        # 重置后应该可以立即调仓
        strategy.reset()
        intents = strategy.on_bar(bars, context)
        assert len(intents) > 0

    def test_top_pct_selection(self):
        """按百分比选股"""
        factor = MockFactor("F1", {
            "A": 100, "B": 90, "C": 80, "D": 70, "E": 60,
            "F": 50, "G": 40, "H": 30, "I": 20, "J": 10,
        })
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=None, top_pct=0.3, rebalance_days=1),
        )

        prices = {s: 10 for s in "ABCDEFGHIJ"}
        bars = create_bars(prices)
        context = create_context(nav=100000.0)

        intents = strategy.on_bar(bars, context)

        # 30% of 10 = 3 stocks
        buy_symbols = {i.symbol for i in intents if i.side == Side.BUY}
        assert len(buy_symbols) == 3
        assert buy_symbols == {"A", "B", "C"}

    def test_adjust_existing_positions(self):
        """调整已有持仓"""
        factor = MockFactor("F1", {"A": 100, "B": 80})
        
        strategy = FactorBasedStrategy(
            factors=[factor],
            config=FactorStrategyConfig(top_k=2, equal_weight=True, rebalance_days=1),
        )

        bars = create_bars({"A": 10, "B": 10})
        # 当前 A 持仓过多，B 持仓过少
        context = create_context(
            nav=100000.0,
            positions={"A": 10000, "B": 1000},  # A 太多，B 太少
        )

        intents = strategy.on_bar(bars, context)

        # 应该卖出 A，买入 B
        sell_a = [i for i in intents if i.symbol == "A" and i.side == Side.SELL]
        buy_b = [i for i in intents if i.symbol == "B" and i.side == Side.BUY]
        
        # 目标权重 50%，NAV=100000，每只 50000 元
        # A: 50000/10 = 5000 股；当前 10000，需卖 5000
        # B: 50000/10 = 5000 股；当前 1000，需买 4000
        assert len(sell_a) == 1
        assert sell_a[0].qty == 5000
        assert len(buy_b) == 1
        assert buy_b[0].qty == 4000
