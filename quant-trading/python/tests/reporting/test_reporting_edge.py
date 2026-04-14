"""Reporting 模块边界测试 — Phase 37

覆盖 report.py / archiver.py / cost_attribution.py / yearly_attribution.py / alpha_decay.py
中已识别的 10 个未覆盖分支。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pytest

from aipha.reporting.report import (
    BacktestReport,
    PerformanceMetrics,
    calculate_benchmark_relative_metrics,
    calculate_metrics,
)
from aipha.reporting.archiver import (
    ExperimentArchiver,
    _json_serial,
    _hash_config,
)
from aipha.reporting.cost_attribution import (
    CostAttributionReport,
    CostComponent,
    TradeCostRecord,
    build_cost_attribution,
)
from aipha.reporting.yearly_attribution import (
    YearlyMetrics,
    yearly_breakdown,
    _calc_max_drawdown,
    format_yearly_report,
)
from aipha.reporting.alpha_decay import (
    analyze_alpha_decay,
    analyze_by_regime,
    _simple_ols,
    AlphaDecayResult,
    RegimeMetrics,
)


# ============================================================
# 1. calculate_metrics: calmar = inf (monotonic NAV, max_dd=0)
# ============================================================
class TestCalmarInfinite:
    def test_monotonic_increasing_nav(self):
        """单调递增 NAV → max_drawdown=0 且 annual_return>0 → calmar=inf"""
        navs = [(date(2023, 1, i + 2), 100.0 + i) for i in range(10)]
        m = calculate_metrics(navs)
        assert m.max_drawdown == 0.0
        assert m.annual_return > 0
        assert m.calmar_ratio == float("inf")

    def test_flat_nav_calmar_zero(self):
        """NAV 不变 → max_drawdown=0 且 annual_return=0 → calmar=0"""
        navs = [(date(2023, 1, i + 2), 100.0) for i in range(5)]
        m = calculate_metrics(navs)
        assert m.max_drawdown == 0.0
        assert m.calmar_ratio == 0.0


# ============================================================
# 2. calculate_metrics: 2-point NAV → exactly 1 daily return → vol=0, sharpe=0
# ============================================================
class TestTwoPointNav:
    def test_two_points(self):
        """只有 2 个 NAV 点 → 只有 1 个日收益率 → len(daily_returns)<2"""
        navs = [(date(2023, 1, 3), 100.0), (date(2023, 1, 4), 102.0)]
        m = calculate_metrics(navs)
        assert m.volatility == 0.0
        assert m.sharpe_ratio == 0.0
        assert m.total_return == pytest.approx(0.02)

    def test_single_point_raises(self):
        """单点 NAV → 应正常返回（total_return=0, annual_return=0）"""
        navs = [(date(2023, 1, 3), 100.0)]
        m = calculate_metrics(navs)
        assert m.total_return == 0.0
        assert m.annual_return == 0.0


# ============================================================
# 3. calculate_benchmark_relative_metrics: var_b ≈ 0 → beta=0, alpha=0
# ============================================================
class TestBenchmarkVarZero:
    def test_constant_benchmark(self):
        """基准 NAV 恒定 → 所有 bench_rets=0 → var_b=0 → beta=alpha=0"""
        strat = [(date(2023, 1, i + 2), 100 + i * 0.5) for i in range(10)]
        bench = [(date(2023, 1, i + 2), 1000.0) for i in range(10)]
        m = calculate_benchmark_relative_metrics(strat, bench)
        assert m.beta == 0.0
        assert m.alpha == 0.0

    def test_less_than_2_common_dates(self):
        """公共日期 < 2 → 只返回绝对指标，无基准相对指标"""
        strat = [(date(2023, 1, 3), 100.0)]
        bench = [(date(2023, 1, 3), 1000.0)]
        m = calculate_benchmark_relative_metrics(strat, bench)
        assert m.beta is None  # 没有基准相对指标
        assert m.alpha is None


# ============================================================
# 4. archiver._json_serial: 各分支
# ============================================================
class TestJsonSerial:
    def test_date_serialization(self):
        assert _json_serial(date(2023, 6, 15)) == "2023-06-15"

    def test_datetime_serialization(self):
        dt = datetime(2023, 6, 15, 10, 30, 0)
        assert _json_serial(dt) == "2023-06-15T10:30:00"

    def test_to_dict_method(self):
        """有 to_dict 方法的对象"""
        class Foo:
            def to_dict(self):
                return {"x": 1}
        assert _json_serial(Foo()) == {"x": 1}

    def test_dict_attribute(self):
        """没有 to_dict 但有 __dict__"""
        class Bar:
            def __init__(self):
                self.y = 2
        result = _json_serial(Bar())
        assert result == {"y": 2}

    def test_non_serializable_raises(self):
        with pytest.raises(TypeError, match="not serializable"):
            _json_serial(set([1, 2, 3]))


# ============================================================
# 5. archiver.load_experiment: FileNotFoundError
# ============================================================
class TestArchiverLoadNotFound:
    def test_missing_experiment(self, tmp_path):
        archiver = ExperimentArchiver(base_dir=str(tmp_path / "archive"))
        with pytest.raises(FileNotFoundError, match="实验不存在"):
            archiver.load_experiment("nonexist_strategy", "20230101_000000")


# ============================================================
# 6. archiver._extract_metrics: empty snapshots → {}
# ============================================================
class TestArchiverEmptySnapshots:
    def test_extract_metrics_empty(self, tmp_path):
        archiver = ExperimentArchiver(base_dir=str(tmp_path / "results"))
        # 构造一个 mock result 无快照
        result = _MockResult(daily_snapshots=[], trades=[])
        metrics = archiver._extract_metrics(result)
        assert metrics == {}


# ============================================================
# 7. archiver._save_nav_series/_save_trades: empty → skip
# ============================================================
class TestArchiverSkipEmpty:
    def test_save_nav_empty(self, tmp_path):
        archiver = ExperimentArchiver(base_dir=str(tmp_path / "results"))
        csv_path = tmp_path / "nav.csv"
        result = _MockResult(daily_snapshots=[], trades=[])
        archiver._save_nav_series(csv_path, result)
        assert not csv_path.exists()

    def test_save_trades_empty(self, tmp_path):
        archiver = ExperimentArchiver(base_dir=str(tmp_path / "results"))
        csv_path = tmp_path / "trades.csv"
        result = _MockResult(daily_snapshots=[], trades=[])
        archiver._save_trades(csv_path, result)
        assert not csv_path.exists()


# ============================================================
# 8. cost_attribution: trades present, final_nav=None → cost_drag=0
# ============================================================
class TestCostDragNone:
    def test_final_nav_none(self):
        trades = [
            TradeCostRecord(
                trade_date=date(2023, 1, 3),
                symbol="000001.SZ",
                side="BUY",
                qty=100,
                price=10.0,
                notional=1000.0,
                commission=5.0,
                stamp_tax=0.0,
                transfer_fee=0.02,
            )
        ]
        report = build_cost_attribution(trades, final_nav=None, total_calendar_days=365)
        assert report.cost_drag_annualized == 0.0
        assert report.total.trade_count == 1

    def test_total_calendar_days_none(self):
        trades = [
            TradeCostRecord(
                trade_date=date(2023, 1, 3),
                symbol="000001.SZ",
                side="SELL",
                qty=100,
                price=10.0,
                notional=1000.0,
                commission=5.0,
                stamp_tax=1.0,
                transfer_fee=0.02,
            )
        ]
        report = build_cost_attribution(
            trades, final_nav=1_050_000.0, total_calendar_days=None
        )
        assert report.cost_drag_annualized == 0.0

    def test_final_nav_zero(self):
        """final_nav=0 → cost_drag=0 (0 > 0 is False)"""
        trades = [
            TradeCostRecord(
                trade_date=date(2023, 1, 3),
                symbol="000001.SZ",
                side="BUY",
                qty=100,
                price=10.0,
                notional=1000.0,
                commission=5.0,
                stamp_tax=0.0,
                transfer_fee=0.02,
            )
        ]
        report = build_cost_attribution(
            trades, final_nav=0.0, total_calendar_days=365
        )
        assert report.cost_drag_annualized == 0.0


# ============================================================
# 9. yearly_breakdown: start_nav=0 → ZeroDivision defense
# ============================================================
class TestYearlyStartNavZero:
    def test_start_nav_zero(self):
        """某年第一天 NAV=0 → total_return=0 (defense branch)"""
        navs = [
            (date(2023, 1, 3), 0.0),
            (date(2023, 1, 4), 10.0),
            (date(2023, 1, 5), 12.0),
        ]
        result = yearly_breakdown(navs)
        assert len(result) == 1
        assert result[0].total_return == 0.0  # defense: start_nav == 0

    def test_prev_nav_zero_cross_year(self):
        """跨年 prev_nav=0 在 daily_returns 中会被跳过"""
        navs = [
            (date(2023, 1, 3), 0.0),
            (date(2023, 1, 4), 0.0),
            (date(2024, 1, 2), 10.0),
            (date(2024, 1, 3), 12.0),
        ]
        result = yearly_breakdown(navs)
        assert len(result) == 2


# ============================================================
# 10. _calc_max_drawdown edge cases
# ============================================================
class TestCalcMaxDrawdown:
    def test_single_point(self):
        assert _calc_max_drawdown([100.0]) == 0.0

    def test_empty_list(self):
        assert _calc_max_drawdown([]) == 0.0

    def test_all_zero(self):
        """全零 → peak=0 → dd=0 (defense: peak > 0)"""
        assert _calc_max_drawdown([0.0, 0.0, 0.0]) == 0.0


# ============================================================
# 11. alpha_decay: _simple_ols edge cases
# ============================================================
class TestSimpleOlsEdge:
    def test_single_point(self):
        slope, intercept, r2 = _simple_ols([1.0], [2.0])
        assert slope == 0.0
        assert r2 == 0.0

    def test_perfect_fit(self):
        """完美线性 → r²=1.0"""
        x = [0.0, 1.0, 2.0, 3.0]
        y = [1.0, 3.0, 5.0, 7.0]  # y = 1 + 2x
        slope, intercept, r2 = _simple_ols(x, y)
        assert slope == pytest.approx(2.0)
        assert intercept == pytest.approx(1.0)
        assert r2 == pytest.approx(1.0)

    def test_constant_x(self):
        """x 全相同 → denom ≈ 0 → slope=0"""
        x = [5.0, 5.0, 5.0]
        y = [1.0, 2.0, 3.0]
        slope, intercept, r2 = _simple_ols(x, y)
        assert slope == 0.0


# ============================================================
# 12. alpha_decay: insufficient data → None
# ============================================================
class TestAlphaDecayInsufficient:
    def test_too_short_strategy(self):
        strat = [(date(2023, 1, i + 2), 100 + i) for i in range(5)]
        bench = [(date(2023, 1, i + 2), 1000 + i) for i in range(5)]
        result = analyze_alpha_decay(strat, bench, window_days=60)
        assert result is None

    def test_too_few_rolling_windows(self):
        """数据刚好够 1 个窗口 → rolling_alphas < 2 → None"""
        n = 62  # window_days=60, 需要 61 个日期产出 60 个 excess_returns → 1 个窗口
        strat = [(date(2023, 1, 1) + __import__('datetime').timedelta(days=i), 100 + i * 0.01) for i in range(n)]
        bench = [(date(2023, 1, 1) + __import__('datetime').timedelta(days=i), 1000 + i * 0.005) for i in range(n)]
        result = analyze_alpha_decay(strat, bench, window_days=60)
        assert result is None


# ============================================================
# 13. analyze_by_regime: edge cases
# ============================================================
class TestRegimeEdge:
    def test_empty_navs(self):
        assert analyze_by_regime([], []) == []

    def test_single_common_date(self):
        """公共日期 < 2 → 空列表"""
        strat = [(date(2023, 1, 3), 100.0)]
        regimes = [(date(2023, 1, 3), "BULL")]
        assert analyze_by_regime(strat, regimes) == []

    def test_no_common_dates(self):
        strat = [(date(2023, 1, 3), 100.0)]
        regimes = [(date(2023, 1, 4), "BULL")]
        assert analyze_by_regime(strat, regimes) == []


# ============================================================
# 14. _hash_config determinism
# ============================================================
class TestHashConfig:
    def test_same_config_same_hash(self):
        c1 = {"a": 1, "b": 2}
        c2 = {"b": 2, "a": 1}
        assert _hash_config(c1) == _hash_config(c2)

    def test_different_config_different_hash(self):
        assert _hash_config({"a": 1}) != _hash_config({"a": 2})


# ============================================================
# Helpers
# ============================================================
@dataclass
class _MockSnapshot:
    trade_date: date
    nav: float
    cash: float = 0.0
    daily_return: float = 0.0
    cumulative_return: float = 0.0


@dataclass
class _MockFees:
    total: float = 0.0
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0


@dataclass
class _MockResult:
    daily_snapshots: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    start_date: date = date(2023, 1, 3)
    end_date: date = date(2023, 12, 29)
    initial_capital: float = 1_000_000.0
    final_nav: float = 1_000_000.0
    total_trades: int = 0
    total_fees: _MockFees = field(default_factory=_MockFees)
    strategy_name: str = "test_strategy"
    config: dict = field(default_factory=lambda: {"fill_model_name": "test"})
