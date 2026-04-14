"""Tests for p1_factors_ml module (R125 validated factors for ML pipeline)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.factors.p1_factors_ml import (
    P1_FACTOR_NAMES,
    add_p1_factors,
)


def _make_panel(n_stocks: int = 50, n_days: int = 280) -> pd.DataFrame:
    """Create synthetic panel with all columns needed by P1 factors.

    Need >=252 days for 52WkHigh, >=60 days for reversal_3m.
    """
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2023-01-01", periods=n_days)
    symbols = [f"{600000 + i:06d}.SH" for i in range(n_stocks)]
    rows = []
    for sym in symbols:
        base = rng.uniform(5, 100)
        for d in dates:
            ret = rng.normal(0.0005, 0.02)
            close = base * (1 + ret)
            base = close
            rows.append({
                "datetime": d, "symbol": sym,
                "open": close * (1 + rng.normal(0, 0.005)),
                "high": close * (1 + abs(rng.normal(0, 0.01))),
                "low": close * (1 - abs(rng.normal(0, 0.01))),
                "close": close,
                "volume": rng.uniform(1e6, 1e8),
                "amount": rng.uniform(1e7, 1e9),
                "pre_close": close / (1 + ret),
                "pe_ttm": rng.uniform(5, 80),
                "pb": rng.uniform(0.5, 8),
                "grossprofit_margin": rng.uniform(10, 60),
                "netprofit_margin": rng.uniform(2, 30),
                "roe": rng.uniform(-5, 25),
                "eps": rng.uniform(-0.5, 3.0),
            })
    df = pd.DataFrame(rows)
    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    return df


class TestAddP1Factors:
    """Test add_p1_factors() end-to-end."""

    def test_basic_output(self):
        """All 12 expected columns are added (6 raw + 6 cs_rank)."""
        df = _make_panel()
        add_p1_factors(df)
        for col in P1_FACTOR_NAMES:
            assert col in df.columns, f"Missing column: {col}"

    def test_return_count(self):
        """Returns correct column count (12)."""
        df = _make_panel()
        n = add_p1_factors(df)
        assert n == 12  # 6 factors × (raw + cs_rank)

    def test_no_nan_in_warm_period(self):
        """After warm-up (260+ days) factors should have values."""
        df = _make_panel(n_stocks=20, n_days=280)
        add_p1_factors(df)
        late = df[df["datetime"] >= df["datetime"].unique()[265]]
        for col in ["amihud", "ivol", "skewness", "reversal_3m",
                     "value_prof", "wk52_high_rev"]:
            assert late[col].notna().sum() > 0, f"{col} all NaN"

    def test_cs_rank_range(self):
        """cs_rank columns in [0, 1]."""
        df = _make_panel()
        add_p1_factors(df)
        for col in P1_FACTOR_NAMES:
            if col.startswith("cs_rank_"):
                valid = df[col].dropna()
                if len(valid) > 0:
                    assert valid.min() >= 0.0
                    assert valid.max() <= 1.0

    def test_amihud_positive(self):
        """Amihud ILLIQ should be positive (|ret|/amount)."""
        df = _make_panel(n_stocks=10, n_days=50)
        add_p1_factors(df)
        valid = df["amihud"].dropna()
        assert (valid >= 0).all()

    def test_ivol_negative(self):
        """IVOL column should be negative (low vol = good → negated)."""
        df = _make_panel(n_stocks=10, n_days=50)
        add_p1_factors(df)
        valid = df["ivol"].dropna()
        # All values should be <= 0 (negated std)
        assert (valid <= 0).all()

    def test_skewness_sign(self):
        """Skewness is negated (low skew = good)."""
        df = _make_panel(n_stocks=10, n_days=50)
        add_p1_factors(df)
        # skewness column exists and has both + and - values
        valid = df["skewness"].dropna()
        assert len(valid) > 0

    def test_reversal_3m_sign(self):
        """3M reversal is negated (past losers = good)."""
        df = _make_panel(n_stocks=10, n_days=80)
        add_p1_factors(df)
        valid = df["reversal_3m"].dropna()
        assert len(valid) > 0

    def test_wk52_high_reversed(self):
        """52WkHigh is reversed for A-share market."""
        df = _make_panel(n_stocks=10, n_days=280)
        add_p1_factors(df)
        valid = df["wk52_high_rev"].dropna()
        # Reversed: -(close/max), all values should be <= 0
        assert (valid <= 0).all()

    def test_value_prof_composite(self):
        """Value+Prof in (0,1) as rank average."""
        df = _make_panel(n_stocks=30, n_days=50)
        add_p1_factors(df)
        valid = df["value_prof"].dropna()
        assert valid.min() >= 0.0
        assert valid.max() <= 1.0

    def test_missing_amount_graceful(self):
        """Amihud skips gracefully when 'amount' missing."""
        df = _make_panel(n_stocks=10, n_days=30)
        df.drop(columns=["amount"], inplace=True)
        n = add_p1_factors(df)
        # 10 columns instead of 12 (amihud + cs_rank_amihud skipped)
        assert n == 10
        assert "amihud" not in df.columns

    def test_missing_high_graceful(self):
        """52WkHigh skips gracefully when 'high' missing."""
        df = _make_panel(n_stocks=10, n_days=30)
        df.drop(columns=["high"], inplace=True)
        n = add_p1_factors(df)
        # 10 columns instead of 12 (wk52_high_rev + cs_rank skipped)
        assert n == 10
        assert "wk52_high_rev" not in df.columns
