"""信号评估模块测试"""

from datetime import datetime, timedelta

import pytest

from aipha.signals.evaluator import (
    DecayStats,
    ReturnStats,
    Signal,
    SignalEvaluationReport,
    SignalEvaluator,
    SignalStats,
    SignalType,
    evaluate_signals,
)


class TestSignal:
    """Signal 数据类测试"""

    def test_signal_creation(self):
        """测试信号创建"""
        dt = datetime(2024, 1, 15, 10, 0)
        signal = Signal(
            symbol="000001.SZ",
            signal_type=SignalType.BUY,
            timestamp=dt,
            price=10.5,
            confidence=0.8,
        )

        assert signal.symbol == "000001.SZ"
        assert signal.signal_type == SignalType.BUY
        assert signal.timestamp == dt
        assert signal.price == 10.5
        assert signal.confidence == 0.8

    def test_signal_default_values(self):
        """测试信号默认值"""
        signal = Signal(
            symbol="000001.SZ",
            signal_type=SignalType.SELL,
            timestamp=datetime.now(),
            price=10.0,
        )

        assert signal.confidence == 1.0
        assert signal.metadata == {}


class TestSignalStats:
    """SignalStats 数据类测试"""

    def test_default_values(self):
        """测试默认值"""
        stats = SignalStats()
        assert stats.total_signals == 0
        assert stats.buy_signals == 0
        assert stats.sell_signals == 0


class TestSignalEvaluator:
    """SignalEvaluator 测试"""

    @pytest.fixture
    def sample_signals(self) -> list[Signal]:
        """创建测试信号"""
        base_date = datetime(2024, 1, 1)
        return [
            Signal("A", SignalType.BUY, base_date, 100.0, 0.9),
            Signal("A", SignalType.SELL, base_date + timedelta(days=5), 110.0, 0.8),
            Signal("B", SignalType.BUY, base_date + timedelta(days=2), 50.0, 0.7),
            Signal("B", SignalType.BUY, base_date + timedelta(days=10), 55.0, 0.6),
            Signal("C", SignalType.HOLD, base_date + timedelta(days=3), 200.0, 0.5),
        ]

    @pytest.fixture
    def sample_prices(self) -> dict[str, dict[datetime, float]]:
        """创建测试价格数据"""
        base_date = datetime(2024, 1, 1)
        return {
            "A": {
                base_date: 100.0,
                base_date + timedelta(days=1): 102.0,
                base_date + timedelta(days=2): 105.0,
                base_date + timedelta(days=3): 108.0,
                base_date + timedelta(days=5): 110.0,
                base_date + timedelta(days=6): 108.0,
                base_date + timedelta(days=10): 115.0,
            },
            "B": {
                base_date + timedelta(days=2): 50.0,
                base_date + timedelta(days=3): 52.0,
                base_date + timedelta(days=7): 48.0,
                base_date + timedelta(days=10): 55.0,
                base_date + timedelta(days=11): 54.0,
                base_date + timedelta(days=15): 60.0,
            },
        }

    def test_evaluate_empty_signals(self):
        """测试空信号列表"""
        evaluator = SignalEvaluator([], {})
        report = evaluator.evaluate()

        assert report.signal_stats.total_signals == 0
        assert report.return_stats.avg_return == 0.0

    def test_signal_stats_computation(self, sample_signals, sample_prices):
        """测试信号统计计算"""
        evaluator = SignalEvaluator(sample_signals, sample_prices)
        report = evaluator.evaluate()

        assert report.signal_stats.total_signals == 5
        assert report.signal_stats.buy_signals == 3
        assert report.signal_stats.sell_signals == 1
        assert report.signal_stats.hold_signals == 1
        assert report.signal_stats.unique_symbols == 3

    def test_avg_confidence(self, sample_signals, sample_prices):
        """测试平均置信度"""
        evaluator = SignalEvaluator(sample_signals, sample_prices)
        report = evaluator.evaluate()

        expected_avg = (0.9 + 0.8 + 0.7 + 0.6 + 0.5) / 5
        assert abs(report.signal_stats.avg_confidence - expected_avg) < 1e-10

    def test_return_stats_computation(self, sample_signals, sample_prices):
        """测试收益统计计算"""
        evaluator = SignalEvaluator(sample_signals, sample_prices, holding_periods=[1])
        report = evaluator.evaluate()

        # 应该有收益统计
        assert report.return_stats.total_pnl != 0 or len(sample_signals) == 0

    def test_decay_stats(self, sample_signals, sample_prices):
        """测试信号衰减统计"""
        periods = [1, 3, 5]
        evaluator = SignalEvaluator(sample_signals, sample_prices, holding_periods=periods)
        report = evaluator.evaluate()

        assert report.decay_stats.holding_periods == periods
        assert len(report.decay_stats.avg_returns) == len(periods)
        assert len(report.decay_stats.win_rates) == len(periods)

    def test_optimal_period(self, sample_signals, sample_prices):
        """测试最优持有期"""
        evaluator = SignalEvaluator(sample_signals, sample_prices, holding_periods=[1, 3, 5])
        report = evaluator.evaluate()

        assert report.decay_stats.optimal_period in [1, 3, 5]

    def test_by_symbol_stats(self, sample_signals, sample_prices):
        """测试按标的统计"""
        evaluator = SignalEvaluator(sample_signals, sample_prices, holding_periods=[1])
        report = evaluator.evaluate()

        # 至少有部分标的有统计
        # 注意：某些标的可能因为缺少价格数据而没有统计
        assert isinstance(report.by_symbol, dict)

    def test_report_summary(self, sample_signals, sample_prices):
        """测试报告摘要"""
        evaluator = SignalEvaluator(sample_signals, sample_prices)
        report = evaluator.evaluate()

        summary = report.summary()
        assert "信号评估报告" in summary
        assert "信号统计" in summary
        assert "收益分析" in summary
        assert "信号衰减" in summary


class TestReturnCalculation:
    """收益计算测试"""

    def test_buy_signal_return(self):
        """测试买入信号收益"""
        base_date = datetime(2024, 1, 1)
        signals = [Signal("A", SignalType.BUY, base_date, 100.0)]
        prices = {
            "A": {
                base_date: 100.0,
                base_date + timedelta(days=1): 110.0,  # +10%
            }
        }

        evaluator = SignalEvaluator(signals, prices, holding_periods=[1])
        report = evaluator.evaluate()

        assert abs(report.return_stats.avg_return - 0.1) < 1e-10

    def test_sell_signal_return(self):
        """测试卖出信号收益"""
        base_date = datetime(2024, 1, 1)
        signals = [Signal("A", SignalType.SELL, base_date, 100.0)]
        prices = {
            "A": {
                base_date: 100.0,
                base_date + timedelta(days=1): 90.0,  # 价格跌 10%，卖空盈利
            }
        }

        evaluator = SignalEvaluator(signals, prices, holding_periods=[1])
        report = evaluator.evaluate()

        assert abs(report.return_stats.avg_return - 0.1) < 1e-10

    def test_win_rate_calculation(self):
        """测试胜率计算"""
        base_date = datetime(2024, 1, 1)
        signals = [
            Signal("A", SignalType.BUY, base_date, 100.0),
            Signal("B", SignalType.BUY, base_date, 100.0),
            Signal("C", SignalType.BUY, base_date, 100.0),
        ]
        prices = {
            "A": {base_date: 100.0, base_date + timedelta(days=1): 110.0},  # win
            "B": {base_date: 100.0, base_date + timedelta(days=1): 105.0},  # win
            "C": {base_date: 100.0, base_date + timedelta(days=1): 95.0},   # loss
        }

        evaluator = SignalEvaluator(signals, prices, holding_periods=[1])
        report = evaluator.evaluate()

        # 2 wins out of 3
        assert abs(report.return_stats.win_rate - 2 / 3) < 1e-10

    def test_profit_factor(self):
        """测试盈亏比"""
        base_date = datetime(2024, 1, 1)
        signals = [
            Signal("A", SignalType.BUY, base_date, 100.0),
            Signal("B", SignalType.BUY, base_date, 100.0),
        ]
        prices = {
            "A": {base_date: 100.0, base_date + timedelta(days=1): 120.0},  # +20%
            "B": {base_date: 100.0, base_date + timedelta(days=1): 90.0},   # -10%
        }

        evaluator = SignalEvaluator(signals, prices, holding_periods=[1])
        report = evaluator.evaluate()

        # profit_factor = 0.2 / 0.1 = 2.0
        assert abs(report.return_stats.profit_factor - 2.0) < 1e-10


class TestEdgeCases:
    """边界情况测试"""

    def test_missing_price_data(self):
        """测试缺少价格数据"""
        base_date = datetime(2024, 1, 1)
        signals = [Signal("A", SignalType.BUY, base_date, 100.0)]
        prices = {}  # 没有价格数据

        evaluator = SignalEvaluator(signals, prices, holding_periods=[1])
        report = evaluator.evaluate()

        # 应该能正常运行，只是没有收益统计
        assert report.return_stats.total_pnl == 0.0

    def test_hold_signals_ignored(self):
        """测试 HOLD 信号被忽略"""
        base_date = datetime(2024, 1, 1)
        signals = [
            Signal("A", SignalType.HOLD, base_date, 100.0),
            Signal("A", SignalType.HOLD, base_date + timedelta(days=1), 105.0),
        ]
        prices = {
            "A": {
                base_date: 100.0,
                base_date + timedelta(days=1): 105.0,
                base_date + timedelta(days=2): 110.0,
            }
        }

        evaluator = SignalEvaluator(signals, prices, holding_periods=[1])
        report = evaluator.evaluate()

        # HOLD 信号不参与收益计算
        assert report.return_stats.total_pnl == 0.0

    def test_single_signal(self):
        """测试单个信号"""
        base_date = datetime(2024, 1, 1)
        signals = [Signal("A", SignalType.BUY, base_date, 100.0)]
        prices = {
            "A": {
                base_date: 100.0,
                base_date + timedelta(days=1): 110.0,
            }
        }

        evaluator = SignalEvaluator(signals, prices, holding_periods=[1])
        report = evaluator.evaluate()

        assert report.signal_stats.total_signals == 1
        assert report.signal_stats.signals_per_day == 1.0


class TestConvenienceFunction:
    """便捷函数测试"""

    def test_evaluate_signals_function(self):
        """测试 evaluate_signals 函数"""
        base_date = datetime(2024, 1, 1)
        signals = [Signal("A", SignalType.BUY, base_date, 100.0)]
        prices = {"A": {base_date: 100.0, base_date + timedelta(days=1): 105.0}}

        report = evaluate_signals(signals, prices, holding_periods=[1])

        assert isinstance(report, SignalEvaluationReport)
        assert report.signal_stats.total_signals == 1
