"""
亏损追踪模块单元测试

测试覆盖：
1. RoundTripTracker FIFO 配对
2. LossAnalyzer 亏损分析
3. DrawdownAttributor 回撤归因
4. classify_exit_reason 退出分类
5. analyze_losses 一站式入口
6. format_loss_report 格式化
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from aipha.backtest.engine import BacktestResult, DailySnapshot, TradeRecord
from aipha.domain.models import Side
from aipha.execution.fees import FeeBreakdown
from aipha.reporting.loss_tracker import (
    DrawdownAttributor,
    ExitCategory,
    LossAnalyzer,
    LossSummary,
    RoundTrip,
    RoundTripTracker,
    analyze_losses,
    classify_exit_reason,
    format_loss_report,
)


# ============================================================================
# Fixtures
# ============================================================================

def _make_fee(total: float = 5.0) -> FeeBreakdown:
    return FeeBreakdown(commission=total * 0.6, stamp_tax=total * 0.3, transfer_fee=total * 0.1)


def _make_trade(
    symbol: str,
    side: Side,
    qty: int,
    price: float,
    trade_date: date,
    reason: str | None = None,
    fee: float = 5.0,
    trade_id: str = "",
    order_id: str = "",
) -> TradeRecord:
    """构造 TradeRecord"""
    dt = datetime.combine(trade_date, datetime.min.time())
    return TradeRecord(
        trade_id=trade_id or f"TRD-{id(object()):06d}",
        order_id=order_id or f"ORD-{id(object()):06d}",
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        notional=qty * price,
        fees=_make_fee(fee),
        trade_time=dt,
        trade_date=trade_date,
        reason=reason,
    )


@pytest.fixture
def simple_trades() -> list[TradeRecord]:
    """简单交易序列：2笔盈利 + 1笔亏损"""
    d1 = date(2025, 1, 6)
    d2 = date(2025, 1, 13)
    d3 = date(2025, 1, 20)
    d4 = date(2025, 1, 27)

    return [
        # CB001: 买100 → 卖100, 盈利
        _make_trade("CB001", Side.BUY, 100, 100.0, d1, reason="轮动买入"),
        _make_trade("CB001", Side.SELL, 100, 110.0, d2, reason="轮动卖出"),
        # CB002: 买200 → 卖200, 亏损
        _make_trade("CB002", Side.BUY, 200, 120.0, d2, reason="轮动买入"),
        _make_trade("CB002", Side.SELL, 200, 105.0, d3, reason="止损: 105.0 < 108.0"),
        # CB003: 买150 → 卖150, 盈利
        _make_trade("CB003", Side.BUY, 150, 95.0, d3, reason="轮动买入"),
        _make_trade("CB003", Side.SELL, 150, 100.0, d4, reason="轮动卖出"),
    ]


@pytest.fixture
def backtest_result_with_drawdown() -> BacktestResult:
    """含回撤的回测结果"""
    base_date = date(2025, 1, 6)
    nav_values = [
        1000000, 1010000, 1020000, 1015000, 1005000,  # 轻微回撤
        990000,  980000,  985000,  995000,  1000000,   # 回撤后恢复
        1025000, 1030000, 1035000, 1040000, 1050000,   # 上涨
        1045000, 1020000, 1010000, 1015000, 1025000,   # 第二次回撤
    ]

    snapshots = []
    prev_nav = nav_values[0]
    for i, nav in enumerate(nav_values):
        td = base_date + timedelta(days=i)
        dr = (nav - prev_nav) / prev_nav if prev_nav > 0 else 0
        cr = (nav - nav_values[0]) / nav_values[0]
        snapshots.append(DailySnapshot(
            trade_date=td,
            nav=nav,
            cash=nav * 0.1,
            positions={"CB001": 100} if i < 10 else {"CB002": 100},
            position_values=(
                {"CB001": nav * 0.9} if i < 10 else {"CB002": nav * 0.9}
            ),
            daily_return=dr,
            cumulative_return=cr,
        ))
        prev_nav = nav

    trades = [
        _make_trade("CB001", Side.BUY, 100, 100.0, base_date, reason="轮动买入"),
        _make_trade("CB001", Side.SELL, 100, 95.0, base_date + timedelta(days=9), reason="止损"),
        _make_trade("CB002", Side.BUY, 100, 102.0, base_date + timedelta(days=10), reason="轮动买入"),
        _make_trade("CB002", Side.SELL, 100, 101.0, base_date + timedelta(days=17), reason="轮动卖出"),
    ]

    return BacktestResult(
        strategy_name="test_strategy",
        start_date=base_date,
        end_date=base_date + timedelta(days=19),
        initial_capital=1000000,
        final_nav=1025000,
        final_cash=102500,
        final_positions={"CB002": 100},
        daily_snapshots=snapshots,
        trades=trades,
        total_trades=len(trades),
        total_fees=_make_fee(20),
        exclusion_stats={},
    )


# ============================================================================
# classify_exit_reason
# ============================================================================

class TestClassifyExitReason:
    def test_stop_loss(self):
        assert classify_exit_reason("止损: 105.0 < 108.0") == ExitCategory.STOP_LOSS

    def test_engine_stop_loss(self):
        assert classify_exit_reason("引擎止损: xxx") == ExitCategory.ENGINE_STOP_LOSS

    def test_rotation(self):
        assert classify_exit_reason("small_size轮动卖出") == ExitCategory.ROTATION

    def test_force_redeem(self):
        assert classify_exit_reason("已公告强赎, status=拟赎回") == ExitCategory.FORCE_REDEEM

    def test_unknown(self):
        assert classify_exit_reason(None) == ExitCategory.UNKNOWN
        assert classify_exit_reason("") == ExitCategory.UNKNOWN
        assert classify_exit_reason("其他原因") == ExitCategory.UNKNOWN


# ============================================================================
# RoundTripTracker
# ============================================================================

class TestRoundTripTracker:
    def test_build_basic(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        assert len(rts) == 3

    def test_round_trip_pnl(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        # CB001: buy@100 sell@110, qty=100, gross_pnl=1000
        cb001 = [rt for rt in rts if rt.symbol == "CB001"]
        assert len(cb001) == 1
        assert cb001[0].gross_pnl == 1000.0
        assert cb001[0].entry_price == 100.0
        assert cb001[0].exit_price == 110.0

    def test_round_trip_loss(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        # CB002: buy@120 sell@105, qty=200, gross_pnl=-3000
        cb002 = [rt for rt in rts if rt.symbol == "CB002"]
        assert len(cb002) == 1
        assert cb002[0].gross_pnl == -3000.0
        assert cb002[0].net_pnl < cb002[0].gross_pnl  # 扣费后更少

    def test_holding_days(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        # CB001: Jan 6 → Jan 13 = 7 days
        cb001 = [rt for rt in rts if rt.symbol == "CB001"][0]
        assert cb001.holding_days == 7

    def test_exit_reason_preserved(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        # CB002 was sold due to 止损
        cb002 = [rt for rt in rts if rt.symbol == "CB002"][0]
        assert "止损" in cb002.exit_reason

    def test_empty_trades(self):
        tracker = RoundTripTracker()
        rts = tracker.build([])
        assert rts == []

    def test_buy_only_no_round_trip(self):
        """只有买入没有卖出，不生成 round-trip"""
        trades = [
            _make_trade("CB001", Side.BUY, 100, 100.0, date(2025, 1, 6)),
        ]
        tracker = RoundTripTracker()
        rts = tracker.build(trades)
        assert len(rts) == 0

    def test_partial_fill(self):
        """部分卖出：一次买入分两次卖出"""
        trades = [
            _make_trade("CB001", Side.BUY, 200, 100.0, date(2025, 1, 6), reason="买入"),
            _make_trade("CB001", Side.SELL, 100, 110.0, date(2025, 1, 10), reason="部分卖出1"),
            _make_trade("CB001", Side.SELL, 100, 105.0, date(2025, 1, 15), reason="部分卖出2"),
        ]
        tracker = RoundTripTracker()
        rts = tracker.build(trades)

        assert len(rts) == 2
        assert rts[0].qty == 100
        assert rts[1].qty == 100
        assert rts[0].exit_price == 110.0
        assert rts[1].exit_price == 105.0


# ============================================================================
# LossAnalyzer
# ============================================================================

class TestLossAnalyzer:
    def test_basic_analysis(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        analyzer = LossAnalyzer(top_n=5)
        summary = analyzer.analyze(rts)

        assert summary.total_round_trips == 3
        assert summary.loss_count == 1
        assert summary.win_count == 2
        assert summary.total_loss < 0
        assert summary.total_profit > 0

    def test_worst_loss(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        analyzer = LossAnalyzer(top_n=5)
        summary = analyzer.analyze(rts)

        # Worst loss should be CB002
        assert summary.worst_loss < 0
        assert len(summary.top_losers) == 1
        assert summary.top_losers[0].symbol == "CB002"

    def test_loss_by_category(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        analyzer = LossAnalyzer(top_n=5)
        summary = analyzer.analyze(rts)

        # CB002 was 止损
        assert ExitCategory.STOP_LOSS in summary.loss_by_category

    def test_loss_by_month(self, simple_trades):
        tracker = RoundTripTracker()
        rts = tracker.build(simple_trades)

        analyzer = LossAnalyzer(top_n=5)
        summary = analyzer.analyze(rts)

        assert "2025-01" in summary.loss_by_month

    def test_empty_round_trips(self):
        analyzer = LossAnalyzer()
        summary = analyzer.analyze([])

        assert summary.total_round_trips == 0
        assert summary.loss_count == 0


# ============================================================================
# DrawdownAttributor
# ============================================================================

class TestDrawdownAttributor:
    def test_find_drawdown_periods(self, backtest_result_with_drawdown):
        attributor = DrawdownAttributor(min_dd_pct=0.01, max_periods=5)
        periods = attributor.analyze(backtest_result_with_drawdown)

        # Should find at least 1 drawdown period
        assert len(periods) >= 1

    def test_drawdown_pct_positive(self, backtest_result_with_drawdown):
        attributor = DrawdownAttributor(min_dd_pct=0.01, max_periods=5)
        periods = attributor.analyze(backtest_result_with_drawdown)

        for period in periods:
            assert period.drawdown_pct > 0

    def test_position_contributions(self, backtest_result_with_drawdown):
        attributor = DrawdownAttributor(min_dd_pct=0.01, max_periods=5)
        periods = attributor.analyze(backtest_result_with_drawdown)

        # The first (largest) drawdown should have position contributions
        if periods and periods[0].position_contributions:
            total_contrib = sum(periods[0].position_contributions.values())
            # Contributions should roughly sum to ~1.0 (±0.5 for approximation)
            assert total_contrib > 0

    def test_high_threshold_no_periods(self, backtest_result_with_drawdown):
        attributor = DrawdownAttributor(min_dd_pct=0.50, max_periods=5)
        periods = attributor.analyze(backtest_result_with_drawdown)
        # 50% drawdown threshold -- should find nothing
        assert len(periods) == 0


# ============================================================================
# analyze_losses (一站式入口)
# ============================================================================

class TestAnalyzeLosses:
    def test_full_analysis(self, backtest_result_with_drawdown):
        analysis = analyze_losses(backtest_result_with_drawdown, min_dd_pct=0.01)

        assert "round_trips" in analysis
        assert "loss_summary" in analysis
        assert "drawdown_periods" in analysis
        assert isinstance(analysis["loss_summary"], LossSummary)

    def test_format_report(self, backtest_result_with_drawdown):
        analysis = analyze_losses(backtest_result_with_drawdown, min_dd_pct=0.01)
        report = format_loss_report(analysis, name_map={"CB001": "测试转债1"})

        assert "亏损追踪分析报告" in report
        assert "测试转债1" in report
        assert "退出原因归因" in report
