"""因子相关性与换手率测试

覆盖：
- compute_correlation_matrix: 正常/边界/方法选择
- detect_redundant_factors: 阈值/排序/边界
- compute_factor_turnover: 正常/边界/Top/Bottom
- CorrelationMatrix 辅助方法: get/top_pairs/to_dict
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from aipha.factors.correlation import (
    CorrelationMatrix,
    CorrelationPair,
    FactorTurnoverResult,
    compute_correlation_matrix,
    compute_factor_turnover,
    detect_redundant_factors,
)


# ============================================================================
# Fixtures
# ============================================================================


def _make_panel(
    n_dates: int = 20,
    n_symbols: int = 50,
    seed: int = 42,
    noise: float = 0.1,
) -> dict[date, dict[str, float]]:
    """生成随机因子面板"""
    rng = np.random.RandomState(seed)
    base_date = date(2024, 1, 1)
    panel: dict[date, dict[str, float]] = {}
    for d in range(n_dates):
        dt = base_date + timedelta(days=d)
        panel[dt] = {
            f"S{i:04d}": float(rng.randn()) for i in range(n_symbols)
        }
    return panel


def _make_correlated_panels(
    correlation: float = 0.9,
    n_dates: int = 30,
    n_symbols: int = 100,
    seed: int = 42,
) -> tuple[dict[date, dict[str, float]], dict[date, dict[str, float]]]:
    """生成两个高相关的因子面板"""
    rng = np.random.RandomState(seed)
    base_date = date(2024, 1, 1)
    panel_a: dict[date, dict[str, float]] = {}
    panel_b: dict[date, dict[str, float]] = {}
    for d in range(n_dates):
        dt = base_date + timedelta(days=d)
        symbols = [f"S{i:04d}" for i in range(n_symbols)]
        vals_a = rng.randn(n_symbols)
        noise = rng.randn(n_symbols) * np.sqrt(1 - correlation**2)
        vals_b = correlation * vals_a + noise
        panel_a[dt] = dict(zip(symbols, vals_a.tolist()))
        panel_b[dt] = dict(zip(symbols, vals_b.tolist()))
    return panel_a, panel_b


# ============================================================================
# TestComputeCorrelationMatrix
# ============================================================================


class TestComputeCorrelationMatrix:
    """compute_correlation_matrix 测试"""

    def test_basic_spearman(self) -> None:
        p1 = _make_panel(seed=1)
        p2 = _make_panel(seed=2)
        result = compute_correlation_matrix(
            {"f1": p1, "f2": p2}, method="spearman",
        )
        assert isinstance(result, CorrelationMatrix)
        assert result.factor_names == ["f1", "f2"]
        assert result.matrix.shape == (2, 2)
        # 对角线 = 1.0
        assert abs(result.matrix[0, 0] - 1.0) < 0.01
        assert abs(result.matrix[1, 1] - 1.0) < 0.01
        assert result.method == "spearman"

    def test_basic_pearson(self) -> None:
        p1 = _make_panel(seed=1)
        p2 = _make_panel(seed=2)
        result = compute_correlation_matrix(
            {"f1": p1, "f2": p2}, method="pearson",
        )
        assert result.method == "pearson"
        assert result.matrix.shape == (2, 2)

    def test_high_correlation(self) -> None:
        pa, pb = _make_correlated_panels(correlation=0.95)
        result = compute_correlation_matrix({"a": pa, "b": pb})
        corr = result.get("a", "b")
        assert corr > 0.85, f"Expected high correlation, got {corr}"

    def test_uncorrelated(self) -> None:
        p1 = _make_panel(seed=100, n_symbols=200)
        p2 = _make_panel(seed=999, n_symbols=200)
        result = compute_correlation_matrix({"x": p1, "y": p2})
        corr = abs(result.get("x", "y"))
        assert corr < 0.3, f"Expected low correlation, got {corr}"

    def test_three_factors(self) -> None:
        p1 = _make_panel(seed=1)
        p2 = _make_panel(seed=2)
        p3 = _make_panel(seed=3)
        result = compute_correlation_matrix({"a": p1, "b": p2, "c": p3})
        assert result.matrix.shape == (3, 3)
        assert len(result.factor_names) == 3

    def test_symmetric(self) -> None:
        p1 = _make_panel(seed=10)
        p2 = _make_panel(seed=20)
        result = compute_correlation_matrix({"x": p1, "y": p2})
        assert abs(result.matrix[0, 1] - result.matrix[1, 0]) < 1e-10

    def test_too_few_factors_raises(self) -> None:
        p1 = _make_panel()
        with pytest.raises(ValueError, match="至少需要 2 个因子"):
            compute_correlation_matrix({"only_one": p1})

    def test_no_common_symbols(self) -> None:
        """不同标的集合 → 矩阵全零（无数据）"""
        base = date(2024, 1, 1)
        p1 = {base: {f"A{i}": float(i) for i in range(20)}}
        p2 = {base: {f"B{i}": float(i) for i in range(20)}}
        result = compute_correlation_matrix(
            {"f1": p1, "f2": p2}, min_common_symbols=5,
        )
        assert result.n_dates == 0

    def test_min_common_symbols_filter(self) -> None:
        """公共标的不足时日期被过滤"""
        base = date(2024, 1, 1)
        p1 = {base: {f"S{i}": float(i) for i in range(8)}}
        p2 = {base: {f"S{i}": float(i) for i in range(8)}}
        result = compute_correlation_matrix(
            {"f1": p1, "f2": p2}, min_common_symbols=10,
        )
        assert result.n_dates == 0

    def test_n_dates_and_avg_symbols(self) -> None:
        p1 = _make_panel(n_dates=15, n_symbols=30)
        p2 = _make_panel(n_dates=15, n_symbols=30, seed=99)
        result = compute_correlation_matrix({"a": p1, "b": p2})
        assert result.n_dates > 0
        assert result.avg_n_symbols > 0


# ============================================================================
# TestCorrelationMatrix
# ============================================================================


class TestCorrelationMatrix:
    """CorrelationMatrix 辅助方法"""

    @pytest.fixture()
    def matrix(self) -> CorrelationMatrix:
        pa, pb = _make_correlated_panels(0.9)
        p3 = _make_panel(seed=777)
        return compute_correlation_matrix({"a": pa, "b": pb, "c": p3})

    def test_get(self, matrix: CorrelationMatrix) -> None:
        c = matrix.get("a", "b")
        assert isinstance(c, float)

    def test_get_symmetric(self, matrix: CorrelationMatrix) -> None:
        assert abs(matrix.get("a", "b") - matrix.get("b", "a")) < 1e-10

    def test_top_pairs(self, matrix: CorrelationMatrix) -> None:
        pairs = matrix.top_pairs(n=2)
        assert len(pairs) <= 2
        assert isinstance(pairs[0], CorrelationPair)
        # 最高相关的一对应该是 a-b
        top = pairs[0]
        assert {top.factor_a, top.factor_b} == {"a", "b"}

    def test_top_pairs_nonabsolute(self, matrix: CorrelationMatrix) -> None:
        pairs = matrix.top_pairs(n=3, absolute=False)
        # 应按原始值降序
        for i in range(len(pairs) - 1):
            assert pairs[i].correlation >= pairs[i + 1].correlation

    def test_to_dict(self, matrix: CorrelationMatrix) -> None:
        d = matrix.to_dict()
        assert "factor_names" in d
        assert "matrix" in d
        assert "p_values" in d
        assert len(d["matrix"]) == len(d["factor_names"])

    def test_to_dict_serializable(self, matrix: CorrelationMatrix) -> None:
        import json

        d = matrix.to_dict()
        s = json.dumps(d)
        assert len(s) > 0


# ============================================================================
# TestDetectRedundantFactors
# ============================================================================


class TestDetectRedundantFactors:
    """detect_redundant_factors 测试"""

    def test_finds_high_correlation(self) -> None:
        pa, pb = _make_correlated_panels(0.95)
        pairs = detect_redundant_factors(
            {"a": pa, "b": pb}, threshold=0.7,
        )
        assert len(pairs) > 0
        assert abs(pairs[0].correlation) >= 0.7

    def test_no_redundancy(self) -> None:
        p1 = _make_panel(seed=1, n_symbols=200)
        p2 = _make_panel(seed=999, n_symbols=200)
        pairs = detect_redundant_factors(
            {"x": p1, "y": p2}, threshold=0.7,
        )
        assert len(pairs) == 0

    def test_sorted_by_abs_correlation(self) -> None:
        pa, pb = _make_correlated_panels(0.95)
        pc = _make_panel(seed=777)
        pairs = detect_redundant_factors(
            {"a": pa, "b": pb, "c": pc}, threshold=0.1,
        )
        for i in range(len(pairs) - 1):
            assert abs(pairs[i].correlation) >= abs(pairs[i + 1].correlation)

    def test_threshold_0_raises(self) -> None:
        with pytest.raises(ValueError, match="阈值"):
            detect_redundant_factors(
                {"a": _make_panel(), "b": _make_panel(seed=2)},
                threshold=0.0,
            )

    def test_threshold_1_raises(self) -> None:
        with pytest.raises(ValueError, match="阈值"):
            detect_redundant_factors(
                {"a": _make_panel(), "b": _make_panel(seed=2)},
                threshold=1.0,
            )

    def test_high_threshold_filters_all(self) -> None:
        pa, pb = _make_correlated_panels(0.6)
        pairs = detect_redundant_factors(
            {"a": pa, "b": pb}, threshold=0.99,
        )
        assert len(pairs) == 0


# ============================================================================
# TestComputeFactorTurnover
# ============================================================================


class TestComputeFactorTurnover:
    """compute_factor_turnover 测试"""

    def test_basic(self) -> None:
        panel = _make_panel(n_dates=20, n_symbols=50)
        result = compute_factor_turnover(panel, factor_name="test")
        assert isinstance(result, FactorTurnoverResult)
        assert result.factor_name == "test"
        assert 0 <= result.avg_turnover <= 2.0  # 理论最大 2.0
        assert result.n_dates == 20
        assert result.quantile == 0.2

    def test_constant_panel_zero_turnover(self) -> None:
        """常数面板 → 换手率为 0"""
        base = date(2024, 1, 1)
        panel = {}
        for d in range(10):
            dt = base + timedelta(days=d)
            panel[dt] = {f"S{i}": float(i) for i in range(20)}
        result = compute_factor_turnover(panel)
        assert result.avg_turnover == 0.0

    def test_full_turnover(self) -> None:
        """每期完全不同 → 换手率 = 2.0"""
        base = date(2024, 1, 1)
        panel = {}
        for d in range(5):
            dt = base + timedelta(days=d)
            # 每天不同的标的在 Top 位置
            panel[dt] = {
                f"S{i}": float(100 - i if (i + d) % 5 == 0 else i)
                for i in range(25)
            }
        result = compute_factor_turnover(panel, quantile=0.2)
        assert result.avg_turnover > 0

    def test_top_vs_bottom(self) -> None:
        panel = _make_panel(n_dates=10, n_symbols=50, seed=123)
        top = compute_factor_turnover(panel, top=True)
        bottom = compute_factor_turnover(panel, top=False)
        assert isinstance(top.avg_turnover, float)
        assert isinstance(bottom.avg_turnover, float)

    def test_quantile_range(self) -> None:
        panel = _make_panel(n_dates=10, n_symbols=50)
        result = compute_factor_turnover(panel, quantile=0.5)
        assert result.quantile == 0.5
        assert result.avg_group_size > 0

    def test_single_date(self) -> None:
        panel = {date(2024, 1, 1): {f"S{i}": float(i) for i in range(20)}}
        result = compute_factor_turnover(panel)
        assert result.avg_turnover == 0.0
        assert result.n_dates == 1

    def test_empty_panel(self) -> None:
        result = compute_factor_turnover({})
        assert result.avg_turnover == 0.0
        assert result.n_dates == 0

    def test_invalid_quantile_raises(self) -> None:
        with pytest.raises(ValueError, match="quantile"):
            compute_factor_turnover(_make_panel(), quantile=0.0)

    def test_invalid_quantile_high_raises(self) -> None:
        with pytest.raises(ValueError, match="quantile"):
            compute_factor_turnover(_make_panel(), quantile=1.0)

    def test_turnover_by_date_keys(self) -> None:
        panel = _make_panel(n_dates=10, n_symbols=50)
        result = compute_factor_turnover(panel)
        # 换手率只从第二天开始有
        assert len(result.turnover_by_date) == result.n_dates - 1

    def test_avg_group_size(self) -> None:
        panel = _make_panel(n_dates=5, n_symbols=100)
        result = compute_factor_turnover(panel, quantile=0.1)
        assert result.avg_group_size == pytest.approx(10.0, abs=1)


# ============================================================================
# TestCorrelationPair
# ============================================================================


class TestCorrelationPair:
    """CorrelationPair 数据结构"""

    def test_frozen(self) -> None:
        p = CorrelationPair("a", "b", 0.5, 0.01, 100)
        with pytest.raises(AttributeError):
            p.correlation = 0.9  # type: ignore[misc]

    def test_attrs(self) -> None:
        p = CorrelationPair("x", "y", 0.75, 0.001, 50)
        assert p.factor_a == "x"
        assert p.factor_b == "y"
        assert p.correlation == 0.75
        assert p.p_value == 0.001
        assert p.n_observations == 50
