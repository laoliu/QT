"""Tests for behavioral_ml factor module (ML training pipeline compatible)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.factors.behavioral_ml import (
    BEHAVIORAL_FACTOR_NAMES,
    add_behavioral_factors,
)


def _make_panel(n_stocks: int = 50, n_days: int = 80) -> pd.DataFrame:
    """Create a synthetic panel DataFrame mimicking Alpha158 input format."""
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    symbols = [f"{600000 + i:06d}.SH" for i in range(n_stocks)]

    rows = []
    for sym in symbols:
        base_price = rng.uniform(5, 100)
        for d in dates:
            close = base_price * (1 + rng.normal(0, 0.02))
            base_price = close
            rows.append({
                "datetime": d,
                "symbol": sym,
                "open": close * (1 + rng.normal(0, 0.005)),
                "high": close * (1 + abs(rng.normal(0, 0.01))),
                "low": close * (1 - abs(rng.normal(0, 0.01))),
                "close": close,
                "volume": rng.uniform(1e6, 1e8),
                "turnover_rate": rng.uniform(0.5, 10.0),
            })

    df = pd.DataFrame(rows)
    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    return df


class TestAddBehavioralFactors:
    """Test add_behavioral_factors() end-to-end."""

    def test_basic_output(self):
        """All expected columns are added."""
        df = _make_panel()
        n_added = add_behavioral_factors(df)
        assert n_added == 6  # abt, max_ret, overreact + 3 cs_rank
        for col in BEHAVIORAL_FACTOR_NAMES:
            assert col in df.columns, f"Missing column: {col}"

    def test_no_nan_in_warm_period(self):
        """After warm-up period, factors should not be all NaN."""
        df = _make_panel(n_stocks=20, n_days=80)
        add_behavioral_factors(df)
        # After 60 days of warm-up, values should be available
        late = df[df["datetime"] >= df["datetime"].unique()[65]]
        for col in ["abt", "max_ret", "overreact"]:
            assert late[col].notna().sum() > 0, f"{col} all NaN"

    def test_cross_sectional_rank_range(self):
        """cs_rank columns should be in [0, 1]."""
        df = _make_panel()
        add_behavioral_factors(df)
        for col in ["cs_rank_abt", "cs_rank_max_ret", "cs_rank_overreact"]:
            valid = df[col].dropna()
            if len(valid) > 0:
                assert valid.min() >= 0.0
                assert valid.max() <= 1.0

    def test_overreact_is_composite(self):
        """Overreact should combine ABT, MAX, STR signals."""
        df = _make_panel(n_stocks=30, n_days=80)
        add_behavioral_factors(df)
        late = df[df["datetime"] >= df["datetime"].unique()[65]]
        valid = late[["overreact"]].dropna()
        assert len(valid) > 0
        # Overreaction is average of 3 ranks → bounded in (0, 1)
        assert valid["overreact"].min() >= 0.0
        assert valid["overreact"].max() <= 1.0

    def test_without_turnover_rate(self):
        """Should fallback to volume when turnover_rate missing."""
        df = _make_panel(n_stocks=10, n_days=80)
        df.drop(columns=["turnover_rate"], inplace=True)
        n_added = add_behavioral_factors(df)
        assert "abt" in df.columns
        assert n_added == 6

    def test_idempotent_columns(self):
        """Running twice should not double the columns."""
        df = _make_panel(n_stocks=10, n_days=80)
        add_behavioral_factors(df)
        cols_before = set(df.columns)
        add_behavioral_factors(df)
        cols_after = set(df.columns)
        assert cols_before == cols_after

    def test_empty_dataframe(self):
        """Empty input should not crash."""
        df = pd.DataFrame(columns=[
            "datetime", "symbol", "close", "volume", "turnover_rate",
        ])
        n_added = add_behavioral_factors(df)
        assert n_added == 6
        assert "overreact" in df.columns


class TestBehavioralFactorNames:
    """Test constant definitions."""

    def test_names_count(self):
        assert len(BEHAVIORAL_FACTOR_NAMES) == 6

    def test_names_content(self):
        assert "abt" in BEHAVIORAL_FACTOR_NAMES
        assert "max_ret" in BEHAVIORAL_FACTOR_NAMES
        assert "overreact" in BEHAVIORAL_FACTOR_NAMES
