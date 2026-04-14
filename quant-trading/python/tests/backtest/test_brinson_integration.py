"""Tests for Brinson attribution integration into BacktestReport.

Tests:
- _compute_brinson_from_snapshots bridge function
- generate_report with Brinson parameters
- BacktestReport.summary() with Brinson data
- Edge cases (empty snapshots, missing sectors)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from aipha.backtest.report import (
    BacktestReport,
    _compute_brinson_from_snapshots,
    generate_report,
)


# ============================================================================
# Helpers
# ============================================================================


@dataclass
class _FakeSnapshot:
    """Minimal DailySnapshot stub for testing."""

    trade_date: date
    nav: float
    cash: float
    positions: dict[str, int] = field(default_factory=dict)
    position_values: dict[str, float] = field(default_factory=dict)
    daily_return: float = 0.0
    cumulative_return: float = 0.0
    frozen_cash: float = 0.0


@dataclass
class _FakeResult:
    """Minimal BacktestResult stub for testing."""

    strategy_name: str = "test"
    start_date: date = date(2024, 1, 2)
    end_date: date = date(2024, 1, 5)
    initial_capital: float = 1_000_000.0
    final_nav: float = 1_010_000.0
    total_fees: object = None
    daily_snapshots: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    config: dict = field(default_factory=dict)
    exclusion_stats: dict = field(default_factory=dict)

    def __post_init__(self):
        from aipha.execution.fees import FeeBreakdown

        if self.total_fees is None:
            self.total_fees = FeeBreakdown(
                commission=100.0, stamp_tax=50.0, transfer_fee=10.0
            )


def _make_snapshots() -> list[_FakeSnapshot]:
    """Create 4-day snapshot series with 2 stocks in 2 sectors."""
    return [
        _FakeSnapshot(
            trade_date=date(2024, 1, 2),
            nav=1_000_000, cash=500_000,
            positions={"A": 200, "B": 100},
            position_values={"A": 300_000, "B": 200_000},
            daily_return=0.0, cumulative_return=0.0,
        ),
        _FakeSnapshot(
            trade_date=date(2024, 1, 3),
            nav=1_005_000, cash=500_000,
            positions={"A": 200, "B": 100},
            position_values={"A": 305_000, "B": 200_000},
            daily_return=0.005, cumulative_return=0.005,
        ),
        _FakeSnapshot(
            trade_date=date(2024, 1, 4),
            nav=1_008_000, cash=500_000,
            positions={"A": 200, "B": 100},
            position_values={"A": 306_000, "B": 202_000},
            daily_return=0.003, cumulative_return=0.008,
        ),
        _FakeSnapshot(
            trade_date=date(2024, 1, 5),
            nav=1_010_000, cash=500_000,
            positions={"A": 200, "B": 100},
            position_values={"A": 308_000, "B": 202_000},
            daily_return=0.002, cumulative_return=0.010,
        ),
    ]


SECTOR_MAP = {"A": "科技", "B": "金融"}
BENCH_WEIGHTS = {"科技": 0.5, "金融": 0.5}
BENCH_RETURNS = {"科技": 0.01, "金融": 0.005}


# ============================================================================
# _compute_brinson_from_snapshots 测试
# ============================================================================


class TestComputeBrinsonFromSnapshots:
    """桥接函数单元测试。"""

    def test_basic_computation(self):
        """正常 4 天数据 → 3 个归因期。"""
        snapshots = _make_snapshots()
        result = _compute_brinson_from_snapshots(
            snapshots, SECTOR_MAP, BENCH_WEIGHTS, BENCH_RETURNS,
        )
        assert result is not None
        assert len(result.periods) == 3
        # 累计效应都应是 float
        assert isinstance(result.cumulative_allocation, float)
        assert isinstance(result.cumulative_selection, float)
        assert isinstance(result.cumulative_interaction, float)

    def test_single_period(self):
        """2 天 → 1 个归因期。"""
        snapshots = _make_snapshots()[:2]
        result = _compute_brinson_from_snapshots(
            snapshots, SECTOR_MAP, BENCH_WEIGHTS, BENCH_RETURNS,
        )
        assert result is not None
        assert len(result.periods) == 1

    def test_empty_snapshots(self):
        """空快照 → None。"""
        result = _compute_brinson_from_snapshots(
            [], SECTOR_MAP, BENCH_WEIGHTS, BENCH_RETURNS,
        )
        assert result is None

    def test_single_snapshot(self):
        """仅 1 天 → None。"""
        result = _compute_brinson_from_snapshots(
            _make_snapshots()[:1], SECTOR_MAP, BENCH_WEIGHTS, BENCH_RETURNS,
        )
        assert result is None

    def test_unknown_sector_goes_to_other(self):
        """无行业映射的标的归入 '其他'。"""
        snapshots = _make_snapshots()[:2]
        # 只映射 A，B 无映射
        partial_map = {"A": "科技"}
        result = _compute_brinson_from_snapshots(
            snapshots, partial_map, BENCH_WEIGHTS, BENCH_RETURNS,
        )
        assert result is not None
        single = list(result.periods.values())[0]
        assert "其他" in single.allocation or "其他" in single.selection

    def test_active_return_decomposition(self):
        """总超额 = 配置 + 选择 + 交互（每期都严格相等）。"""
        snapshots = _make_snapshots()
        result = _compute_brinson_from_snapshots(
            snapshots, SECTOR_MAP, BENCH_WEIGHTS, BENCH_RETURNS,
        )
        for single in result.periods.values():
            expected = single.total_allocation + single.total_selection + single.total_interaction
            assert abs(single.total_active - expected) < 1e-10

    def test_to_dict(self):
        """to_dict() 产出可序列化字典。"""
        result = _compute_brinson_from_snapshots(
            _make_snapshots(), SECTOR_MAP, BENCH_WEIGHTS, BENCH_RETURNS,
        )
        d = result.to_dict()
        assert "num_periods" in d
        assert d["num_periods"] == 3
        assert "cumulative_allocation" in d


# ============================================================================
# generate_report with Brinson 测试
# ============================================================================


class TestGenerateReportBrinson:
    """generate_report 集成 Brinson 归因。"""

    def test_without_brinson_params(self):
        """不提供行业数据 → brinson_attribution = None。"""
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(result)
        assert report.brinson_attribution is None

    def test_with_brinson_params(self):
        """提供完整行业数据 → brinson_attribution 非 None。"""
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(
            result,
            sector_mapping=SECTOR_MAP,
            benchmark_sector_weights=BENCH_WEIGHTS,
            benchmark_sector_returns=BENCH_RETURNS,
        )
        assert report.brinson_attribution is not None
        assert len(report.brinson_attribution.periods) == 3

    def test_partial_brinson_params_no_crash(self):
        """部分提供行业数据 → brinson_attribution = None（跳过）。"""
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(
            result,
            sector_mapping=SECTOR_MAP,
            # 缺少 benchmark_sector_weights 和 benchmark_sector_returns
        )
        assert report.brinson_attribution is None


# ============================================================================
# BacktestReport.summary() with Brinson 测试
# ============================================================================


class TestBrinsonInSummary:
    """summary() 输出 Brinson 信息。"""

    def test_summary_includes_brinson(self):
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(
            result,
            sector_mapping=SECTOR_MAP,
            benchmark_sector_weights=BENCH_WEIGHTS,
            benchmark_sector_returns=BENCH_RETURNS,
        )
        text = report.summary()
        assert "Brinson" in text
        assert "配置效应" in text
        assert "选择效应" in text
        assert "交互效应" in text

    def test_summary_without_brinson(self):
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(result)
        text = report.summary()
        assert "Brinson" not in text
