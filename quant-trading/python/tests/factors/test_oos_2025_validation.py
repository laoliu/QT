"""Unit tests for oos_2025_validation.py."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from oos_2025_validation import (
    compute_ic_series,
    compute_top_bottom_return,
)


def _make_oos_df(n_days: int = 50, n_stocks: int = 100,
                 start: str = "2024-01-01") -> pd.DataFrame:
    """Create synthetic OOS prediction DataFrame."""
    dates = pd.bdate_range(start, periods=n_days)
    rows = []
    rng = np.random.RandomState(42)
    for dt in dates:
        preds = rng.randn(n_stocks)
        # label correlated with prediction (IC ~ 0.3)
        labels = 0.3 * preds + 0.7 * rng.randn(n_stocks)
        for i in range(n_stocks):
            rows.append({
                "datetime": dt,
                "symbol": f"stock_{i:04d}",
                "prediction": preds[i],
                "label": labels[i],
            })
    return pd.DataFrame(rows)


class TestComputeICSeries:
    def test_positive_ic_with_correlated_data(self):
        df = _make_oos_df()
        ic = compute_ic_series(df)
        assert len(ic) == 50
        assert ic.mean() > 0.1  # correlated data → positive IC

    def test_zero_ic_with_random_data(self):
        df = _make_oos_df()
        rng = np.random.RandomState(99)
        df["prediction"] = rng.randn(len(df))
        ic = compute_ic_series(df)
        assert abs(ic.mean()) < 0.15  # random → near zero

    def test_small_group_returns_nan(self):
        df = _make_oos_df(n_days=1, n_stocks=5)  # too few stocks
        ic = compute_ic_series(df)
        assert ic.isna().all()

    def test_handles_nan_in_predictions(self):
        df = _make_oos_df(n_days=10, n_stocks=50)
        # Inject NaN
        df.loc[df.index[:20], "prediction"] = np.nan
        ic = compute_ic_series(df)
        assert not ic.isna().all()


class TestComputeTopBottomReturn:
    def test_positive_spread_with_correlated_data(self):
        df = _make_oos_df()
        tb = compute_top_bottom_return(df)
        assert tb["spread_daily"] > 0
        assert tb["top_mean_daily"] > tb["bottom_mean_daily"]

    def test_annual_scaling(self):
        df = _make_oos_df()
        tb = compute_top_bottom_return(df)
        assert abs(tb["spread_ann"] - tb["spread_daily"] * 243) < 1e-10

    def test_custom_quantile(self):
        df = _make_oos_df()
        tb_10 = compute_top_bottom_return(df, quantile=0.1)
        tb_20 = compute_top_bottom_return(df, quantile=0.2)
        # Wider quantile → less extreme selection → smaller spread
        assert abs(tb_10["spread_daily"]) >= abs(tb_20["spread_daily"]) * 0.5

    def test_too_few_stocks_returns_nan(self):
        df = _make_oos_df(n_days=5, n_stocks=10)
        tb = compute_top_bottom_return(df)
        assert np.isnan(tb["spread_daily"])  # <20 stocks


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
