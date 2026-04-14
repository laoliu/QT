"""
基准相对指标与 Sortino 比率测试

覆盖：
1. calculate_metrics 含 sortino_ratio
2. calculate_benchmark_relative_metrics（完整）
3. PerformanceMetrics.to_dict 含基准字段
4. 边界条件（短序列、相同 NAV、零波动率等）
"""

import math
from datetime import date, timedelta

import pytest

from aipha.reporting.report import (
    PerformanceMetrics,
    calculate_benchmark_relative_metrics,
    calculate_metrics,
)


# ============================================================================
# Fixtures
# ============================================================================

def _make_nav(start_val: float, daily_returns: list[float],
              start_date: date = date(2025, 1, 6)) -> list[tuple[date, float]]:
    """辅助：从日收益率列表构造 NAV 序列"""
    navs = [(start_date, start_val)]
    val = start_val
    for i, r in enumerate(daily_returns):
        val *= (1 + r)
        navs.append((start_date + timedelta(days=i + 1), val))
    return navs


@pytest.fixture
def strategy_nav_up() -> list[tuple[date, float]]:
    """策略 NAV：整体上涨，有波动，250 个交易日"""
    import random
    rng = random.Random(42)
    rets = [0.001 + rng.gauss(0, 0.005) for _ in range(250)]
    return _make_nav(1_000_000.0, rets)


@pytest.fixture
def benchmark_nav_flat() -> list[tuple[date, float]]:
    """基准 NAV：完全不动"""
    rets = [0.0] * 250
    return _make_nav(1_000_000.0, rets)


@pytest.fixture
def benchmark_nav_up_slower() -> list[tuple[date, float]]:
    """基准 NAV：上涨但比策略慢，有波动"""
    import random
    rng = random.Random(123)
    rets = [0.0005 + rng.gauss(0, 0.004) for _ in range(250)]
    return _make_nav(1_000_000.0, rets)


@pytest.fixture
def strategy_nav_volatile() -> list[tuple[date, float]]:
    """策略 NAV：有正有负，净涨"""
    rets = [0.01, -0.005, 0.008, -0.003, 0.006] * 50  # 250 天
    return _make_nav(1_000_000.0, rets)


# ============================================================================
# Sortino Ratio Tests
# ============================================================================


class TestSortinoRatio:
    """测试 calculate_metrics 返回的 sortino_ratio"""

    def test_sortino_has_value(self, strategy_nav_up):
        """带波动的上涨 NAV → sortino 为正"""
        metrics = calculate_metrics(strategy_nav_up)
        assert metrics.sortino_ratio > 0  # 整体涨，sortino 应为正

    def test_sortino_mixed_returns(self, strategy_nav_volatile):
        """有正有负收益 → sortino 有值"""
        metrics = calculate_metrics(strategy_nav_volatile)
        assert metrics.sortino_ratio > 0  # 净涨，sortino 应为正
        # sortino 应大于 sharpe（因为只算下行波动）
        assert metrics.sortino_ratio >= metrics.sharpe_ratio

    def test_sortino_negative_returns(self):
        """全负收益 → sortino 为负"""
        rets = [-0.001] * 100
        nav = _make_nav(1_000_000.0, rets)
        metrics = calculate_metrics(nav)
        assert metrics.sortino_ratio < 0

    def test_sortino_single_day(self):
        """单日 → sortino = 0"""
        nav = [(date(2025, 1, 6), 1_000_000.0)]
        metrics = calculate_metrics(nav)
        assert metrics.sortino_ratio == 0.0

    def test_sortino_in_to_dict(self, strategy_nav_volatile):
        """sortino_ratio 应出现在 to_dict 中"""
        metrics = calculate_metrics(strategy_nav_volatile)
        d = metrics.to_dict()
        assert "sortino_ratio" in d
        assert isinstance(d["sortino_ratio"], float)


# ============================================================================
# Benchmark Relative Metrics Tests
# ============================================================================


class TestBenchmarkRelativeMetrics:
    """测试 calculate_benchmark_relative_metrics"""

    def test_excess_return_vs_flat_benchmark(
        self, strategy_nav_up, benchmark_nav_flat
    ):
        """策略上涨 vs 基准不动 → 超额收益 = 策略收益"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, benchmark_nav_flat
        )
        assert metrics.excess_return is not None
        assert metrics.excess_return > 0.2  # ~28% 累计收益减 0%

    def test_excess_return_vs_slower_benchmark(
        self, strategy_nav_up, benchmark_nav_up_slower
    ):
        """策略涨 vs 基准涨更慢 → 超额收益为正"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, benchmark_nav_up_slower
        )
        assert metrics.excess_return is not None
        assert metrics.excess_return > 0

    def test_alpha_beta_vs_slower(self, strategy_nav_up, benchmark_nav_up_slower):
        """策略涨更快 → alpha 应为正"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, benchmark_nav_up_slower
        )
        assert metrics.alpha is not None
        assert metrics.beta is not None
        assert metrics.alpha > 0  # 策略跑赢基准，alpha 为正

    def test_beta_correlated(self):
        """策略 = 2x 基准 → beta ≈ 2"""
        base_rets = [0.001, -0.002, 0.003, -0.001, 0.002] * 50
        strat_rets = [2 * r for r in base_rets]  # 2x 杠杆
        strat_nav = _make_nav(1_000_000.0, strat_rets)
        bench_nav = _make_nav(1_000_000.0, base_rets)
        metrics = calculate_benchmark_relative_metrics(strat_nav, bench_nav)
        assert metrics.beta is not None
        assert abs(metrics.beta - 2.0) < 0.05  # beta ≈ 2

    def test_tracking_error_identical(self, strategy_nav_up):
        """策略 = 基准 → TE ≈ 0"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, strategy_nav_up
        )
        assert metrics.tracking_error is not None
        assert abs(metrics.tracking_error) < 1e-10

    def test_tracking_error_positive(
        self, strategy_nav_volatile, benchmark_nav_flat
    ):
        """有差异 → TE > 0"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_volatile, benchmark_nav_flat
        )
        assert metrics.tracking_error is not None
        assert metrics.tracking_error > 0

    def test_information_ratio_positive(
        self, strategy_nav_volatile, benchmark_nav_up_slower
    ):
        """策略跑赢基准（有波动）→ IR > 0"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_volatile, benchmark_nav_up_slower
        )
        assert metrics.information_ratio is not None
        assert metrics.information_ratio > 0

    def test_information_ratio_identical(self, strategy_nav_up):
        """策略 = 基准 → IR = 0"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, strategy_nav_up
        )
        assert metrics.information_ratio is not None
        assert abs(metrics.information_ratio) < 1e-10

    def test_win_rate_vs_benchmark(
        self, strategy_nav_up, benchmark_nav_flat
    ):
        """策略整体跑赢基准 → win_rate > 0.5"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, benchmark_nav_flat
        )
        assert metrics.win_rate_vs_benchmark is not None
        assert metrics.win_rate_vs_benchmark > 0.5  # 多数日跑赢

    def test_excess_annual_return(
        self, strategy_nav_up, benchmark_nav_up_slower
    ):
        """年化超额应为正"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, benchmark_nav_up_slower
        )
        assert metrics.excess_annual_return is not None
        assert metrics.excess_annual_return > 0

    def test_to_dict_has_benchmark_fields(
        self, strategy_nav_up, benchmark_nav_flat
    ):
        """to_dict 应包含所有基准相对字段"""
        metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, benchmark_nav_flat
        )
        d = metrics.to_dict()
        for field in [
            "excess_return", "excess_annual_return", "alpha",
            "beta", "tracking_error", "information_ratio",
            "win_rate_vs_benchmark",
        ]:
            assert field in d, f"Missing {field} in to_dict()"
            assert isinstance(d[field], float), f"{field} should be float"

    def test_absolute_metrics_preserved(
        self, strategy_nav_up, benchmark_nav_flat
    ):
        """基准相对指标不应影响绝对指标"""
        abs_metrics = calculate_metrics(strategy_nav_up)
        rel_metrics = calculate_benchmark_relative_metrics(
            strategy_nav_up, benchmark_nav_flat
        )
        assert abs(abs_metrics.total_return - rel_metrics.total_return) < 1e-10
        assert abs(abs_metrics.sharpe_ratio - rel_metrics.sharpe_ratio) < 1e-10
        assert abs(abs_metrics.max_drawdown - rel_metrics.max_drawdown) < 1e-10
        assert abs(abs_metrics.sortino_ratio - rel_metrics.sortino_ratio) < 1e-10


# ============================================================================
# Edge Cases
# ============================================================================


class TestBenchmarkEdgeCases:
    """边界条件测试"""

    def test_short_series_returns_base(self):
        """只有 1 天共同日期 → 退化为绝对指标"""
        strat = [(date(2025, 1, 6), 1_000_000.0)]
        bench = [(date(2025, 1, 6), 1_000_000.0)]
        metrics = calculate_benchmark_relative_metrics(strat, bench)
        assert metrics.excess_return is None  # 无法计算

    def test_no_common_dates(self):
        """日期不重叠 → 退化"""
        strat = [(date(2025, 1, 6), 100.0), (date(2025, 1, 7), 101.0)]
        bench = [(date(2025, 2, 6), 100.0), (date(2025, 2, 7), 101.0)]
        metrics = calculate_benchmark_relative_metrics(strat, bench)
        assert metrics.excess_return is None

    def test_partial_overlap(self):
        """部分日期重叠 → 只用共同日期"""
        strat = [
            (date(2025, 1, 6), 100.0),
            (date(2025, 1, 7), 102.0),
            (date(2025, 1, 8), 104.0),
        ]
        bench = [
            (date(2025, 1, 7), 100.0),
            (date(2025, 1, 8), 101.0),
            (date(2025, 1, 9), 102.0),
        ]
        metrics = calculate_benchmark_relative_metrics(strat, bench)
        assert metrics.excess_return is not None

    def test_zero_volatility_benchmark(self):
        """基准零波动 → beta 应为 0 或 NaN 安全"""
        strat = _make_nav(100.0, [0.01, -0.01, 0.02])
        bench = _make_nav(100.0, [0.0, 0.0, 0.0])
        metrics = calculate_benchmark_relative_metrics(strat, bench)
        assert metrics.beta is not None
        assert math.isfinite(metrics.beta)

    def test_to_dict_without_benchmark(self):
        """无基准字段时 to_dict 不含相关 key"""
        metrics = PerformanceMetrics(
            total_return=0.1,
            annual_return=0.1,
            max_drawdown=0.05,
            sharpe_ratio=1.5,
            calmar_ratio=2.0,
            volatility=0.1,
        )
        d = metrics.to_dict()
        assert "excess_return" not in d
        assert "alpha" not in d
        assert "beta" not in d
