"""Edge tests for backtest/benchmark.py — 边界与极端场景。

覆盖:
- 非重叠日期 → 空公共日期
- 常量净值（零波动率 + len>=2）
- 仅 2 天数据
- rolling window >= 数据长度
- generate_report(include_rolling=False)
- tracking_error ≈ 0 / 信息比率硬编码 0
- _compute_max_drawdown 空字典
- _compute_sharpe std=0
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from aipha.backtest.benchmark import BenchmarkComparison, compare_with_benchmark


def _make_nav(start: date, n: int, base: float = 1.0, daily_return: float = 0.001):
    """生成 n 天的 nav 字典。"""
    nav = {}
    v = base
    for i in range(n):
        d = start + timedelta(days=i)
        nav[d] = v
        v *= (1 + daily_return)
    return nav


class TestNonOverlappingDates:
    """策略与基准无公共日期。"""

    def test_no_common_dates(self) -> None:
        s_nav = {date(2024, 1, 1): 1.0, date(2024, 1, 2): 1.01}
        b_nav = {date(2024, 2, 1): 1.0, date(2024, 2, 2): 0.99}
        bc = BenchmarkComparison(s_nav, b_nav)
        assert len(bc.dates) == 0

    def test_metrics_all_zero_no_overlap(self) -> None:
        s_nav = {date(2024, 1, 1): 1.0}
        b_nav = {date(2024, 2, 1): 1.0}
        bc = BenchmarkComparison(s_nav, b_nav)
        m = bc.compute_metrics()
        assert m.strategy_return == 0
        assert m.alpha == 0

    def test_report_with_no_dates(self) -> None:
        s_nav = {date(2024, 1, 1): 1.0}
        b_nav = {date(2024, 2, 1): 1.0}
        bc = BenchmarkComparison(s_nav, b_nav)
        report = bc.generate_report()
        # analysis_period fallback to (today, today)
        assert report.analysis_period[0] == report.analysis_period[1]
        assert report.trading_days == 0


class TestConstantNav:
    """净值完全不变（零波动率）。"""

    def test_constant_nav_sharpe_zero(self) -> None:
        d1, d2, d3 = date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)
        nav = {d1: 1.0, d2: 1.0, d3: 1.0}
        bc = BenchmarkComparison(nav, nav)
        m = bc.compute_metrics()
        assert m.strategy_sharpe == 0.0
        assert m.benchmark_sharpe == 0.0

    def test_constant_nav_metrics_finite(self) -> None:
        """即使 std=0，所有 metrics 不含 inf/nan。"""
        import math
        d1, d2, d3 = date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3)
        nav = {d1: 1.0, d2: 1.0, d3: 1.0}
        bc = BenchmarkComparison(nav, nav)
        m = bc.compute_metrics()
        for field_name in [
            "strategy_return", "strategy_annual_return", "strategy_volatility",
            "alpha", "beta", "tracking_error", "information_ratio",
        ]:
            v = getattr(m, field_name)
            assert math.isfinite(v), f"{field_name} is {v}"


class TestTwoDayData:
    """仅 2 天数据（1 个收益率）。"""

    def test_two_day_metrics(self) -> None:
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        s_nav = {d1: 1.0, d2: 1.05}
        b_nav = {d1: 1.0, d2: 1.02}
        bc = BenchmarkComparison(s_nav, b_nav)
        m = bc.compute_metrics()
        assert m.strategy_return == pytest.approx(0.05)
        assert m.benchmark_return == pytest.approx(0.02)
        assert m.excess_return == pytest.approx(0.03)


class TestRollingWindowEdge:
    """滚动窗口 ≥ 数据长度。"""

    def test_window_equals_data_length(self) -> None:
        """window == len → 0 滚动点。"""
        nav = _make_nav(date(2024, 1, 1), 10)
        bc = BenchmarkComparison(nav, nav)
        rolling = bc.compute_rolling_metrics(window=10)
        # 10 dates → 9 returns → window=10 → range(10, 9) empty
        assert len(rolling.dates) == 0

    def test_window_larger_than_data(self) -> None:
        nav = _make_nav(date(2024, 1, 1), 5)
        bc = BenchmarkComparison(nav, nav)
        rolling = bc.compute_rolling_metrics(window=100)
        assert len(rolling.dates) == 0

    def test_window_one_less_than_data(self) -> None:
        """window = len-1 → 还没到 returns 长度，因为 returns = len-1。"""
        nav = _make_nav(date(2024, 1, 1), 11)
        bc = BenchmarkComparison(nav, nav)
        # 11 dates → 10 returns → window=10 → range(10, 10) = 0 points
        rolling = bc.compute_rolling_metrics(window=10)
        assert len(rolling.dates) == 0


class TestGenerateReportEdge:
    """generate_report 参数变体。"""

    def test_include_rolling_false(self) -> None:
        nav = _make_nav(date(2024, 1, 1), 100)
        bc = BenchmarkComparison(nav, nav)
        report = bc.generate_report(include_rolling=False)
        assert report.rolling_metrics is None

    def test_include_rolling_true_insufficient_data(self) -> None:
        nav = _make_nav(date(2024, 1, 1), 5)
        bc = BenchmarkComparison(nav, nav)
        report = bc.generate_report(include_rolling=True, rolling_window=60)
        # Not enough data → rolling_metrics is None
        assert report.rolling_metrics is None

    def test_report_summary_string(self) -> None:
        nav = _make_nav(date(2024, 1, 1), 10)
        bc = BenchmarkComparison(nav, nav)
        report = bc.generate_report(include_rolling=False)
        summary = report.summary()
        assert "基准对比报告" in summary
        assert "Strategy" in summary


class TestTrackingErrorEdge:
    """跟踪误差与信息比率边界。"""

    def test_identical_nav_tracking_error_zero(self) -> None:
        nav = _make_nav(date(2024, 1, 1), 30)
        bc = BenchmarkComparison(nav, nav)
        m = bc.compute_metrics()
        assert m.tracking_error == pytest.approx(0.0)
        assert m.information_ratio == 0.0

    def test_information_ratio_capped_at_zero_when_te_zero(self) -> None:
        """tracking_error=0, excess≠0 → information_ratio=0（硬编码保护）。"""
        # 同一 nav → tracking_error=0, excess_return=0
        nav = _make_nav(date(2024, 1, 1), 30)
        bc = BenchmarkComparison(nav, nav)
        m = bc.compute_metrics()
        assert m.information_ratio == 0.0


class TestComputeSharpeSelf:
    """_compute_sharpe 内部边界。"""

    def test_single_return(self) -> None:
        import numpy as np
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        bc = BenchmarkComparison({d1: 1.0, d2: 1.01}, {d1: 1.0, d2: 1.01})
        result = bc._compute_sharpe(np.array([0.01]))
        assert result == 0.0  # len < 2


class TestMaxDrawdownEdge:
    """_compute_max_drawdown 边界。"""

    def test_empty_nav(self) -> None:
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        bc = BenchmarkComparison({d1: 1.0, d2: 1.01}, {d1: 1.0, d2: 1.01})
        assert bc._compute_max_drawdown({}) == 0.0

    def test_monotonic_increase_no_drawdown(self) -> None:
        nav = _make_nav(date(2024, 1, 1), 30, daily_return=0.01)
        d1, d2 = date(2024, 1, 1), date(2024, 1, 2)
        bc = BenchmarkComparison({d1: 1.0, d2: 1.01}, {d1: 1.0, d2: 1.01})
        dd = bc._compute_max_drawdown(nav)
        assert dd == pytest.approx(0.0)


class TestCompareWithBenchmark:
    """便捷函数 compare_with_benchmark。"""

    def test_basic(self) -> None:
        nav = _make_nav(date(2024, 1, 1), 30)
        report = compare_with_benchmark(nav, nav, "S1", "B1")
        assert report.strategy_name == "S1"
        assert report.benchmark_name == "B1"
        assert report.trading_days == 30
