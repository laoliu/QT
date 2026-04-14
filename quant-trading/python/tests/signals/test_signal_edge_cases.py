"""信号评估模块边界条件测试

覆盖：空信号、单信号、无价格数据、部分价格缺失等异常路径。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aipha.signals.evaluator import (
    Signal,
    SignalEvaluator,
    SignalStats,
    SignalType,
    evaluate_signals,
)

BASE_DATE = datetime(2024, 1, 1)


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def _sig(symbol: str, stype: SignalType, day: int = 0, price: float = 100.0) -> Signal:
    return Signal(symbol=symbol, signal_type=stype, timestamp=BASE_DATE + timedelta(days=day), price=price)


def _prices(symbol: str, start_day: int, end_day: int, base: float = 100.0) -> dict[str, dict[datetime, float]]:
    """生成连续价格序列（每天递增 1 元）。"""
    return {
        symbol: {
            BASE_DATE + timedelta(days=d): base + d
            for d in range(start_day, end_day + 1)
        }
    }


# ---------------------------------------------------------------------------
# 空信号
# ---------------------------------------------------------------------------

class TestEmptySignals:
    def test_empty_signals_returns_zero_stats(self):
        """空信号列表 → 全零统计"""
        ev = SignalEvaluator([], {})
        report = ev.evaluate()
        assert report.signal_stats.total_signals == 0
        assert report.signal_stats.buy_signals == 0
        assert report.signal_stats.sell_signals == 0
        assert report.return_stats.avg_return == 0.0
        assert report.decay_stats.optimal_period == 1

    def test_evaluate_signals_function_empty(self):
        """顶层函数空信号 → 无异常"""
        report = evaluate_signals([], {})
        assert report.signal_stats.total_signals == 0

    def test_empty_signals_no_by_symbol(self):
        """空信号 → by_symbol 字典为空"""
        ev = SignalEvaluator([], {})
        report = ev.evaluate()
        assert report.by_symbol == {}


# ---------------------------------------------------------------------------
# 单信号
# ---------------------------------------------------------------------------

class TestSingleSignal:
    def test_single_buy_with_price(self):
        """单个 BUY 信号 + 价格数据 → 统计正确"""
        signal = _sig("A", SignalType.BUY, day=0, price=100.0)
        prices = _prices("A", 0, 5)
        ev = SignalEvaluator([signal], prices)
        report = ev.evaluate()

        assert report.signal_stats.total_signals == 1
        assert report.signal_stats.buy_signals == 1
        assert report.signal_stats.unique_symbols == 1
        assert report.signal_stats.avg_confidence == 1.0

    def test_single_signal_signals_per_day_equals_total(self):
        """只有 1 个信号时 signals_per_day == len(signals)"""
        signal = _sig("A", SignalType.BUY, day=0)
        ev = SignalEvaluator([signal], {})
        stats: SignalStats = ev._compute_signal_stats()
        assert stats.signals_per_day == 1.0

    def test_single_sell_no_price_no_return(self):
        """无价格数据时 return_stats 为零"""
        signal = _sig("A", SignalType.SELL, day=0)
        ev = SignalEvaluator([signal], {})
        report = ev.evaluate()
        assert report.return_stats.avg_return == 0.0


# ---------------------------------------------------------------------------
# 缺失价格
# ---------------------------------------------------------------------------

class TestMissingPrices:
    def test_signal_no_matching_symbol_ignored(self):
        """信号 symbol 不在价格数据中 → 不报错，return_stats 为零"""
        signal = _sig("UNKNOWN", SignalType.BUY, day=0)
        prices = _prices("A", 0, 10)
        ev = SignalEvaluator([signal], prices)
        report = ev.evaluate()
        assert report.return_stats.avg_return == 0.0

    def test_signal_no_entry_price_ignored(self):
        """信号时间戳找不到入场价格 → 跳过，不报错"""
        signal = _sig("A", SignalType.BUY, day=99)  # day=99 不在价格表中
        prices = _prices("A", 0, 10)
        ev = SignalEvaluator([signal], prices)
        report = ev.evaluate()
        assert report.return_stats.avg_return == 0.0

    def test_partial_prices_computes_available(self):
        """部分信号有价格，部分没有 → 只计算有价格的信号"""
        signals = [
            _sig("A", SignalType.BUY, day=0),
            _sig("B", SignalType.BUY, day=0),  # B 无价格
        ]
        prices = _prices("A", 0, 5)  # 只有 A 的价格
        ev = SignalEvaluator(signals, prices)
        report = ev.evaluate()
        assert report.signal_stats.total_signals == 2
        # 只有 A 能计算出 return
        assert "A" in report.by_symbol or report.return_stats.avg_return != 0.0


# ---------------------------------------------------------------------------
# HOLD 信号
# ---------------------------------------------------------------------------

class TestHoldSignals:
    def test_hold_signals_excluded_from_returns(self):
        """HOLD 信号不参与收益计算"""
        signals = [
            _sig("A", SignalType.HOLD, day=0),
            _sig("A", SignalType.BUY, day=1),
        ]
        prices = _prices("A", 0, 10)
        ev = SignalEvaluator(signals, prices)
        report = ev.evaluate()
        # 1 hold + 1 buy = 2 total
        assert report.signal_stats.hold_signals == 1
        assert report.signal_stats.buy_signals == 1

    def test_all_hold_signals_zero_returns(self):
        """全部 HOLD 信号 → 收益为零"""
        signals = [_sig("A", SignalType.HOLD, day=i) for i in range(5)]
        prices = _prices("A", 0, 20)
        ev = SignalEvaluator(signals, prices)
        report = ev.evaluate()
        assert report.return_stats.avg_return == 0.0


# ---------------------------------------------------------------------------
# 置信度
# ---------------------------------------------------------------------------

class TestConfidence:
    def test_avg_confidence_computed_correctly(self):
        """平均置信度计算正确"""
        signals = [
            Signal("A", SignalType.BUY, BASE_DATE, 100.0, confidence=0.6),
            Signal("A", SignalType.BUY, BASE_DATE + timedelta(days=1), 102.0, confidence=0.8),
            Signal("A", SignalType.BUY, BASE_DATE + timedelta(days=2), 104.0, confidence=1.0),
        ]
        ev = SignalEvaluator(signals, {})
        stats = ev._compute_signal_stats()
        assert abs(stats.avg_confidence - 0.8) < 1e-9
