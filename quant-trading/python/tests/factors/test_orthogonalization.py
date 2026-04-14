"""Tests for Factor Orthogonalization Framework.

测试覆盖（≥10 用例 per §6.2）：
- 向量工具函数
- 相关性计算
- Gram-Schmidt 正交化
- PCA 正交化
- 残差正交化
- 策略正交度评估
- 边界情况
"""

from __future__ import annotations

import math

import pytest

from aipha.factors.orthogonalization import (
    OrthMethod,
    _dot,
    _effective_dimensions,
    _mean,
    _norm,
    _scale,
    _standardize,
    _subtract,
    compute_factor_correlation,
    compute_factor_ic,
    evaluate_strategy_orthogonality,
    gram_schmidt_orthogonalize,
    pca_orthogonalize,
    residual_orthogonalize,
)


# ===========================================================================
# 1. 向量工具 (5 tests)
# ===========================================================================

class TestVectorUtils:
    def test_dot_product(self):
        assert abs(_dot([1, 2, 3], [4, 5, 6]) - 32) < 1e-10

    def test_norm(self):
        assert abs(_norm([3, 4]) - 5.0) < 1e-10

    def test_scale(self):
        assert _scale([1, 2, 3], 2.0) == [2.0, 4.0, 6.0]

    def test_subtract(self):
        assert _subtract([5, 3], [1, 2]) == [4, 1]

    def test_standardize(self):
        data = [10, 20, 30, 40, 50]
        std_data = _standardize(data)
        assert abs(_mean(std_data)) < 1e-10
        # std should be ~1 (sample)
        from aipha.factors.orthogonalization import _std
        assert abs(_std(std_data) - 1.0) < 1e-6


# ===========================================================================
# 2. 相关性 (4 tests)
# ===========================================================================

class TestCorrelation:
    def test_perfect_correlation(self):
        factors = {
            "A": [1, 2, 3, 4, 5],
            "B": [2, 4, 6, 8, 10],
        }
        corr = compute_factor_correlation(factors)
        assert abs(corr["A"]["B"] - 1.0) < 1e-6

    def test_self_correlation(self):
        factors = {"A": [1, 2, 3, 4, 5]}
        corr = compute_factor_correlation(factors)
        assert abs(corr["A"]["A"] - 1.0) < 1e-6

    def test_negative_correlation(self):
        factors = {
            "A": [1, 2, 3, 4, 5],
            "B": [5, 4, 3, 2, 1],
        }
        corr = compute_factor_correlation(factors)
        assert abs(corr["A"]["B"] - (-1.0)) < 1e-6

    def test_uncorrelated(self):
        factors = {
            "A": [1, -1, 1, -1, 1, -1],
            "B": [1, 1, -1, -1, 1, 1],
        }
        corr = compute_factor_correlation(factors)
        assert abs(corr["A"]["B"]) < 0.5  # approximately uncorrelated


# ===========================================================================
# 3. Factor IC (2 tests)
# ===========================================================================

class TestFactorIC:
    def test_perfect_ic(self):
        f = [1, 2, 3, 4, 5]
        r = [0.01, 0.02, 0.03, 0.04, 0.05]
        ic = compute_factor_ic(f, r)
        assert abs(ic - 1.0) < 1e-6

    def test_zero_ic_short_data(self):
        ic = compute_factor_ic([1], [2])
        assert ic == 0.0


# ===========================================================================
# 4. Gram-Schmidt (5 tests)
# ===========================================================================

class TestGramSchmidt:
    def test_two_factors(self):
        factors = {
            "A": [1, 0, 0, 0, 0],
            "B": [1, 1, 0, 0, 0],
        }
        orth = gram_schmidt_orthogonalize(factors, priority_order=["A", "B"])
        # B should be orthogonal to A
        dot_ab = _dot(orth["A"], orth["B"])
        assert abs(dot_ab) < 1e-8

    def test_preserves_first_direction(self):
        factors = {
            "A": [1, 2, 3, 4, 5],
            "B": [2, 3, 4, 5, 6],
        }
        orth = gram_schmidt_orthogonalize(factors, priority_order=["A", "B"])
        # A direction preserved (just normalized)
        assert abs(_dot(orth["A"], _standardize([1, 2, 3, 4, 5])) / _norm(_standardize([1, 2, 3, 4, 5]))) > 0.9

    def test_three_factors_pairwise_orthogonal(self):
        factors = {
            "X": [1, 0, 1, 0, 1],
            "Y": [0, 1, 1, 0, 0],
            "Z": [1, 1, 0, 0, 1],
        }
        orth = gram_schmidt_orthogonalize(factors, priority_order=["X", "Y", "Z"])
        assert abs(_dot(orth["X"], orth["Y"])) < 1e-6
        assert abs(_dot(orth["X"], orth["Z"])) < 1e-6
        assert abs(_dot(orth["Y"], orth["Z"])) < 1e-6

    def test_empty(self):
        assert gram_schmidt_orthogonalize({}) == {}

    def test_single_factor(self):
        factors = {"A": [1, 2, 3]}
        orth = gram_schmidt_orthogonalize(factors)
        assert "A" in orth


# ===========================================================================
# 5. PCA (4 tests)
# ===========================================================================

class TestPCA:
    def test_two_correlated_factors(self):
        factors = {
            "A": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
            "B": [1.1, 2.2, 2.9, 4.1, 5.0, 5.9, 7.1, 8.0, 9.2, 10.1],
        }
        pc, explained = pca_orthogonalize(factors, n_components=2)
        assert "PC1" in pc
        assert len(explained) == 2
        # First PC should explain most variance
        assert explained[0] > 0.8

    def test_orthogonal_pcs(self):
        factors = {
            "A": [1, -1, 2, -2, 3] * 4,
            "B": [2, 1, -1, 3, -2] * 4,
        }
        pc, _ = pca_orthogonalize(factors, n_components=2)
        if "PC1" in pc and "PC2" in pc:
            # PCs should be orthogonal
            n = min(len(pc["PC1"]), len(pc["PC2"]))
            dot = _dot(pc["PC1"][:n], pc["PC2"][:n])
            assert abs(dot) < 1.0  # approximately orthogonal

    def test_returns_explained_variance(self):
        factors = {"A": [1, 2, 3, 4, 5, 6, 7, 8], "B": [2, 1, 4, 3, 5, 7, 6, 8]}
        _, explained = pca_orthogonalize(factors)
        assert len(explained) == 2
        assert sum(explained) > 0.5  # total explained should be meaningful

    def test_single_component(self):
        factors = {"A": [1, 2, 3], "B": [2, 4, 6]}
        pc, explained = pca_orthogonalize(factors, n_components=1)
        assert "PC1" in pc
        assert len(explained) == 1


# ===========================================================================
# 6. 残差正交化 (3 tests)
# ===========================================================================

class TestResidualOrthogonalize:
    def test_removes_correlated_component(self):
        target = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        controls = {"ctrl": [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]}
        residual = residual_orthogonalize(target, controls)
        # Residual should be orthogonal to control
        ctrl_std = _standardize(controls["ctrl"][:10])
        dot = _dot(residual, ctrl_std)
        assert abs(dot) < 1e-6

    def test_no_controls(self):
        target = [1, 2, 3]
        residual = residual_orthogonalize(target, {})
        assert len(residual) == 3

    def test_uncorrelated_unchanged(self):
        target = [1, -1, 1, -1, 1, -1]
        controls = {"ctrl": [1, 1, 1, 1, 1, 1]}
        residual = residual_orthogonalize(target, controls)
        # Already orthogonal → residual ≈ target (standardized)
        norm = _norm(residual)
        assert norm > 0.5  # not zeroed out


# ===========================================================================
# 7. 策略正交度评估 (4 tests)
# ===========================================================================

class TestEvaluateOrthogonality:
    def test_identical_strategies_low_score(self):
        returns = {
            "s1": [0.01, -0.01, 0.02, -0.02, 0.01] * 10,
            "s2": [0.01, -0.01, 0.02, -0.02, 0.01] * 10,
        }
        result = evaluate_strategy_orthogonality(returns)
        assert result["max_abs_correlation"] > 0.9
        assert result["orthogonality_score"] < 0.2

    def test_uncorrelated_strategies_high_score(self):
        returns = {
            "s1": [0.01, -0.01, 0.01, -0.01] * 15,
            "s2": [0.01, 0.01, -0.01, -0.01] * 15,
        }
        result = evaluate_strategy_orthogonality(returns)
        assert result["avg_abs_correlation"] < 0.3
        assert result["orthogonality_score"] > 0.7

    def test_single_strategy(self):
        returns = {"s1": [0.01, -0.01, 0.02]}
        result = evaluate_strategy_orthogonality(returns)
        assert result["orthogonality_score"] == 1.0

    def test_effective_dimensions(self):
        """多个独立策略应有较高有效维度。"""
        import random
        random.seed(42)
        returns = {
            f"s{i}": [random.gauss(0, 0.01) for _ in range(100)]
            for i in range(4)
        }
        result = evaluate_strategy_orthogonality(returns)
        assert result["effective_dimensions"] > 2.0


# ===========================================================================
# 8. 边界情况 (3 tests)
# ===========================================================================

class TestEdgeCases:
    def test_empty_factors(self):
        assert compute_factor_correlation({}) == {}

    def test_constant_factor(self):
        factors = {"A": [5.0] * 10, "B": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}
        corr = compute_factor_correlation(factors)
        # Constant factor → std=0 → correlation undefined, should be 0
        assert abs(corr["A"]["B"]) < 1e-6

    def test_effective_dimensions_formula(self):
        # Uniform distribution → max entropy → eff_dim = n
        explained = [0.25, 0.25, 0.25, 0.25]
        eff = _effective_dimensions(explained)
        assert abs(eff - 4.0) < 1e-6
