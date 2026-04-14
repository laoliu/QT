"""Tests for aipha.models.feature_eng module.

覆盖要求 (§13.2): ≥10 tests per factor module.
目标: StandardScaler, RankScaler, Winsorizer, MissingValueHandler,
      Neutralizer, LagFeatureGenerator, RollingFeatureGenerator,
      FeaturePipeline, FeatureSelector — 正常/边界/缺失/极端值.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.models.feature_eng import (
    FeatureConfig,
    FeaturePipeline,
    FeatureSelector,
    FeatureTransformer,
    LagFeatureGenerator,
    MissingValueHandler,
    Neutralizer,
    RankScaler,
    RollingFeatureGenerator,
    ScalerType,
    StandardScaler,
    Winsorizer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_df() -> pd.DataFrame:
    """20-row sample DataFrame with 3 numeric features."""
    np.random.seed(42)
    n = 20
    return pd.DataFrame({
        "f1": np.random.randn(n) * 10 + 50,
        "f2": np.random.randn(n) * 5 + 100,
        "f3": np.random.randn(n) * 2 + 0,
    })


@pytest.fixture()
def sample_df_with_symbols() -> pd.DataFrame:
    """DataFrame with symbol column for per-symbol transforms."""
    np.random.seed(42)
    symbols = ["A"] * 10 + ["B"] * 10
    return pd.DataFrame({
        "symbol": symbols,
        "f1": np.random.randn(20) * 10 + 50,
        "f2": np.random.randn(20) * 5 + 100,
    })


@pytest.fixture()
def df_with_nan() -> pd.DataFrame:
    """DataFrame containing NaN values."""
    return pd.DataFrame({
        "f1": [1.0, np.nan, 3.0, np.nan, 5.0],
        "f2": [np.nan, 2.0, np.nan, 4.0, np.nan],
    })


# ============================= ScalerType ==================================

class TestScalerType:
    """ScalerType enum tests."""

    def test_values(self) -> None:
        assert ScalerType.STANDARD.value == "standard"
        assert ScalerType.RANK.value == "rank"
        assert ScalerType.MINMAX.value == "minmax"

    def test_membership(self) -> None:
        assert "standard" in [s.value for s in ScalerType]


# ============================= FeatureConfig ===============================

class TestFeatureConfig:
    """FeatureConfig dataclass tests."""

    def test_defaults(self) -> None:
        cfg = FeatureConfig(name="f1")
        assert cfg.name == "f1"
        assert cfg.scaler == ScalerType.STANDARD
        assert cfg.fillna_method == "median"
        assert cfg.winsorize is True

    def test_custom(self) -> None:
        cfg = FeatureConfig(
            name="f2",
            scaler=ScalerType.RANK,
            fillna_method="zero",
            winsorize=False,
            winsorize_percentile=(0.01, 0.99),
        )
        assert cfg.scaler == ScalerType.RANK
        assert cfg.winsorize is False
        assert cfg.winsorize_percentile == (0.01, 0.99)


# ============================= StandardScaler ==============================

class TestStandardScaler:
    """StandardScaler: Z-score normalization."""

    def test_fit_transform_basic(self, sample_df: pd.DataFrame) -> None:
        scaler = StandardScaler(columns=["f1", "f2"])
        result = scaler.fit_transform(sample_df)
        # After z-score, mean ≈ 0 and std ≈ 1
        assert abs(result["f1"].mean()) < 0.1
        assert abs(result["f1"].std() - 1.0) < 0.15

    def test_transform_uses_fitted_params(self, sample_df: pd.DataFrame) -> None:
        scaler = StandardScaler(columns=["f1"])
        scaler.fit(sample_df)
        result = scaler.transform(sample_df.head(5))
        # Should still produce values near 0 for subset
        assert len(result) == 5
        assert "f1" in result.columns

    def test_zero_std_column(self) -> None:
        """Column with constant values → std=0. Should not produce NaN/inf."""
        df = pd.DataFrame({"f1": [5.0] * 10, "f2": np.arange(10, dtype=float)})
        scaler = StandardScaler(columns=["f1", "f2"])
        result = scaler.fit_transform(df)
        # f1 all same → should be 0 or handled gracefully
        assert not result["f1"].isna().any()
        assert not np.isinf(result["f1"]).any()

    def test_single_row(self) -> None:
        """Single row edge case."""
        df = pd.DataFrame({"f1": [3.0]})
        scaler = StandardScaler(columns=["f1"])
        result = scaler.fit_transform(df)
        assert len(result) == 1

    def test_preserves_other_columns(self, sample_df: pd.DataFrame) -> None:
        """Columns not in `columns` list should be untouched."""
        scaler = StandardScaler(columns=["f1"])
        result = scaler.fit_transform(sample_df)
        pd.testing.assert_series_equal(result["f3"], sample_df["f3"])


# ============================= RankScaler ==================================

class TestRankScaler:
    """RankScaler: percentile ranking."""

    def test_rank_basic(self, sample_df: pd.DataFrame) -> None:
        scaler = RankScaler(columns=["f1"])
        result = scaler.fit_transform(sample_df)
        # Rank pct should be in (0, 1]
        assert result["f1"].min() > 0
        assert result["f1"].max() <= 1.0

    def test_rank_all_same(self) -> None:
        """All identical values should still produce valid output."""
        df = pd.DataFrame({"f1": [7.0] * 5})
        scaler = RankScaler(columns=["f1"])
        result = scaler.fit_transform(df)
        assert not result["f1"].isna().any()

    def test_preserves_other_columns(self, sample_df: pd.DataFrame) -> None:
        scaler = RankScaler(columns=["f1"])
        result = scaler.fit_transform(sample_df)
        pd.testing.assert_series_equal(result["f2"], sample_df["f2"])


# ============================= Winsorizer ==================================

class TestWinsorizer:
    """Winsorizer: quantile-based clipping."""

    def test_fit_clips_extremes(self) -> None:
        np.random.seed(0)
        df = pd.DataFrame({"f1": np.concatenate([np.arange(100), [1000, -500]])})
        wins = Winsorizer(columns=["f1"], lower=0.05, upper=0.95)
        result = wins.fit_transform(df)
        # Extremes should be clipped
        assert result["f1"].max() < 1000
        assert result["f1"].min() > -500

    def test_fit_then_transform(self) -> None:
        df = pd.DataFrame({"f1": np.arange(100, dtype=float)})
        wins = Winsorizer(columns=["f1"], lower=0.1, upper=0.9)
        wins.fit(df)
        new = pd.DataFrame({"f1": [-999.0, 999.0]})
        result = wins.transform(new)
        # New extremes clipped to fitted bounds
        assert result["f1"].iloc[0] >= df["f1"].quantile(0.1) - 1
        assert result["f1"].iloc[1] <= df["f1"].quantile(0.9) + 1

    def test_constant_column(self) -> None:
        """All same values → bounds identical → should not error."""
        df = pd.DataFrame({"f1": [3.0] * 10})
        wins = Winsorizer(columns=["f1"])
        result = wins.fit_transform(df)
        assert (result["f1"] == 3.0).all()


# ========================= MissingValueHandler =============================

class TestMissingValueHandler:
    """MissingValueHandler: fill/drop NaNs."""

    def test_mean(self, df_with_nan: pd.DataFrame) -> None:
        handler = MissingValueHandler(columns=["f1"], method="mean")
        result = handler.fit_transform(df_with_nan)
        assert not result["f1"].isna().any()
        # Mean of [1, 3, 5] = 3.0
        assert abs(result["f1"].iloc[1] - 3.0) < 1e-9

    def test_median(self, df_with_nan: pd.DataFrame) -> None:
        handler = MissingValueHandler(columns=["f1"], method="median")
        result = handler.fit_transform(df_with_nan)
        assert not result["f1"].isna().any()

    def test_zero(self, df_with_nan: pd.DataFrame) -> None:
        handler = MissingValueHandler(columns=["f1"], method="zero")
        result = handler.fit_transform(df_with_nan)
        assert result["f1"].iloc[1] == 0.0

    def test_forward_fill(self, df_with_nan: pd.DataFrame) -> None:
        handler = MissingValueHandler(columns=["f1"], method="forward")
        result = handler.fit_transform(df_with_nan)
        # Row 1 should forward-fill from row 0 = 1.0
        assert result["f1"].iloc[1] == 1.0

    def test_drop(self, df_with_nan: pd.DataFrame) -> None:
        handler = MissingValueHandler(columns=["f1"], method="drop")
        result = handler.fit_transform(df_with_nan)
        assert len(result) < len(df_with_nan)
        assert not result["f1"].isna().any()

    def test_no_nan_passthrough(self, sample_df: pd.DataFrame) -> None:
        """If no NaN, output should match input."""
        handler = MissingValueHandler(columns=["f1"], method="mean")
        result = handler.fit_transform(sample_df)
        pd.testing.assert_series_equal(result["f1"], sample_df["f1"])

    def test_all_nan_column(self) -> None:
        """Entire column is NaN → fill with 0 if method=zero."""
        df = pd.DataFrame({"f1": [np.nan, np.nan, np.nan]})
        handler = MissingValueHandler(columns=["f1"], method="zero")
        result = handler.fit_transform(df)
        assert (result["f1"] == 0.0).all()


# ============================= Neutralizer =================================

class TestNeutralizer:
    """Neutralizer: OLS regression residuals."""

    def test_market_cap_neutralization(self) -> None:
        np.random.seed(42)
        n = 50
        market_cap = np.random.uniform(1e8, 1e11, n)
        # Feature correlated with market cap
        f1 = 0.5 * np.log(market_cap) + np.random.randn(n)
        df = pd.DataFrame({"f1": f1, "market_cap": market_cap})
        neut = Neutralizer(
            feature_cols=["f1"],
            market_cap_col="market_cap",
            neutralize_market=True,
            neutralize_industry=False,
        )
        result = neut.fit_transform(df)
        # After neutralization, correlation with market_cap should be lower
        orig_corr = abs(np.corrcoef(f1, np.log(market_cap))[0, 1])
        new_corr = abs(np.corrcoef(result["f1"].values, np.log(market_cap))[0, 1])
        assert new_corr < orig_corr

    def test_small_sample_guard(self) -> None:
        """Very few samples; should handle gracefully."""
        df = pd.DataFrame({
            "f1": [1.0, 2.0],
            "market_cap": [1e9, 2e9],
        })
        neut = Neutralizer(
            feature_cols=["f1"],
            market_cap_col="market_cap",
            neutralize_market=True,
            neutralize_industry=False,
        )
        # Should not raise even with tiny data
        result = neut.fit_transform(df)
        assert len(result) == 2


# ========================= LagFeatureGenerator =============================

class TestLagFeatureGenerator:
    """LagFeatureGenerator: create lagged features."""

    def test_basic_lag(self) -> None:
        df = pd.DataFrame({"f1": [10.0, 20.0, 30.0, 40.0, 50.0]})
        gen = LagFeatureGenerator(columns=["f1"], lags=[1, 2])
        result = gen.fit_transform(df)
        assert "f1_lag1" in result.columns
        assert "f1_lag2" in result.columns
        assert result["f1_lag1"].iloc[1] == 10.0
        assert result["f1_lag2"].iloc[2] == 10.0

    def test_lag_per_symbol(self, sample_df_with_symbols: pd.DataFrame) -> None:
        gen = LagFeatureGenerator(
            columns=["f1"], lags=[1], symbol_col="symbol",
        )
        result = gen.fit_transform(sample_df_with_symbols)
        assert "f1_lag1" in result.columns
        # First row per symbol group should be NaN
        group_a = result[result["symbol"] == "A"]
        assert pd.isna(group_a["f1_lag1"].iloc[0])

    def test_empty_df(self) -> None:
        df = pd.DataFrame({"f1": pd.Series([], dtype=float)})
        gen = LagFeatureGenerator(columns=["f1"], lags=[1])
        result = gen.fit_transform(df)
        assert len(result) == 0


# ======================== RollingFeatureGenerator ==========================

class TestRollingFeatureGenerator:
    """RollingFeatureGenerator: rolling window statistics."""

    def test_rolling_mean(self) -> None:
        df = pd.DataFrame({"f1": [1.0, 2.0, 3.0, 4.0, 5.0]})
        gen = RollingFeatureGenerator(
            columns=["f1"], windows=[3], functions=["mean"],
        )
        result = gen.fit_transform(df)
        col = "f1_roll3_mean"
        assert col in result.columns
        # rolling(3).mean() at index 2 → (1+2+3)/3 = 2.0
        assert abs(result[col].iloc[2] - 2.0) < 1e-9

    def test_multiple_functions(self) -> None:
        df = pd.DataFrame({"f1": np.arange(10, dtype=float)})
        gen = RollingFeatureGenerator(
            columns=["f1"], windows=[3], functions=["mean", "std"],
        )
        result = gen.fit_transform(df)
        assert "f1_roll3_mean" in result.columns
        assert "f1_roll3_std" in result.columns

    def test_multiple_windows(self) -> None:
        df = pd.DataFrame({"f1": np.arange(10, dtype=float)})
        gen = RollingFeatureGenerator(
            columns=["f1"], windows=[3, 5], functions=["mean"],
        )
        result = gen.fit_transform(df)
        assert "f1_roll3_mean" in result.columns
        assert "f1_roll5_mean" in result.columns


# ============================ FeaturePipeline ==============================

class TestFeaturePipeline:
    """FeaturePipeline: sequential transformation pipeline."""

    def test_pipeline_fit_transform(self, sample_df: pd.DataFrame) -> None:
        pipe = FeaturePipeline()
        pipe.add_step("scaler", StandardScaler(columns=["f1", "f2"]))
        result = pipe.fit_transform(sample_df)
        assert abs(result["f1"].mean()) < 0.1

    def test_multistep_pipeline(self, sample_df: pd.DataFrame) -> None:
        pipe = FeaturePipeline()
        pipe.add_step("winsorize", Winsorizer(columns=["f1"]))
        pipe.add_step("scale", StandardScaler(columns=["f1"]))
        result = pipe.fit_transform(sample_df)
        assert "f1" in result.columns
        assert abs(result["f1"].mean()) < 0.2

    def test_get_steps(self) -> None:
        pipe = FeaturePipeline()
        pipe.add_step("a", StandardScaler(columns=["f1"]))
        pipe.add_step("b", RankScaler(columns=["f2"]))
        steps = pipe.get_steps()
        assert len(steps) == 2
        assert steps[0][0] == "a"
        assert steps[1][0] == "b"

    def test_empty_pipeline(self, sample_df: pd.DataFrame) -> None:
        pipe = FeaturePipeline()
        result = pipe.fit_transform(sample_df)
        pd.testing.assert_frame_equal(result, sample_df)


# ============================ FeatureSelector ==============================

class TestFeatureSelector:
    """FeatureSelector: select features by importance/correlation."""

    def test_select_by_importance_top_n(self) -> None:
        selector = FeatureSelector()
        importance = {"f1": 0.5, "f2": 0.3, "f3": 0.1, "f4": 0.05}
        selected = selector.select_by_importance(importance, top_n=2)
        assert len(selected) == 2
        assert "f1" in selected
        assert "f2" in selected

    def test_select_by_importance_threshold(self) -> None:
        selector = FeatureSelector()
        importance = {"f1": 0.5, "f2": 0.3, "f3": 0.1, "f4": 0.05}
        selected = selector.select_by_importance(importance, threshold=0.2)
        assert "f1" in selected
        assert "f2" in selected
        assert "f3" not in selected

    def test_select_by_correlation(self) -> None:
        np.random.seed(42)
        n = 100
        target = np.random.randn(n)
        df = pd.DataFrame({
            "f1": target + np.random.randn(n) * 0.1,  # high corr
            "f2": np.random.randn(n),  # low corr
        })
        selector = FeatureSelector()
        selected = selector.select_by_correlation(
            df, target=pd.Series(target), top_n=1,
        )
        assert len(selected) == 1
        assert "f1" in selected

    def test_select_low_correlation_features(self) -> None:
        np.random.seed(42)
        n = 100
        base = np.random.randn(n)
        df = pd.DataFrame({
            "f1": base,
            "f2": base + np.random.randn(n) * 0.01,  # near-duplicate
            "f3": np.random.randn(n),  # independent
        })
        selector = FeatureSelector()
        selected = selector.select_low_correlation_features(
            df, threshold=0.9,
        )
        # Should drop one of f1/f2 (high mutual corr)
        assert len(selected) <= 2
        assert "f3" in selected
