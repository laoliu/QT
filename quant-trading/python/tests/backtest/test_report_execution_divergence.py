"""回测报告执行口径差异接线测试。

覆盖 generate_report(price_lookup=...) → BacktestReport.execution_divergence
的接入，以及无行情时的降级行为。
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from aipha.backtest.engine import BacktestResult, DailySnapshot, TradeRecord
from aipha.backtest.report import generate_report
from aipha.domain.models import Side
from aipha.execution.fees import FeeBreakdown


@pytest.fixture
def fee() -> FeeBreakdown:
    return FeeBreakdown(commission=5.0, stamp_tax=0.0, transfer_fee=0.1)


@pytest.fixture
def result(fee: FeeBreakdown) -> BacktestResult:
    snapshots = [
        DailySnapshot(
            trade_date=date(2026, 3, d),
            nav=100_000.0 + d * 100,
            cash=50_000.0,
            positions={"600000.SH": 1000},
            position_values={"600000.SH": 10_000.0},
            daily_return=0.001,
            cumulative_return=0.001 * d,
        )
        for d in (2, 3, 4)
    ]
    trades = [
        TradeRecord(
            trade_id="T001",
            order_id="O001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.0,
            notional=10_000.0,
            fees=fee,
            trade_time=datetime(2026, 3, 2, 9, 31),
            trade_date=date(2026, 3, 2),
        ),
    ]
    return BacktestResult(
        strategy_name="DivergenceTest",
        start_date=date(2026, 3, 2),
        end_date=date(2026, 3, 4),
        initial_capital=100_000.0,
        final_nav=100_400.0,
        final_cash=50_000.0,
        final_positions={"600000.SH": 1000},
        daily_snapshots=snapshots,
        trades=trades,
        total_trades=1,
        total_fees=fee,
        exclusion_stats={},
    )


class TestDivergenceWiring:
    """price_lookup → execution_divergence"""

    def test_absent_by_default(self, result: BacktestResult) -> None:
        """不传 price_lookup 时不计算（保持原有行为）"""
        report = generate_report(result)
        assert report.execution_divergence is None

    def test_populated_with_price_lookup(self, result: BacktestResult) -> None:
        report = generate_report(
            result, price_lookup=lambda s, d: (10.0, 10.5),
        )

        ed = report.execution_divergence
        assert ed is not None
        assert ed["total_trades"] == 1
        # 次日高开 → 回测口径成交价更高
        assert ed["mean_price_diff_bps"] > 0

    def test_none_when_quotes_missing(self, result: BacktestResult) -> None:
        """行情全缺 → 不产出该节，主报告照常生成"""
        report = generate_report(result, price_lookup=lambda s, d: None)

        assert report.execution_divergence is None
        assert report.performance is not None

    def test_no_trades_skips_computation(self, result: BacktestResult) -> None:
        result.trades = []
        report = generate_report(result, price_lookup=lambda s, d: (10.0, 10.5))
        assert report.execution_divergence is None

    def test_lookup_exception_does_not_break_report(
        self, result: BacktestResult,
    ) -> None:
        """行情查询抛异常时降级跳过，不让主报告失败"""
        def boom(symbol: str, d: date):
            raise RuntimeError("行情服务不可用")

        report = generate_report(result, price_lookup=boom)

        assert report.execution_divergence is None
        assert report.performance is not None


class TestSummaryRendering:
    """summary() 输出"""

    def test_section_present_when_computed(self, result: BacktestResult) -> None:
        report = generate_report(result, price_lookup=lambda s, d: (10.0, 10.5))
        text = report.summary()

        assert "【执行口径差异】" in text
        assert "平均成交价差" in text

    def test_section_absent_otherwise(self, result: BacktestResult) -> None:
        assert "【执行口径差异】" not in generate_report(result).summary()
