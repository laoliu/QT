"""R127 P2 因子模块单元测试."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from aipha.factors.p2_factors_ml import add_p2_factors, P2_FACTOR_NAMES


def _make_panel(n_dates: int = 120, n_stocks: int = 50) -> pd.DataFrame:
    """构建测试面板数据."""
    rng = np.random.RandomState(42)
    dates = pd.bdate_range("2023-01-01", periods=n_dates)
    rows = []
    for sym_i in range(n_stocks):
        sym = f"{600000 + sym_i:06d}.SH"
        close = 10 + rng.randn(n_dates).cumsum() * 0.5
        close = np.maximum(close, 1.0)
        vol = rng.uniform(1e6, 1e8, n_dates)
        amt = close * vol * 0.01
        tr = rng.uniform(0.5, 15.0, n_dates)
        eps = rng.uniform(0.1, 3.0, n_dates)
        cfps = eps + rng.randn(n_dates) * 0.5
        roe = rng.uniform(2, 25, n_dates)
        yoy = rng.uniform(-50, 100, n_dates)
        for i, d in enumerate(dates):
            rows.append({
                "datetime": d, "symbol": sym,
                "close": close[i], "volume": vol[i],
                "amount": amt[i], "turnover_rate": tr[i],
                "eps": eps[i], "cfps": cfps[i],
                "roe": roe[i], "dt_netprofit_yoy": yoy[i],
            })
    df = pd.DataFrame(rows)
    df = df.sort_values(["symbol", "datetime"]).reset_index(drop=True)
    return df


class TestP2Factors:
    """P2 因子测试套件."""

    def setup_method(self):
        self.df = _make_panel()

    def test_basic_output(self):
        n = add_p2_factors(self.df)
        assert n == 10  # 5 因子 × 2 (raw + cs_rank)
        for name in P2_FACTOR_NAMES:
            assert name in self.df.columns

    def test_no_nan_in_warm_period(self):
        add_p2_factors(self.df)
        warm = self.df[self.df["datetime"] >= "2023-04-01"]
        for name in ["sue", "accruals", "cftp", "turnover_stability"]:
            nn = warm[name].notna().mean()
            assert nn > 0.9, f"{name} NaN rate too high: {1-nn:.1%}"

    def test_cs_rank_range(self):
        add_p2_factors(self.df)
        for name in P2_FACTOR_NAMES:
            if name.startswith("cs_rank_"):
                vals = self.df[name].dropna()
                assert vals.min() >= 0.0
                assert vals.max() <= 1.0

    def test_sue_zscore_clipped(self):
        add_p2_factors(self.df)
        sue = self.df["sue"].dropna()
        assert sue.min() >= -3.01
        assert sue.max() <= 3.01

    def test_accruals_sign(self):
        add_p2_factors(self.df)
        # 高 CFPS >> EPS → 低应计 → 正因子值
        warm = self.df[self.df["datetime"] >= "2023-04-01"]
        high_cf = warm[warm["cfps"] > warm["eps"] * 1.5]
        if len(high_cf) > 10:
            assert high_cf["accruals"].mean() > 0

    def test_cftp_positive_prices(self):
        add_p2_factors(self.df)
        cftp = self.df["cftp"].dropna()
        assert cftp.notna().sum() > 0

    def test_roe_momentum_warm(self):
        add_p2_factors(self.df)
        warm = self.df[self.df["datetime"] >= "2023-06-01"]
        nn = warm["roe_momentum"].notna().mean()
        assert nn > 0.8, f"ROE momentum NaN too high: {1-nn:.1%}"

    def test_turnover_stability_sign(self):
        add_p2_factors(self.df)
        ts = self.df["turnover_stability"].dropna()
        # 负CV → 值域应主要为负
        assert ts.mean() < 0

    def test_missing_fields_graceful(self):
        df_bare = self.df[["datetime", "symbol", "close"]].copy()
        n = add_p2_factors(df_bare)
        assert n == 0  # 无财务字段 → 0 因子

    def test_partial_fields(self):
        df_part = self.df.drop(columns=["roe", "dt_netprofit_yoy"])
        n = add_p2_factors(df_part)
        assert n >= 4  # accruals + cftp + turnover_stability

    def test_return_count(self):
        n = add_p2_factors(self.df)
        assert n == len(P2_FACTOR_NAMES)

    def test_factor_names_consistent(self):
        add_p2_factors(self.df)
        for name in P2_FACTOR_NAMES:
            assert name in self.df.columns
