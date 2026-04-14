"""Tests for FactorAnalyzer (C-3).

覆盖要求（copilot-instructions §6.2 ≥10 tests）:
  T01: IC = 1.0 for perfectly correlated data
  T02: IC = -1.0 for reverse-correlated data
  T03: IC ≈ 0 for uncorrelated data
  T04: IC tolerates NaN values (filtered out)
  T05: ICIR = mean/std of IC series
  T06: ICIR = NaN for insufficient data
  T07: compute_quantile_returns monotonicity
  T08: compute_quantile_returns insufficient data returns {}
  T09: compute_turnover = 0 for identical factor
  T10: compute_turnover = 1 for completely different top set
  T11: FactorAnalyzer multi-period report correctness
  T12: FactorAnalyzer.reset clears state
  T13: FactorReport.is_significant threshold check
"""
from __future__ import annotations

import math

import pytest

from aipha.factors.factor_analyzer import (
    FactorAnalyzer,
    FactorReport,
    compute_ic,
    compute_icir,
    compute_quantile_returns,
    compute_turnover,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_factor(n: int, ascending: bool = True) -> dict[str, float]:
    """生成 n 个 symbol 的因子值（升序 or 降序）。"""
    vals = list(range(n))
    if not ascending:
        vals = list(reversed(vals))
    return {f"S{i:02d}": float(v) for i, v in enumerate(vals)}


def _make_returns(n: int, ascending: bool = True) -> dict[str, float]:
    """生成 n 个 symbol 的收益率（升序 or 降序）。"""
    vals = [i * 0.01 for i in range(n)]
    if not ascending:
        vals = list(reversed(vals))
    return {f"S{i:02d}": v for i, v in enumerate(vals)}


# ---------------------------------------------------------------------------
# T01-T04: compute_ic
# ---------------------------------------------------------------------------

class TestComputeIC:

    def test_T01_perfect_positive_correlation(self):
        """完全正相关 → IC = 1.0"""
        f = _make_factor(10, ascending=True)
        r = _make_returns(10, ascending=True)
        ic = compute_ic(f, r, method="rank")
        assert abs(ic - 1.0) < 1e-9

    def test_T02_perfect_negative_correlation(self):
        """完全负相关 → IC = -1.0"""
        f = _make_factor(10, ascending=True)
        r = _make_returns(10, ascending=False)
        ic = compute_ic(f, r, method="rank")
        assert abs(ic + 1.0) < 1e-9

    def test_T03_uncorrelated_random_small(self):
        """无关数据 IC 应接近 0（不一定为 0，但不应为 ±1）。"""
        # 因子升序，收益与因子交叉排列
        f = {f"S{i}": float(i) for i in range(10)}
        r = {f"S{i}": float(i % 3) for i in range(10)}
        ic = compute_ic(f, r, method="rank")
        assert math.isfinite(ic)
        assert abs(ic) < 0.99

    def test_T04_nan_tolerance_filtered_out(self):
        """含 NaN 的 symbol 被过滤，不影响其他 symbol 的 IC 计算。"""
        f = {f"S{i}": float(i) for i in range(10)}
        r = {f"S{i}": float(i) * 0.01 for i in range(10)}
        # 注入 NaN
        f["S00"] = float("nan")
        r["S05"] = float("nan")
        ic = compute_ic(f, r, method="rank")
        # 剩余 8 个有效 symbol 仍应完全正相关
        assert math.isfinite(ic)
        assert ic > 0.9

    def test_T04b_too_few_symbols_returns_nan(self):
        """有效 symbol < 5 时应返回 NaN。"""
        f = {"A": 1.0, "B": 2.0}
        r = {"A": 0.01, "B": 0.02}
        ic = compute_ic(f, r)
        assert math.isnan(ic)

    def test_T04c_pearson_method(self):
        """Pearson IC 对完全线性相关数据应返回1.0。"""
        f = {f"S{i}": float(i) for i in range(10)}
        r = {f"S{i}": float(i) * 2.5 + 1.0 for i in range(10)}  # 完全线性
        ic = compute_ic(f, r, method="pearson")
        assert abs(ic - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# T05-T06: compute_icir
# ---------------------------------------------------------------------------

class TestComputeICIR:

    def test_T05_icir_formula(self):
        """ICIR = mean/std, 验证精度。"""
        ic_series = [0.04, 0.06, 0.05, 0.03, 0.07]
        import statistics
        expected = statistics.mean(ic_series) / statistics.stdev(ic_series)
        assert abs(compute_icir(ic_series) - expected) < 1e-9

    def test_T06_insufficient_data_returns_nan(self):
        """只有 1 期 IC 时，std=0 应返回 NaN。"""
        assert math.isnan(compute_icir([0.05]))

    def test_T06b_all_nan_returns_nan(self):
        """全为 NaN 时返回 NaN。"""
        assert math.isnan(compute_icir([float("nan"), float("nan")]))

    def test_T06c_positive_icir(self):
        """正 IC 系列应产生正 ICIR。"""
        ic_series = [0.04, 0.05, 0.06, 0.03, 0.04, 0.05]
        assert compute_icir(ic_series) > 0


# ---------------------------------------------------------------------------
# T07-T08: compute_quantile_returns
# ---------------------------------------------------------------------------

class TestComputeQuantileReturns:

    def test_T07_monotonic_quantile_returns(self):
        """因子越大的组，收益越高（正向因子）。"""
        f = _make_factor(20, ascending=True)
        r = _make_returns(20, ascending=True)
        q_rets = compute_quantile_returns(f, r, n_quantiles=5)
        assert len(q_rets) == 5
        # Q1 < Q2 < Q3 < Q4 < Q5
        prev = q_rets[1]
        for q in range(2, 6):
            assert q_rets[q] > prev, f"Q{q}={q_rets[q]:.4f} 应 > Q{q-1}={prev:.4f}"
            prev = q_rets[q]

    def test_T08_too_few_symbols_returns_empty(self):
        """symbol 数量 < n_quantiles 时返回空字典。"""
        f = {"A": 1.0, "B": 2.0}
        r = {"A": 0.01, "B": 0.02}
        assert compute_quantile_returns(f, r, n_quantiles=5) == {}

    def test_T08b_custom_quantiles(self):
        """自定义 n_quantiles 应正确分组。"""
        f = _make_factor(12, ascending=True)
        r = _make_returns(12, ascending=True)
        q_rets = compute_quantile_returns(f, r, n_quantiles=3)
        assert set(q_rets.keys()) == {1, 2, 3}


# ---------------------------------------------------------------------------
# T09-T10: compute_turnover
# ---------------------------------------------------------------------------

class TestComputeTurnover:

    def test_T09_zero_turnover_same_factor(self):
        """相同因子时，换手率应为 0。"""
        f = {f"S{i}": float(i) for i in range(20)}
        assert compute_turnover(f, f, top_pct=0.20) == pytest.approx(0.0)

    def test_T10_full_turnover_disjoint_top(self):
        """前20% 完全不重叠时，换手率应为 1.0。"""
        # S00-S03 = 低值（prev 不入选）→ curr 完全更换
        prev = {f"S{i:02d}": float(i) for i in range(20)}         # S19 > ... > S00
        curr = {f"S{i:02d}": float(19 - i) for i in range(20)}     # S00 > ... > S19
        # prev top20% = {S16,S17,S18,S19}; curr top20% = {S00,S01,S02,S03}
        to = compute_turnover(prev, curr, top_pct=0.20)
        assert to == pytest.approx(1.0)

    def test_T10b_partial_turnover(self):
        """部分重叠时，换手率应在 0~1 之间。"""
        prev = {f"S{i}": float(i) for i in range(10)}
        # curr 中只把最低替换掉
        curr = dict(prev)
        curr["S0"] = 100.0  # S0 现在变成最高
        to = compute_turnover(prev, curr, top_pct=0.30)
        assert 0.0 < to < 1.0


# ---------------------------------------------------------------------------
# T11-T13: FactorAnalyzer
# ---------------------------------------------------------------------------

class TestFactorAnalyzer:

    def test_T11_multi_period_report(self):
        """多期数据后报告应包含正确的均值 IC / 分层收益。"""
        fa = FactorAnalyzer("test_factor", n_quantiles=5)
        f = _make_factor(20, ascending=True)
        r = _make_returns(20, ascending=True)
        for _ in range(10):
            fa.add_period(f, r)
        report = fa.generate_report()

        assert report.n_periods == 10
        assert abs(report.mean_ic - 1.0) < 1e-9
        # 多空收益应为正
        assert report.long_short_return > 0
        # 无换手（每期同样的因子）
        assert abs(report.mean_turnover) < 1e-9

    def test_T12_reset_clears_state(self):
        """reset() 后 generate_report 应返回空报告。"""
        fa = FactorAnalyzer("test_factor")
        fa.add_period(_make_factor(10), _make_returns(10))
        fa.reset()

        report = fa.generate_report()
        assert report.n_periods == 0
        assert math.isnan(report.mean_ic)
        assert math.isnan(report.icir)

    def test_T13_is_significant_threshold(self):
        """FactorReport.is_significant 应正确判断 IC/ICIR 门槛。"""
        # 达标
        good = FactorReport(
            factor_name="good", mean_ic=0.05, icir=0.35, n_periods=12,
        )
        assert good.is_significant is True

        # IC 不达标
        weak_ic = FactorReport(
            factor_name="weak_ic", mean_ic=0.02, icir=0.40, n_periods=12,
        )
        assert weak_ic.is_significant is False

        # ICIR 不达标
        weak_icir = FactorReport(
            factor_name="weak_icir", mean_ic=0.05, icir=0.25, n_periods=12,
        )
        assert weak_icir.is_significant is False
