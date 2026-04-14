"""Tests for factor exposure integration into BacktestReport.

Tests:
- compute_factor_exposure_from_snapshots bridge function
- generate_report with factor_scores_by_date
- BacktestReport.summary() with factor exposure data
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from aipha.backtest.report import (
    compute_factor_exposure_from_snapshots,
    generate_report,
)


# ============================================================================
# Helpers
# ============================================================================


@dataclass
class _FakeSnapshot:
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
    strategy_name: str = "test"
    start_date: date = date(2024, 1, 2)
    end_date: date = date(2024, 1, 4)
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
            self.total_fees = FeeBreakdown(commission=100, stamp_tax=50, transfer_fee=10)


def _make_snapshots():
    return [
        _FakeSnapshot(
            trade_date=date(2024, 1, 2), nav=1_000_000, cash=400_000,
            positions={"A": 200, "B": 100},
            position_values={"A": 400_000, "B": 200_000},
        ),
        _FakeSnapshot(
            trade_date=date(2024, 1, 3), nav=1_005_000, cash=400_000,
            positions={"A": 200, "B": 100},
            position_values={"A": 405_000, "B": 200_000},
            daily_return=0.005, cumulative_return=0.005,
        ),
        _FakeSnapshot(
            trade_date=date(2024, 1, 4), nav=1_010_000, cash=400_000,
            positions={"A": 200, "B": 100},
            position_values={"A": 406_000, "B": 204_000},
            daily_return=0.005, cumulative_return=0.010,
        ),
    ]


def _make_factor_scores():
    """返回 factor_scores_by_date: {date: {factor: {symbol: score}}}"""
    return {
        date(2024, 1, 2): {
            "size": {"A": 0.8, "B": 0.3},
            "value": {"A": 0.4, "B": 0.7},
        },
        date(2024, 1, 3): {
            "size": {"A": 0.85, "B": 0.28},
            "value": {"A": 0.38, "B": 0.72},
        },
        date(2024, 1, 4): {
            "size": {"A": 0.82, "B": 0.31},
            "value": {"A": 0.42, "B": 0.68},
        },
    }


# ============================================================================
# compute_factor_exposure_from_snapshots 测试
# ============================================================================


class TestComputeFactorExposure:
    """桥接函数测试。"""

    def test_basic_computation(self):
        result = compute_factor_exposure_from_snapshots(
            _make_snapshots(), _make_factor_scores(),
        )
        assert result is not None
        assert "summary" in result
        assert "snapshots" in result
        assert len(result["summary"]) == 2  # size, value

    def test_auto_detect_factor_names(self):
        result = compute_factor_exposure_from_snapshots(
            _make_snapshots(), _make_factor_scores(),
        )
        factor_names = [s["factor"] for s in result["summary"]]
        assert "size" in factor_names
        assert "value" in factor_names

    def test_explicit_factor_names(self):
        result = compute_factor_exposure_from_snapshots(
            _make_snapshots(), _make_factor_scores(),
            factor_names=["size"],
        )
        assert len(result["summary"]) == 1
        assert result["summary"][0]["factor"] == "size"

    def test_empty_snapshots_returns_none(self):
        assert compute_factor_exposure_from_snapshots([], _make_factor_scores()) is None

    def test_empty_scores_returns_none(self):
        assert compute_factor_exposure_from_snapshots(_make_snapshots(), {}) is None

    def test_no_matching_dates_returns_none(self):
        scores = {date(2025, 6, 1): {"size": {"X": 1.0}}}
        result = compute_factor_exposure_from_snapshots(_make_snapshots(), scores)
        assert result is None

    def test_summary_has_statistics(self):
        result = compute_factor_exposure_from_snapshots(
            _make_snapshots(), _make_factor_scores(),
        )
        for s in result["summary"]:
            assert "mean" in s
            assert "std" in s
            assert "latest" in s

    def test_exposure_values_reasonable(self):
        """暴露 = 加权平均因子分数，应在 [0, 1] 范围内。"""
        result = compute_factor_exposure_from_snapshots(
            _make_snapshots(), _make_factor_scores(),
        )
        for s in result["summary"]:
            assert 0.0 <= s["mean"] <= 1.0


# ============================================================================
# generate_report with factor exposure 测试
# ============================================================================


class TestGenerateReportFactorExposure:

    def test_without_factor_scores(self):
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(result)
        assert report.factor_exposure is None

    def test_with_factor_scores(self):
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(result, factor_scores_by_date=_make_factor_scores())
        assert report.factor_exposure is not None
        assert "summary" in report.factor_exposure


# ============================================================================
# summary() with factor exposure 测试
# ============================================================================


class TestFactorExposureInSummary:

    def test_summary_includes_factor_exposure(self):
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(result, factor_scores_by_date=_make_factor_scores())
        text = report.summary()
        assert "因子暴露" in text
        assert "size" in text
        assert "value" in text

    def test_summary_without_factor_exposure(self):
        result = _FakeResult(daily_snapshots=_make_snapshots())
        report = generate_report(result)
        text = report.summary()
        assert "因子暴露" not in text
