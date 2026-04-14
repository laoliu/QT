"""Tests for ml_neutralizer module (Du 2025)."""

import numpy as np
import pandas as pd
import pytest

from aipha.factors.ml_neutralizer import (
    build_regression_matrix,
    neutralize_cross_section,
    neutralize_ml_features,
)


class TestBuildRegressionMatrix:
    """build_regression_matrix 测试组。"""

    def test_basic_shape(self):
        log_cap = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        X = build_regression_matrix(log_cap, quadratic_size=False)
        assert X.shape == (5, 2)  # intercept + log_cap

    def test_quadratic_shape(self):
        log_cap = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        X = build_regression_matrix(log_cap, quadratic_size=True)
        assert X.shape == (5, 3)  # intercept + log_cap + log_cap²

    def test_with_industry(self):
        log_cap = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        ind = np.array([0, 0, 1, 1, 2])
        X = build_regression_matrix(log_cap, ind, quadratic_size=True)
        # intercept(1) + log_cap(1) + log_cap²(1) + 2 dummies (3-1)
        assert X.shape == (5, 5)

    def test_intercept_column_is_ones(self):
        log_cap = np.array([1.0, 2.0, 3.0])
        X = build_regression_matrix(log_cap, quadratic_size=False)
        np.testing.assert_array_equal(X[:, 0], [1.0, 1.0, 1.0])


class TestNeutralizeCrossSection:
    """neutralize_cross_section 测试组。"""

    def test_basic_residuals(self):
        np.random.seed(42)
        n = 100
        X = np.column_stack([np.ones(n), np.random.randn(n)])
        beta_true = np.array([1.0, 2.0])
        y = X @ beta_true + np.random.randn(n) * 0.1
        res = neutralize_cross_section(y, X, min_samples=10)
        assert np.abs(np.nanmean(res)) < 0.05

    def test_nan_preservation(self):
        y = np.array([1.0, np.nan, 3.0, 4.0, 5.0] * 10)
        X = np.column_stack([np.ones(50), np.arange(50, dtype=float)])
        res = neutralize_cross_section(y, X, min_samples=10)
        assert np.isnan(res[1])
        assert np.isfinite(res[0])

    def test_too_few_samples(self):
        y = np.array([1.0, 2.0, 3.0])
        X = np.column_stack([np.ones(3), np.arange(3, dtype=float)])
        res = neutralize_cross_section(y, X, min_samples=30)
        assert np.all(np.isnan(res))


class TestNeutralizeMLFeatures:
    """neutralize_ml_features 集成测试。"""

    def _make_df(self, n_dates=10, n_stocks=50):
        np.random.seed(42)
        rows = []
        industries = ["银行", "医药", "科技", "消费", "能源"]
        for d in range(n_dates):
            dt = f"2024-01-{d + 1:02d}"
            for s in range(n_stocks):
                rows.append({
                    "datetime": dt,
                    "symbol": f"{s:06d}.SZ",
                    "circ_mv": np.random.uniform(1e4, 1e7),
                    "industry": industries[s % len(industries)],
                    "alpha_1": np.random.randn() + 0.5 * np.log(1e4 + s * 100),
                    "alpha_2": np.random.randn() * 2,
                })
        return pd.DataFrame(rows)

    def test_output_shape_unchanged(self):
        df = self._make_df()
        orig_shape = df.shape
        result = neutralize_ml_features(df, ["alpha_1", "alpha_2"])
        assert result.shape == orig_shape

    def test_neutralized_mean_near_zero(self):
        df = self._make_df(n_dates=5, n_stocks=100)
        result = neutralize_ml_features(df, ["alpha_1", "alpha_2"])
        for dt in result["datetime"].unique():
            mask = result["datetime"] == dt
            mean_val = result.loc[mask, "alpha_1"].mean()
            assert abs(mean_val) < 0.5  # 中性化后截面均值接近 0

    def test_no_industry_still_works(self):
        df = self._make_df()
        df.drop(columns=["industry"], inplace=True)
        result = neutralize_ml_features(df, ["alpha_1"])
        assert "alpha_1" in result.columns

    def test_no_cap_returns_unchanged(self):
        df = self._make_df()
        df.drop(columns=["circ_mv"], inplace=True)
        result = neutralize_ml_features(df, ["alpha_1"])
        pd.testing.assert_frame_equal(result, df)

    def test_no_temp_columns_leaked(self):
        df = self._make_df()
        result = neutralize_ml_features(df, ["alpha_1"])
        assert "_log_cap" not in result.columns
        assert "_ind_code" not in result.columns
