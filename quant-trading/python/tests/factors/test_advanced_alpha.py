"""Tests for advanced_alpha module."""

import numpy as np
import pandas as pd
import pytest

from aipha.factors.advanced_alpha import (
    add_advanced_alpha_factors,
    add_residual_momentum,
    add_value_profitability_composite,
)


def _make_sample_df(n_dates: int = 30, n_stocks: int = 50) -> pd.DataFrame:
    """创建测试用 DataFrame。"""
    np.random.seed(42)
    rows = []
    for d in range(n_dates):
        dt = f"2024-01-{d + 1:02d}"
        for s in range(n_stocks):
            rows.append({
                "datetime": dt,
                "symbol": f"{s:06d}.SZ",
                "pct_chg": np.random.randn() * 2,
                "pe_ttm": np.random.uniform(5, 100),
                "pb": np.random.uniform(0.5, 10),
                "dv_ttm": np.random.uniform(0, 5),
                "circ_mv": np.random.uniform(1e4, 1e6),
            })
    return pd.DataFrame(rows)


class TestResidualMomentum:
    """残差动量因子测试组。"""

    def test_adds_columns(self):
        df = _make_sample_df()
        n = add_residual_momentum(df)
        assert n == 4
        assert "res_momentum_20d" in df.columns
        assert "cs_rank_res_mom" in df.columns

    def test_no_intermediate_cols(self):
        df = _make_sample_df()
        add_residual_momentum(df)
        assert "residual_ret" not in df.columns

    def test_missing_return_col(self):
        df = _make_sample_df().drop(columns=["pct_chg"])
        n = add_residual_momentum(df)
        assert n == 0

    def test_rank_range(self):
        df = _make_sample_df()
        add_residual_momentum(df)
        valid = df["cs_rank_res_mom"].dropna()
        assert valid.min() > 0
        assert valid.max() <= 1.0


class TestValueProfitComposite:
    """价值盈利复合因子测试组。"""

    def test_adds_columns(self):
        df = _make_sample_df()
        n = add_value_profitability_composite(df)
        assert n == 2
        assert "value_profit_composite" in df.columns
        assert "cs_rank_val_prof" in df.columns

    def test_missing_all_inputs(self):
        df = _make_sample_df().drop(columns=["pe_ttm", "pb", "dv_ttm"])
        n = add_value_profitability_composite(df)
        assert n == 0

    def test_composite_range(self):
        df = _make_sample_df()
        add_value_profitability_composite(df)
        valid = df["cs_rank_val_prof"].dropna()
        assert valid.min() > 0
        assert valid.max() <= 1.0


class TestAddAdvancedAlpha:
    """统一入口测试。"""

    def test_combined(self):
        df = _make_sample_df()
        n = add_advanced_alpha_factors(df)
        assert n >= 4  # 至少 residual_momentum(4) + value_profit(2)
