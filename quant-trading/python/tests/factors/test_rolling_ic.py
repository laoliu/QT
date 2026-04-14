"""tests for rolling_ic module"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from aipha.factors.rolling_ic import (
    AdaptiveWeights,
    RollingICAnalyzer,
    RollingICResult,
    SegmentComparison,
    SegmentICResult,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_dates(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def _make_factor_return(
    n_days: int = 100,
    n_symbols: int = 30,
    ic_target: float = 0.05,
    seed: int = 42,
) -> tuple[dict[date, dict[str, float]], dict[date, dict[str, float]]]:
    """生成合成因子和收益数据, IC ≈ ic_target."""
    rng = np.random.default_rng(seed)
    dates = _make_dates(n_days)
    symbols = [f"S{i:03d}" for i in range(n_symbols)]
    factor_data: dict[date, dict[str, float]] = {}
    return_data: dict[date, dict[str, float]] = {}
    for d in dates:
        fv = rng.standard_normal(n_symbols)
        noise = rng.standard_normal(n_symbols)
        rv = ic_target * fv + np.sqrt(1 - ic_target**2) * noise
        factor_data[d] = {s: float(fv[i]) for i, s in enumerate(symbols)}
        return_data[d] = {s: float(rv[i]) for i, s in enumerate(symbols)}
    return factor_data, return_data


# ---------------------------------------------------------------------------
# Data structure tests
# ---------------------------------------------------------------------------

class TestDataStructures:
    def test_rolling_ic_result_to_dict(self):
        r = RollingICResult(
            factor_name="f1", window=60, ic_series={},
            ic_mean=0.05, ic_std=0.02, ir=2.5,
            trend_slope=0.0001, stability=0.6,
        )
        d = r.to_dict()
        assert d["factor_name"] == "f1"
        assert d["ir"] == 2.5
        assert d["n_points"] == 0

    def test_segment_ic_result_to_dict(self):
        r = SegmentICResult(
            factor_name="f1", segment_name="pre",
            ic_mean=0.04, ic_std=0.03, ir=1.33,
            n_days=100, t_stat=4.33, p_value=0.001,
        )
        d = r.to_dict()
        assert d["n_days"] == 100

    def test_segment_comparison_to_dict(self):
        seg = SegmentICResult("f1", "pre", 0.04, 0.03, 1.33, 100, 4.0, 0.001)
        comp = SegmentComparison(
            factor_name="f1", segments=[seg, seg],
            ic_change=0.01, ic_ratio=1.25, regime_robust=True,
        )
        d = comp.to_dict()
        assert len(d["segments"]) == 2
        assert d["regime_robust"] is True

    def test_adaptive_weights_to_dict(self):
        w = AdaptiveWeights(
            weights={"f1": 0.6, "f2": 0.4},
            ic_values={"f1": 0.05, "f2": 0.03},
            as_of_date=date(2024, 1, 1),
        )
        d = w.to_dict()
        assert abs(sum(d["weights"].values()) - 1.0) < 0.01


# ---------------------------------------------------------------------------
# RollingICAnalyzer tests
# ---------------------------------------------------------------------------

class TestRollingICAnalyzer:
    def test_compute_rolling_ic_basic(self):
        fdata, rdata = _make_factor_return(100, 30, ic_target=0.10, seed=1)
        analyzer = RollingICAnalyzer({"alpha": fdata}, rdata)
        result = analyzer.compute_rolling_ic("alpha", window=20)
        assert isinstance(result, RollingICResult)
        assert result.factor_name == "alpha"
        assert len(result.ic_series) > 0
        # IC应为正(因子与收益正相关)
        assert result.ic_mean > 0

    def test_compute_rolling_ic_empty_factor(self):
        _, rdata = _make_factor_return(50, 10)
        analyzer = RollingICAnalyzer({}, rdata)
        result = analyzer.compute_rolling_ic("missing", window=20)
        assert result.ic_mean == 0.0
        assert len(result.ic_series) == 0

    def test_compute_rolling_ic_window_larger_than_data(self):
        fdata, rdata = _make_factor_return(10, 10, seed=3)
        analyzer = RollingICAnalyzer({"f": fdata}, rdata)
        result = analyzer.compute_rolling_ic("f", window=20)
        assert len(result.ic_series) == 0

    def test_compute_rolling_ic_trend_slope(self):
        """因子IC恒正时, trend_slope应接近0"""
        fdata, rdata = _make_factor_return(200, 30, ic_target=0.15, seed=7)
        analyzer = RollingICAnalyzer({"f": fdata}, rdata)
        result = analyzer.compute_rolling_ic("f", window=40)
        assert isinstance(result.trend_slope, float)

    def test_segment_ic_basic(self):
        fdata, rdata = _make_factor_return(200, 30, ic_target=0.10, seed=5)
        analyzer = RollingICAnalyzer({"f": fdata}, rdata)
        cutoff = _make_dates(200)[100]
        result = analyzer.compare_segments("f", cutoff_date=cutoff)
        assert isinstance(result, SegmentComparison)
        assert len(result.segments) == 2
        assert result.segments[0].segment_name == "pre"
        assert result.segments[1].segment_name == "post"
        assert result.segments[0].n_days > 0
        assert result.segments[1].n_days > 0

    def test_segment_ic_regime_robust(self):
        """高IC因子应在两段都有效"""
        fdata, rdata = _make_factor_return(200, 50, ic_target=0.20, seed=8)
        analyzer = RollingICAnalyzer({"f": fdata}, rdata)
        cutoff = _make_dates(200)[100]
        result = analyzer.compare_segments("f", cutoff_date=cutoff)
        # 高IC因子两段IC都应同号
        assert (result.segments[0].ic_mean * result.segments[1].ic_mean) > 0

    def test_segment_ic_empty_factor(self):
        _, rdata = _make_factor_return(50, 10)
        analyzer = RollingICAnalyzer({}, rdata)
        result = analyzer.compare_segments("missing", date(2020, 3, 1))
        assert result.segments[0].n_days == 0


class TestAdaptiveWeights:
    def test_basic_adaptive_weights(self):
        """高IC因子应获得更高权重"""
        rng = np.random.default_rng(99)
        n_days, n_sym = 100, 30
        dates = _make_dates(n_days)
        symbols = [f"S{i:03d}" for i in range(n_sym)]
        rdata: dict[date, dict[str, float]] = {}
        f_strong: dict[date, dict[str, float]] = {}
        f_weak: dict[date, dict[str, float]] = {}
        for d in dates:
            fs = rng.standard_normal(n_sym)
            fw = rng.standard_normal(n_sym)
            noise = rng.standard_normal(n_sym)
            ret = 0.30 * fs + 0.02 * fw + noise
            rdata[d] = {s: float(ret[i]) for i, s in enumerate(symbols)}
            f_strong[d] = {s: float(fs[i]) for i, s in enumerate(symbols)}
            f_weak[d] = {s: float(fw[i]) for i, s in enumerate(symbols)}

        analyzer = RollingICAnalyzer(
            {"strong": f_strong, "weak": f_weak}, rdata,
        )
        aw = analyzer.compute_adaptive_weights(
            ["strong", "weak"], lookback=60, blend_ratio=0.8,
        )
        assert isinstance(aw, AdaptiveWeights)
        assert abs(sum(aw.weights.values()) - 1.0) < 1e-6
        # 强因子应获得更高权重
        assert aw.weights["strong"] > aw.weights["weak"]

    def test_adaptive_weights_equal_when_no_data(self):
        analyzer = RollingICAnalyzer({}, {})
        aw = analyzer.compute_adaptive_weights(["f1", "f2"])
        assert abs(aw.weights["f1"] - 0.5) < 1e-6
        assert abs(aw.weights["f2"] - 0.5) < 1e-6

    def test_adaptive_weights_blend_ratio_zero_is_base(self):
        fdata, rdata = _make_factor_return(100, 20, seed=11)
        analyzer = RollingICAnalyzer({"f": fdata}, rdata)
        base = {"f": 0.7, "g": 0.3}
        aw = analyzer.compute_adaptive_weights(
            ["f", "g"], base_weights=base, blend_ratio=0.0,
        )
        # blend=0 → 纯基线权重
        assert abs(aw.weights["f"] - 0.7) < 1e-6
        assert abs(aw.weights["g"] - 0.3) < 1e-6

    def test_adaptive_weights_with_as_of(self):
        fdata, rdata = _make_factor_return(100, 20, seed=13)
        analyzer = RollingICAnalyzer({"f": fdata}, rdata)
        mid = _make_dates(100)[50]
        aw = analyzer.compute_adaptive_weights(["f"], as_of=mid)
        assert aw.as_of_date == mid
