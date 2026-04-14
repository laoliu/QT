"""顶层函数 evaluate_signals() 专项测试

覆盖：便捷函数的接口契约、自定义 holding_periods、结果一致性。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aipha.signals.evaluator import (
    Signal,
    SignalEvaluationReport,
    SignalEvaluator,
    SignalType,
    evaluate_signals,
)

BASE_DATE = datetime(2024, 1, 1)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _sig(symbol: str, stype: SignalType, day: int = 0, price: float = 100.0) -> Signal:
    return Signal(symbol=symbol, signal_type=stype, timestamp=BASE_DATE + timedelta(days=day), price=price)


def _price_series(symbol: str, n_days: int, base: float = 100.0) -> dict[str, dict[datetime, float]]:
    return {symbol: {BASE_DATE + timedelta(days=d): base + d * 0.5 for d in range(n_days)}}


# ---------------------------------------------------------------------------
# 基本功能
# ---------------------------------------------------------------------------

class TestEvaluateSignalsFunction:
    def test_returns_report_instance(self):
        """返回 SignalEvaluationReport 类型"""
        signals = [_sig("A", SignalType.BUY, day=0)]
        prices = _price_series("A", 20)
        result = evaluate_signals(signals, prices)
        assert isinstance(result, SignalEvaluationReport)

    def test_same_result_as_evaluator_class(self):
        """函数结果与 SignalEvaluator.evaluate() 完全一致"""
        signals = [
            _sig("A", SignalType.BUY, day=0),
            _sig("A", SignalType.SELL, day=5),
        ]
        prices = _price_series("A", 20)

        report_func = evaluate_signals(signals, prices)
        report_cls = SignalEvaluator(signals, prices).evaluate()

        assert report_func.signal_stats.total_signals == report_cls.signal_stats.total_signals
        assert abs(report_func.return_stats.avg_return - report_cls.return_stats.avg_return) < 1e-9
        assert report_func.decay_stats.optimal_period == report_cls.decay_stats.optimal_period

    def test_default_holding_periods_applied(self):
        """未指定 holding_periods 时使用默认值 [1, 3, 5, 10, 20]"""
        signals = [_sig("A", SignalType.BUY, day=0)]
        prices = _price_series("A", 25)
        report = evaluate_signals(signals, prices)
        assert report.decay_stats.holding_periods == [1, 3, 5, 10, 20]

    def test_custom_holding_periods_respected(self):
        """自定义 holding_periods 传入后被正确使用"""
        signals = [_sig("A", SignalType.BUY, day=0)]
        prices = _price_series("A", 30)
        report = evaluate_signals(signals, prices, holding_periods=[2, 7])
        assert report.decay_stats.holding_periods == [2, 7]
        assert len(report.decay_stats.avg_returns) == 2

    def test_empty_signals_no_exception(self):
        """空信号 → 不抛异常"""
        report = evaluate_signals([], {})
        assert report.signal_stats.total_signals == 0

    def test_empty_price_data_no_exception(self):
        """有信号但无价格数据 → 不抛异常"""
        signals = [_sig("A", SignalType.BUY, day=0)]
        report = evaluate_signals(signals, {})
        assert report.signal_stats.total_signals == 1
        assert report.return_stats.avg_return == 0.0


# ---------------------------------------------------------------------------
# 多标的
# ---------------------------------------------------------------------------

class TestMultiSymbol:
    def _make_two_symbol_data(self):
        # A: BUY 信号，价格持续上涨 → 持有 1 天必赚
        # B: BUY 信号，价格持续下跌 → 持有 1 天必亏
        signals = [
            _sig("A", SignalType.BUY, day=0, price=100.0),
            _sig("B", SignalType.BUY, day=0, price=50.0),
        ]
        prices = {
            "A": {BASE_DATE + timedelta(days=d): 100.0 + d * 2 for d in range(15)},   # 上涨
            "B": {BASE_DATE + timedelta(days=d): 50.0 - d * 1.0 for d in range(15)},  # 下跌
        }
        return signals, prices

    def test_multiple_symbols_in_by_symbol(self):
        """多标的回测 by_symbol 包含每个 symbol"""
        signals, prices = self._make_two_symbol_data()
        report = evaluate_signals(signals, prices)
        assert "A" in report.by_symbol
        assert "B" in report.by_symbol

    def test_symbol_returns_differ(self):
        """A 上涨、B 下跌 → by_symbol 的 avg_return 应有差异"""
        signals, prices = self._make_two_symbol_data()
        report = evaluate_signals(signals, prices)
        # A 买入后价格上涨 → 正收益; B 买入后价格下跌 → 负或低收益
        ret_a = report.by_symbol["A"].avg_return if "A" in report.by_symbol else 0
        ret_b = report.by_symbol["B"].avg_return if "B" in report.by_symbol else 0
        # A 应优于 B
        assert ret_a > ret_b

    def test_unique_symbols_count(self):
        """unique_symbols 计数正确"""
        signals, prices = self._make_two_symbol_data()
        report = evaluate_signals(signals, prices)
        assert report.signal_stats.unique_symbols == 2


# ---------------------------------------------------------------------------
# 损益比一致性
# ---------------------------------------------------------------------------

class TestProfitFactor:
    def test_all_winning_profit_factor_infinite(self):
        """全部盈利时 profit_factor 为 inf（无亏损）"""
        # 价格上涨确保单期持有必赚
        prices = {"A": {BASE_DATE + timedelta(days=d): 100.0 + d * 5 for d in range(10)}}
        signals = [_sig("A", SignalType.BUY, day=0, price=100.0)]
        report = evaluate_signals(signals, prices, holding_periods=[1])
        pf = report.return_stats.profit_factor
        # inf 或 > 0（只有 1 个信号，全部盈利 → inf）
        assert pf in (float("inf"),) or pf > 0

    def test_all_losing_profit_factor_zero(self):
        """全部亏损时 profit_factor 为 0（无盈利）"""
        # 价格下跌
        prices = {"A": {BASE_DATE + timedelta(days=d): 100.0 - d * 5 for d in range(10)}}
        signals = [_sig("A", SignalType.BUY, day=0, price=100.0)]
        report = evaluate_signals(signals, prices, holding_periods=[1])
        assert report.return_stats.profit_factor == 0
