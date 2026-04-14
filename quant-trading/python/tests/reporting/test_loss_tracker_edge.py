"""Loss Tracker 模块边界测试 — Phase 40

覆盖 loss_tracker.py 中的未覆盖分支：
- classify_exit_reason 各分支
- RoundTripTracker FIFO: 多买→单卖、无匹配买入、entry_cost=0
- LossAnalyzer: 空输入、全盈利、全亏损
- format_loss_report: name_map、无亏损、无回撤
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

import pytest

from aipha.reporting.loss_tracker import (
    ExitCategory,
    LossAnalyzer,
    RoundTrip,
    RoundTripTracker,
    classify_exit_reason,
    format_loss_report,
)


# ============================================================
# 1. classify_exit_reason 所有分支
# ============================================================
class TestClassifyExitReason:
    def test_none(self):
        assert classify_exit_reason(None) == ExitCategory.UNKNOWN

    def test_empty(self):
        assert classify_exit_reason("") == ExitCategory.UNKNOWN

    def test_stop_loss(self):
        assert classify_exit_reason("止损退出") == ExitCategory.STOP_LOSS

    def test_stop_loss_english(self):
        assert classify_exit_reason("stop loss triggered") == ExitCategory.STOP_LOSS

    def test_engine_stop_loss(self):
        assert classify_exit_reason("引擎止损") == ExitCategory.ENGINE_STOP_LOSS

    def test_engine_stop_english(self):
        assert classify_exit_reason("engine stop") == ExitCategory.ENGINE_STOP_LOSS

    def test_rotation(self):
        assert classify_exit_reason("轮动调仓") == ExitCategory.ROTATION

    def test_rebalance(self):
        assert classify_exit_reason("rebalance signal") == ExitCategory.ROTATION

    def test_force_redeem(self):
        assert classify_exit_reason("强赎退出") == ExitCategory.FORCE_REDEEM

    def test_called_redeem(self):
        assert classify_exit_reason("bond called") == ExitCategory.FORCE_REDEEM

    def test_manual(self):
        assert classify_exit_reason("手动退出") == ExitCategory.MANUAL

    def test_manual_english(self):
        assert classify_exit_reason("manual close") == ExitCategory.MANUAL

    def test_unknown_text(self):
        assert classify_exit_reason("random reason") == ExitCategory.UNKNOWN


# ============================================================
# Mock trade records for RoundTripTracker
# ============================================================
class _Side:
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class _MockFees:
    total: float = 0.0


@dataclass
class _MockTrade:
    symbol: str
    side: object  # Side enum
    qty: int
    price: float
    trade_date: date
    trade_time: datetime
    fees: _MockFees = field(default_factory=lambda: _MockFees(1.0))
    reason: str = ""


# Need to match Side enum
def _make_side():
    from aipha.domain.models import Side
    return Side


# ============================================================
# 2. RoundTripTracker: multi-buy → single-sell (FIFO consumption)
# ============================================================
class TestRoundTripMultiBuy:
    def test_two_buys_one_sell(self):
        """2 次买入 → 1 次全卖出（FIFO 消耗两个批次）"""
        Side = _make_side()
        trades = [
            _MockTrade("000001.SZ", Side.BUY, 100, 10.0,
                       date(2023, 1, 3), datetime(2023, 1, 3, 10, 0)),
            _MockTrade("000001.SZ", Side.BUY, 200, 12.0,
                       date(2023, 1, 4), datetime(2023, 1, 4, 10, 0)),
            _MockTrade("000001.SZ", Side.SELL, 300, 13.0,
                       date(2023, 1, 5), datetime(2023, 1, 5, 10, 0)),
        ]
        tracker = RoundTripTracker()
        rts = tracker.build(trades)
        # 应该产生 2 个 round-trip: lot1(100@10) + lot2(200@12)
        assert len(rts) == 2
        assert rts[0].qty == 100
        assert rts[0].entry_price == 10.0
        assert rts[1].qty == 200
        assert rts[1].entry_price == 12.0


# ============================================================
# 3. RoundTripTracker: 卖出无匹配买入
# ============================================================
class TestRoundTripNoMatch:
    def test_sell_without_buy(self):
        """卖出时无对应买入队列 → 跳过（不崩溃）"""
        Side = _make_side()
        trades = [
            _MockTrade("000001.SZ", Side.SELL, 100, 10.0,
                       date(2023, 1, 3), datetime(2023, 1, 3, 10, 0)),
        ]
        tracker = RoundTripTracker()
        rts = tracker.build(trades)
        assert len(rts) == 0


# ============================================================
# 4. RoundTripTracker: 空交易列表
# ============================================================
class TestRoundTripEmpty:
    def test_empty_trades(self):
        tracker = RoundTripTracker()
        assert tracker.build([]) == []


# ============================================================
# 5. LossAnalyzer: 空输入
# ============================================================
class TestLossAnalyzerEmpty:
    def test_empty_round_trips(self):
        analyzer = LossAnalyzer()
        summary = analyzer.analyze([])
        assert summary.total_round_trips == 0
        assert summary.loss_count == 0
        assert summary.win_count == 0
        assert summary.top_losers == []


# ============================================================
# 6. LossAnalyzer: 全盈利
# ============================================================
class TestLossAnalyzerAllWins:
    def test_all_wins(self):
        rts = [
            RoundTrip("A", date(2023, 1, 3), date(2023, 1, 5), 10.0, 12.0,
                       100, 200.0, 2.0, 198.0, 0.198, 2),
            RoundTrip("B", date(2023, 1, 3), date(2023, 1, 6), 20.0, 22.0,
                       50, 100.0, 1.0, 99.0, 0.099, 3),
        ]
        analyzer = LossAnalyzer()
        summary = analyzer.analyze(rts)
        assert summary.loss_count == 0
        assert summary.win_count == 2
        assert summary.worst_loss == 0.0
        assert summary.loss_by_category == {}


# ============================================================
# 7. LossAnalyzer: 全亏损 + top_n 限制
# ============================================================
class TestLossAnalyzerAllLosses:
    def test_all_losses(self):
        rts = [
            RoundTrip("A", date(2023, 1, 3), date(2023, 1, 5), 10.0, 8.0,
                       100, -200.0, 2.0, -202.0, -0.202, 2, exit_reason="止损"),
            RoundTrip("B", date(2023, 1, 3), date(2023, 2, 1), 20.0, 18.0,
                       50, -100.0, 1.0, -101.0, -0.101, 29, exit_reason="轮动"),
        ]
        analyzer = LossAnalyzer(top_n=1)
        summary = analyzer.analyze(rts)
        assert summary.win_count == 0
        assert summary.loss_count == 2
        assert len(summary.top_losers) == 1  # top_n=1
        assert summary.worst_loss == pytest.approx(-202.0)
        assert ExitCategory.STOP_LOSS in summary.loss_by_category
        assert ExitCategory.ROTATION in summary.loss_by_category


# ============================================================
# 8. format_loss_report: name_map & 无亏损
# ============================================================
class TestFormatLossReport:
    def test_format_with_name_map(self):
        """有 name_map 时应替换显示"""
        rts = [
            RoundTrip("000001.SZ", date(2023, 1, 3), date(2023, 1, 5),
                       10.0, 8.0, 100, -200.0, 2.0, -202.0, -0.202, 2,
                       exit_reason="止损"),
        ]
        summary = LossAnalyzer().analyze(rts)
        analysis = {
            "loss_summary": summary,
            "drawdown_periods": [],
            "round_trips": rts,
        }
        report = format_loss_report(analysis, name_map={"000001.SZ": "平安银行"})
        assert "平安银行" in report

    def test_format_no_losses(self):
        rts = [
            RoundTrip("A", date(2023, 1, 3), date(2023, 1, 5),
                       10.0, 12.0, 100, 200.0, 2.0, 198.0, 0.198, 2),
        ]
        summary = LossAnalyzer().analyze(rts)
        analysis = {
            "loss_summary": summary,
            "drawdown_periods": [],
            "round_trips": rts,
        }
        report = format_loss_report(analysis)
        assert "无亏损交易" in report

    def test_format_empty(self):
        summary = LossAnalyzer().analyze([])
        analysis = {
            "loss_summary": summary,
            "drawdown_periods": [],
            "round_trips": [],
        }
        report = format_loss_report(analysis)
        assert "亏损追踪分析报告" in report
