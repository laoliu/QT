"""Tests for p5_value_factors_ml module (R96 value investor factors)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.factors.p5_value_factors_ml import (
    P5_FACTOR_NAMES,
    add_p5_value_factors,
)


def _make_panel(n_stocks: int = 30, n_days: int = 100) -> pd.DataFrame:
    """Create synthetic panel data for P5 value factor testing."""
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
                "close": close,
                "pe_ttm": rng.uniform(5, 80),
                "pb": rng.uniform(0.5, 8),
                "dv_ttm": rng.uniform(0, 5),
                "dt_netprofit_yoy": rng.uniform(-20, 50),
                "cfps": rng.uniform(-1, 3),
                "eps": rng.uniform(0.01, 2),
                "debt_to_assets": rng.uniform(20, 80),
            })
    df = pd.DataFrame(rows)
    return df.sort_values(["symbol", "datetime"]).reset_index(drop=True)


class TestAddP5ValueFactors:
    """Test add_p5_value_factors() end-to-end."""

    def test_all_columns_added(self):
        """All 16 expected columns are produced."""
        df = _make_panel()
        n = add_p5_value_factors(df)
        assert n == 16
        for col in P5_FACTOR_NAMES:
            assert col in df.columns, f"Missing column: {col}"

    def test_cs_rank_range(self):
        """All cs_rank columns in [0, 1]."""
        df = _make_panel()
        add_p5_value_factors(df)
        for col in P5_FACTOR_NAMES:
            if col.startswith("cs_rank_"):
                valid = df[col].dropna()
                assert valid.min() >= 0, f"{col} min < 0"
                assert valid.max() <= 1.0, f"{col} max > 1"

    def test_no_all_nan(self):
        """No factor column is entirely NaN."""
        df = _make_panel()
        add_p5_value_factors(df)
        for col in P5_FACTOR_NAMES:
            assert df[col].notna().sum() > 0, f"{col} all NaN"

    def test_reversal_60d_needs_history(self):
        """reversal_60d should be NaN for first 60 rows per stock."""
        df = _make_panel(n_stocks=5, n_days=80)
        add_p5_value_factors(df)
        first = df.groupby("symbol").head(60)
        assert first["v_reversal_60d"].isna().all()

    def test_missing_pe_ttm_graceful(self):
        """EP and lynch factors skipped if pe_ttm missing."""
        df = _make_panel()
        df.drop(columns=["pe_ttm"], inplace=True)
        n = add_p5_value_factors(df)
        assert "v_ep" not in df.columns
        assert "v_lynch" not in df.columns
        assert n < 16

    def test_ep_clipping(self):
        """EP values should be clipped to [-1, 1]."""
        df = _make_panel()
        add_p5_value_factors(df)
        assert df["v_ep"].min() >= -1.0
        assert df["v_ep"].max() <= 1.0

    def test_low_leverage_range(self):
        """low_leverage should be in [0, 1]."""
        df = _make_panel()
        add_p5_value_factors(df)
        valid = df["v_low_leverage"].dropna()
        assert valid.min() >= 0.0
        assert valid.max() <= 1.0

    def test_inplace_modification(self):
        """Function modifies df in-place, no copy returned."""
        df = _make_panel()
        orig_id = id(df)
        add_p5_value_factors(df)
        # df should still be same object
        assert "v_ep" in df.columns

