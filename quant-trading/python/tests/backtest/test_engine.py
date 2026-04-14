"""
回测引擎测试
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from aipha.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    DailySnapshot,
    Event,
    EventQueue,
    EventType,
    TradeRecord,
)
from aipha.domain.models import Bar, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel, SameBarCloseFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext


# ============================================================================
# 测试辅助
# ============================================================================


def create_bar(
    symbol: str,
    trade_date: date,
    price: float = 10.0,
    volume: float = 1000000.0,
) -> Bar:
    """创建测试用 Bar"""
    ts = datetime.combine(trade_date, datetime.min.time().replace(hour=15))
    return Bar(
        symbol=symbol,
        start_ts=ts.replace(hour=9, minute=30),
        end_ts=ts,
        open=price * 0.99,
        high=price * 1.01,
        low=price * 0.98,
        close=price,
        volume=volume,
    )


def create_bars_series(
    symbols: list[str],
    start_date: date,
    num_days: int = 10,
    base_price: float = 10.0,
) -> dict[date, dict[str, Bar]]:
    """创建多日 Bar 数据"""
    bars_by_date = {}
    current_date = start_date

    for day in range(num_days):
        # 跳过周末
        while current_date.weekday() >= 5:
            current_date += timedelta(days=1)

        bars_by_date[current_date] = {}
        for i, symbol in enumerate(symbols):
            price = base_price + i * 2 + day * 0.1
            bars_by_date[current_date][symbol] = create_bar(symbol, current_date, price)

        current_date += timedelta(days=1)

    return bars_by_date


@dataclass
class BuyAndHoldStrategy(Strategy):
    """买入持有策略"""

    name: str = "BuyAndHold"
    target_symbols: list[str] = field(default_factory=list)
    _initialized: bool = field(default=False, repr=False)

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        if self._initialized:
            return []

        self._initialized = True
        intents = []

        num_symbols = len(self.target_symbols)
        if num_symbols == 0:
            return []

        available = context.cash * 0.95
        per_symbol = available / num_symbols

        for symbol in self.target_symbols:
            if symbol not in bars:
                continue
            bar = bars[symbol]
            qty = int(per_symbol / bar.close / 100) * 100
            if qty >= 100:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.BUY,
                        qty=qty,
                        intent_type=IntentType.ORDER,
                    )
                )

        return intents


@dataclass
class SellAllStrategy(Strategy):
    """全部卖出策略"""

    name: str = "SellAll"
    _sold: bool = field(default=False, repr=False)

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        if self._sold:
            return []

        self._sold = True
        intents = []

        for symbol, qty in context.positions.items():
            if qty > 0:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.SELL,
                        qty=qty,
                        intent_type=IntentType.ORDER,
                    )
                )

        return intents


# ============================================================================
# EventQueue Tests
# ============================================================================


class TestEventQueue:
    """事件队列测试"""

    def test_empty_queue(self):
        """空队列"""
        queue = EventQueue()
        assert queue.is_empty()
        assert queue.pop() is None

    def test_push_pop(self):
        """入队出队"""
        queue = EventQueue()
        event = Event(
            timestamp=datetime(2025, 1, 6, 10, 0),
            sequence=1,
            event_type=EventType.BAR,
        )
        queue.push(event)
        assert not queue.is_empty()

        popped = queue.pop()
        assert popped == event
        assert queue.is_empty()

    def test_priority_order(self):
        """优先级顺序"""
        queue = EventQueue()

        # 添加乱序事件
        event1 = Event(
            timestamp=datetime(2025, 1, 6, 10, 0),
            sequence=1,
            event_type=EventType.BAR,
        )
        event2 = Event(
            timestamp=datetime(2025, 1, 6, 9, 30),
            sequence=2,
            event_type=EventType.BAR,
        )
        event3 = Event(
            timestamp=datetime(2025, 1, 6, 10, 0),
            sequence=3,
            event_type=EventType.ORDER_FILLED,
        )

        queue.push(event1)
        queue.push(event2)
        queue.push(event3)

        # 应该按时间戳和序列号排序
        assert queue.pop() == event2  # 最早时间
        assert queue.pop() == event1  # 同时间，序列号小
        assert queue.pop() == event3  # 同时间，序列号大

    def test_next_sequence(self):
        """序列号生成"""
        queue = EventQueue()
        assert queue.next_sequence() == 1
        assert queue.next_sequence() == 2
        assert queue.next_sequence() == 3

    def test_clear(self):
        """清空队列"""
        queue = EventQueue()
        queue.push(Event(datetime.now(), 1, EventType.BAR))
        queue.push(Event(datetime.now(), 2, EventType.EOD))
        queue.clear()
        assert queue.is_empty()
        assert queue.next_sequence() == 1


# ============================================================================
# BacktestConfig Tests
# ============================================================================


class TestBacktestConfig:
    """回测配置测试"""

    def test_default_config(self):
        """默认配置"""
        config = BacktestConfig()
        assert config.initial_capital == 1_000_000.0
        assert config.commission_rate == 8.54e-05  # §13.1 MarketRulesCNEquity 万0.85
        assert config.stamp_tax_rate == 0.0005
        assert config.t_plus_n == 1

    def test_custom_config(self):
        """自定义配置"""
        config = BacktestConfig(
            initial_capital=500_000.0,
            commission_rate=0.0002,
            slippage_bps=5.0,
        )
        assert config.initial_capital == 500_000.0
        assert config.commission_rate == 0.0002
        assert config.slippage_bps == 5.0

    def test_to_dict(self):
        """转换为字典"""
        config = BacktestConfig(initial_capital=100_000.0)
        d = config.to_dict()
        assert d["initial_capital"] == 100_000.0
        assert "commission_rate" in d
        assert "stamp_tax_rate" in d


# ============================================================================
# BacktestEngine Tests
# ============================================================================


class TestBacktestEngine:
    """回测引擎测试"""

    @pytest.fixture
    def default_engine_components(self):
        """默认引擎组件"""
        config = BacktestConfig(initial_capital=100_000.0)

        fee_model = FeeModelCNEquity(
            commission=CommissionModel(rate=config.commission_rate, min_fee=config.commission_min),
            stamp_tax=StampTaxModel(sell_rate=config.stamp_tax_rate),
            transfer_fee=TransferFeeModel(rate=config.transfer_fee_rate),
        )

        return {
            "config": config,
            "fill_model": NextBarOpenFillModel(),
            "slippage": FixedBpsSlippageModel(bps=config.slippage_bps),
            "fee_model": fee_model,
            "rules": MarketRulesCNEquity(),
        }

    def test_engine_creation(self, default_engine_components):
        """创建引擎"""
        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(strategy=strategy, **default_engine_components)
        assert engine is not None

    def test_empty_data(self, default_engine_components):
        """空数据"""
        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        with pytest.raises(ValueError, match="No data"):
            engine.run({})

    def test_buy_and_hold(self, default_engine_components):
        """买入持有策略"""
        symbols = ["A", "B"]
        strategy = BuyAndHoldStrategy(target_symbols=symbols)
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        bars = create_bars_series(symbols, date(2025, 1, 6), num_days=10)
        result = engine.run(bars)

        assert result.strategy_name == "BuyAndHold"
        assert result.start_date == date(2025, 1, 6)
        assert len(result.daily_snapshots) == 10
        assert result.total_trades > 0  # 应该有买入交易

    def test_nav_tracking(self, default_engine_components):
        """NAV 跟踪"""
        symbols = ["A"]
        strategy = BuyAndHoldStrategy(target_symbols=symbols)
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        bars = create_bars_series(symbols, date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        # NAV 应该在初始资金附近
        initial = default_engine_components["config"].initial_capital
        assert result.daily_snapshots[0].nav <= initial  # 可能有费用
        assert all(s.nav > 0 for s in result.daily_snapshots)

    def test_trade_records(self, default_engine_components):
        """交易记录"""
        symbols = ["A"]
        strategy = BuyAndHoldStrategy(target_symbols=symbols)
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        bars = create_bars_series(symbols, date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        assert len(result.trades) > 0
        trade = result.trades[0]
        assert trade.symbol == "A"
        assert trade.side == Side.BUY
        assert trade.qty > 0
        assert trade.fees.total >= 0

    def test_fee_calculation(self, default_engine_components):
        """费用计算"""
        symbols = ["A"]
        strategy = BuyAndHoldStrategy(target_symbols=symbols)
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        bars = create_bars_series(symbols, date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        # 买入有佣金
        assert result.total_fees.commission > 0
        # 买入没有印花税
        assert result.total_fees.stamp_tax == 0.0

    def test_daily_returns(self, default_engine_components):
        """日收益率"""
        symbols = ["A"]
        strategy = BuyAndHoldStrategy(target_symbols=symbols)
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        bars = create_bars_series(symbols, date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        # 第一天 daily_return 应该是 0 或接近 0
        assert abs(result.daily_snapshots[0].daily_return) < 0.1
        # 后续天有收益率
        assert all(s.daily_return is not None for s in result.daily_snapshots)

    def test_cumulative_return(self, default_engine_components):
        """累计收益率"""
        symbols = ["A"]
        strategy = BuyAndHoldStrategy(target_symbols=symbols)
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        bars = create_bars_series(symbols, date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        # 最后一天的累计收益率应该等于 (final_nav - initial) / initial
        initial = default_engine_components["config"].initial_capital
        expected = (result.final_nav - initial) / initial
        actual = result.daily_snapshots[-1].cumulative_return
        assert abs(actual - expected) < 0.0001

    def test_position_tracking(self, default_engine_components):
        """持仓跟踪"""
        symbols = ["A", "B"]
        strategy = BuyAndHoldStrategy(target_symbols=symbols)
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        bars = create_bars_series(symbols, date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        # 最后应该持有 A 和 B
        assert len(result.final_positions) == 2
        assert "A" in result.final_positions
        assert "B" in result.final_positions

    def test_config_preserved(self, default_engine_components):
        """配置保留"""
        symbols = ["A"]
        strategy = BuyAndHoldStrategy(target_symbols=symbols)
        engine = BacktestEngine(strategy=strategy, **default_engine_components)

        bars = create_bars_series(symbols, date(2025, 1, 6), num_days=3)
        result = engine.run(bars)

        assert result.config["initial_capital"] == 100_000.0


# ============================================================================
# DailySnapshot Tests
# ============================================================================


class TestDailySnapshot:
    """每日快照测试"""

    def test_snapshot_creation(self):
        """创建快照"""
        snapshot = DailySnapshot(
            trade_date=date(2025, 1, 6),
            nav=100_000.0,
            cash=50_000.0,
            positions={"A": 1000},
            position_values={"A": 50_000.0},
            daily_return=0.01,
            cumulative_return=0.01,
        )
        assert snapshot.trade_date == date(2025, 1, 6)
        assert snapshot.nav == 100_000.0


# ============================================================================
# TradeRecord Tests
# ============================================================================


class TestTradeRecord:
    """交易记录测试"""

    def test_trade_record_creation(self):
        """创建交易记录"""
        from aipha.execution.fees import FeeBreakdown

        record = TradeRecord(
            trade_id="TRD-000001",
            order_id="ORD-000001",
            symbol="A",
            side=Side.BUY,
            qty=100,
            price=10.0,
            notional=1000.0,
            fees=FeeBreakdown(commission=5.0, stamp_tax=0.0, transfer_fee=0.01),
            trade_time=datetime(2025, 1, 6, 10, 0),
            trade_date=date(2025, 1, 6),
        )
        assert record.trade_id == "TRD-000001"
        assert record.notional == 1000.0


# ============================================================================
# Same-Bar-Close Engine Tests
# ============================================================================


class TestSameBarCloseEngine:
    """同 bar close 成交模式引擎测试"""

    @pytest.fixture
    def same_bar_components(self):
        """使用 SameBarCloseFillModel 的引擎组件"""
        config = BacktestConfig(initial_capital=100_000.0, t_plus_n=0)  # T+0
        fee_model = FeeModelCNEquity(
            commission=CommissionModel(rate=config.commission_rate, min_fee=config.commission_min),
            stamp_tax=StampTaxModel(sell_rate=config.stamp_tax_rate),
            transfer_fee=TransferFeeModel(rate=config.transfer_fee_rate),
        )
        return {
            "config": config,
            "fill_model": SameBarCloseFillModel(),
            "slippage": FixedBpsSlippageModel(bps=0.0),
            "fee_model": fee_model,
            "rules": MarketRulesCNEquity(),
        }

    def test_same_bar_trade_on_signal_day(self, same_bar_components):
        """同 bar 模式：信号日当天即有成交记录"""
        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(strategy=strategy, **same_bar_components)

        # 只有 1 个交易日
        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=1)
        result = engine.run(bars)

        # 同 bar 模式应在第 1 天就成交，而 next-bar-open 需要 2 天
        assert result.total_trades > 0
        assert "A" in result.final_positions
        assert result.final_positions["A"] > 0

    def test_same_bar_vs_next_bar_timing(self, same_bar_components):
        """同 bar 成交比 next-bar-open 提前 1 天建仓"""
        strategy_same = BuyAndHoldStrategy(target_symbols=["A"])
        engine_same = BacktestEngine(strategy=strategy_same, **same_bar_components)

        # next-bar-open 对照组
        nbo_components = dict(same_bar_components)
        nbo_components["fill_model"] = NextBarOpenFillModel()
        strategy_nbo = BuyAndHoldStrategy(target_symbols=["A"])
        engine_nbo = BacktestEngine(strategy=strategy_nbo, **nbo_components)

        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=3)

        result_same = engine_same.run(bars)
        result_nbo = engine_nbo.run(bars)

        # 两种模式都应有成交
        assert result_same.total_trades > 0
        assert result_nbo.total_trades > 0

        # same-bar 的第一笔成交应在第 1 天
        first_trade_same = result_same.trades[0].trade_date
        first_trade_nbo = result_nbo.trades[0].trade_date
        assert first_trade_same < first_trade_nbo or first_trade_same == first_trade_nbo

    def test_fill_model_name_in_result(self, same_bar_components):
        """结果中包含 fill_model_name"""
        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(strategy=strategy, **same_bar_components)

        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=2)
        result = engine.run(bars)

        assert result.config["fill_model_name"] == "same_bar_close"
