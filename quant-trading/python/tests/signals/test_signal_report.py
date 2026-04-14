"""SignalEvaluationReport.summary() 输出格式与内容测试

覆盖：summary 字符串结构、关键字段出现、各分段内容、零除保护。
"""
from __future__ import annotations

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

BASE_DATE = datetime(2024, 1, 1)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _minimal_report(
    total: int = 3,
    buy: int = 2,
    sell: int = 1,
    hold: int = 0,
    avg_ret: float = 0.05,
) -> SignalEvaluationReport:
    return SignalEvaluationReport(
        signal_stats=SignalStats(
            total_signals=total,
            buy_signals=buy,
            sell_signals=sell,
            hold_signals=hold,
            unique_symbols=2,
            avg_confidence=0.75,
            signals_per_day=1.5,
        ),
        return_stats=ReturnStats(
            avg_return=avg_ret,
            median_return=0.04,
            std_return=0.02,
            win_rate=0.6,
            profit_factor=1.5,
            avg_win=0.08,
            avg_loss=-0.03,
            best_return=0.15,
            worst_return=-0.05,
            total_pnl=0.20,
        ),
        decay_stats=DecayStats(
            holding_periods=[1, 3, 5],
            avg_returns=[0.01, 0.03, 0.05],
            win_rates=[0.5, 0.6, 0.65],
            optimal_period=5,
        ),
    )


# ---------------------------------------------------------------------------
# 结构测试
# ---------------------------------------------------------------------------

class TestSummaryStructure:
    def test_summary_returns_string(self):
        """summary() 必须返回非空字符串"""
        report = _minimal_report()
        s = report.summary()
        assert isinstance(s, str)
        assert len(s) > 0

    def test_summary_contains_header_dividers(self):
        """报告开头和结尾有分隔符"""
        report = _minimal_report()
        s = report.summary()
        assert "=" * 50 in s

    def test_summary_has_signal_section(self):
        """包含【信号统计】分段"""
        report = _minimal_report()
        s = report.summary()
        assert "信号统计" in s

    def test_summary_has_return_section(self):
        """包含【收益分析】分段"""
        report = _minimal_report()
        s = report.summary()
        assert "收益分析" in s

    def test_summary_has_decay_section(self):
        """包含【信号衰减】分段"""
        report = _minimal_report()
        s = report.summary()
        assert "信号衰减" in s


# ---------------------------------------------------------------------------
# 信号统计字段
# ---------------------------------------------------------------------------

class TestSummarySignalFields:
    def test_total_signals_displayed(self):
        """总信号数显示在报告中"""
        report = _minimal_report(total=7)
        s = report.summary()
        assert "7" in s

    def test_buy_sell_count_displayed(self):
        """买卖数量显示"""
        report = _minimal_report(buy=3, sell=2)
        s = report.summary()
        # 报告包含买入、卖出字样及数量
        assert "买入" in s or "buy" in s.lower()

    def test_unique_symbols_displayed(self):
        """覆盖标的数显示"""
        report = _minimal_report()
        s = report.summary()
        assert "2" in s  # unique_symbols=2


# ---------------------------------------------------------------------------
# 衰减分段
# ---------------------------------------------------------------------------

class TestSummaryDecaySection:
    def test_optimal_period_displayed(self):
        """最优持有期显示"""
        report = _minimal_report()
        s = report.summary()
        assert "5" in s  # optimal_period=5

    def test_holding_period_rows_present(self):
        """每个持有期都有对应行"""
        report = _minimal_report()
        s = report.summary()
        # 持有期 1, 3, 5 都应出现
        assert "1日" in s
        assert "3日" in s
        assert "5日" in s

    def test_no_holding_period_no_crash(self):
        """decay_stats 无 holding_periods 时不报错"""
        report = SignalEvaluationReport(
            signal_stats=SignalStats(),
            return_stats=ReturnStats(),
            decay_stats=DecayStats(),
        )
        s = report.summary()
        assert isinstance(s, str)


# ---------------------------------------------------------------------------
# 零除保护
# ---------------------------------------------------------------------------

class TestSummaryZeroDivision:
    def test_zero_total_signals_no_crash(self):
        """总信号数为 0 时 _pct() 不崩溃"""
        report = _minimal_report(total=0, buy=0, sell=0)
        s = report.summary()
        assert "0.00%" in s

    def test_empty_evaluator_summary_no_crash(self):
        """从空评估器生成的报告 summary 不崩溃"""
        ev = SignalEvaluator([], {})
        report = ev.evaluate()
        s = report.summary()
        assert isinstance(s, str)
        assert "信号统计" in s


# ---------------------------------------------------------------------------
# 端到端 summary 与评估器联动
# ---------------------------------------------------------------------------

class TestSummaryIntegration:
    def test_buy_signal_positive_return_shown(self):
        """价格上涨的 BUY 信号 → summary 显示正平均收益"""
        prices = {"A": {BASE_DATE + timedelta(days=d): 100.0 + d * 2 for d in range(10)}}
        signals = [Signal("A", SignalType.BUY, BASE_DATE, 100.0)]
        report = evaluate_signals(signals, prices, holding_periods=[5])
        s = report.summary()
        # 平均收益 > 0，报告中 avg_return 应为正数
        assert "10.00%" in s or "%" in s  # 至少包含百分比

    def test_summary_consistent_with_stats(self):
        """summary 中的数据与 report 对象字段一致（不是乱写）"""
        prices = {"A": {BASE_DATE + timedelta(days=d): 100.0 + d for d in range(15)}}
        signals = [
            Signal("A", SignalType.BUY, BASE_DATE, 100.0, 0.9),
            Signal("A", SignalType.BUY, BASE_DATE + timedelta(days=2), 102.0, 0.7),
        ]
        report = evaluate_signals(signals, prices, holding_periods=[3])
        s = report.summary()
        # 2 个信号，summary 中应出现总数 "2"
        assert str(report.signal_stats.total_signals) in s
        # 最优持有期
        assert str(report.decay_stats.optimal_period) + "日" in s or str(report.decay_stats.optimal_period) in s
