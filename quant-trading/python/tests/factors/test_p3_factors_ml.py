"""R128 P3 制度防御因子单元测试.

覆盖:
  - add_p3_factors 基本输出
  - 各因子在有效字段下正确计算
  - 缺失字段时优雅降级
  - cs_rank 范围 [0, 1]
  - 因子名称一致性
  - 返回列数
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.factors.p3_factors_ml import add_p3_factors, P3_FACTOR_NAMES


def _make_panel(
    n_dates: int = 300,
    n_symbols: int = 50,
    seed: int = 42,
    include_fina: bool = True,
) -> pd.DataFrame:
    """构造测试面板数据."""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2022-01-01", periods=n_dates)
    symbols = [f"S{i:03d}" for i in range(n_symbols)]

    rows = []
    for sym in symbols:
        base = 10 + rng.rand() * 50
        for i, d in enumerate(dates):
            ret = rng.randn() * 0.02
            c = base * np.exp(ret * (i + 1) * 0.001 + ret)
            rows.append({
                "datetime": d, "symbol": sym,
                "close": round(max(c, 0.5), 2),
                "turnover_rate": max(rng.exponential(3), 0.01),
                "pe_ttm": rng.uniform(5, 100),
            })
    df = pd.DataFrame(rows)

    if include_fina:
        df["roe"] = rng.uniform(-5, 40, len(df))
        df["grossprofit_margin"] = rng.uniform(5, 80, len(df))
        df["debt_to_assets"] = rng.uniform(10, 90, len(df))
        df["dt_netprofit_yoy"] = rng.uniform(-50, 100, len(df))
        df["eps"] = rng.uniform(-1, 5, len(df))
        df["cfps"] = rng.uniform(-2, 6, len(df))
    return df


class TestP3FactorsBasic:
    """P3 因子基本输出测试."""

    def test_basic_output(self):
        df = _make_panel()
        n = add_p3_factors(df)
        assert n == 10  # 5 raw + 5 cs_rank
        for name in P3_FACTOR_NAMES:
            assert name in df.columns, f"{name} missing"

    def test_no_nan_in_warm_period(self):
        df = _make_panel(n_dates=300)
        add_p3_factors(df)
        # After 252-day warmup, should have values
        warm = df[df["datetime"] >= df["datetime"].min()
                  + pd.Timedelta(days=365)]
        for name in ["quality_composite", "value_quality"]:
            pct_valid = warm[name].notna().mean()
            assert pct_valid > 0.8, f"{name}: {pct_valid:.1%} valid"

    def test_cs_rank_range(self):
        df = _make_panel()
        add_p3_factors(df)
        for name in P3_FACTOR_NAMES:
            if name.startswith("cs_rank_"):
                valid = df[name].dropna()
                if len(valid) > 0:
                    assert valid.min() >= 0
                    assert valid.max() <= 1.0


class TestQualityComposite:
    """quality_composite 专项测试."""

    def test_high_roe_high_margin_scores_high(self):
        """高ROE+高毛利+低负债 → 高quality_composite."""
        df = _make_panel(n_dates=100, n_symbols=20)
        # 人为设置 S000 为高质量
        mask = df["symbol"] == "S000"
        df.loc[mask, "roe"] = 35.0
        df.loc[mask, "grossprofit_margin"] = 70.0
        df.loc[mask, "debt_to_assets"] = 15.0
        df.loc[mask, "dt_netprofit_yoy"] = 50.0
        add_p3_factors(df)
        last_day = df["datetime"].max()
        day_data = df[df["datetime"] == last_day]
        s000 = day_data[day_data["symbol"] == "S000"]["cs_rank_quality_composite"]
        assert s000.iloc[0] > 0.6  # 高质量应排名靠前


class TestLowBeta:
    """low_beta 专项测试."""

    def test_beta_values_reasonable(self):
        df = _make_panel(n_dates=300)
        add_p3_factors(df)
        warm = df[df["datetime"] >= df["datetime"].min()
                  + pd.Timedelta(days=365)]
        valid = warm["low_beta"].dropna()
        # low_beta = -beta, 应在 [-5, 3] 范围内
        assert valid.min() >= -5.5
        assert valid.max() <= 3.5


class TestValueQuality:
    """value_quality 交互因子测试."""

    def test_cheap_good_stock(self):
        """低PE+高ROE → 高 value_quality."""
        df = _make_panel(n_dates=100, n_symbols=20)
        mask = df["symbol"] == "S000"
        df.loc[mask, "pe_ttm"] = 8.0   # 便宜
        df.loc[mask, "roe"] = 30.0      # 好
        add_p3_factors(df)
        last_day = df["datetime"].max()
        day_data = df[df["datetime"] == last_day]
        s000 = day_data[day_data["symbol"] == "S000"][
            "cs_rank_value_quality"].iloc[0]
        assert s000 > 0.5


class TestEarningsStability:
    """earnings_stability 专项测试."""

    def test_stable_earnings_high_rank(self):
        df = _make_panel(n_dates=300, n_symbols=20)
        # S000: 恒定 yoy
        mask = df["symbol"] == "S000"
        df.loc[mask, "dt_netprofit_yoy"] = 20.0
        add_p3_factors(df)
        warm = df[(df["datetime"] >= df["datetime"].min()
                   + pd.Timedelta(days=365))
                  & (df["symbol"] == "S000")]
        valid = warm["earnings_stability"].dropna()
        if len(valid) > 0:
            # 恒定yoy → std≈0 → -CV≈0 → 最高
            assert valid.iloc[-1] >= -0.01  # 接近0


class TestCrowdingBeta:
    """crowding_beta 专项测试."""

    def test_crowding_beta_range(self):
        df = _make_panel(n_dates=200, n_symbols=30)
        add_p3_factors(df)
        valid = df["crowding_beta"].dropna()
        # -corr ∈ [-1, 1]
        assert valid.min() >= -1.1
        assert valid.max() <= 1.1


class TestMissingFields:
    """缺失字段优雅降级测试."""

    def test_no_fina_columns(self):
        df = _make_panel(include_fina=False)
        n = add_p3_factors(df)
        # 仍应产出 low_beta + crowding_beta = 4列
        # quality_composite仍有部分(close→safety)
        assert n >= 4

    def test_return_count(self):
        df = _make_panel()
        n = add_p3_factors(df)
        assert n == 10

    def test_factor_names_consistent(self):
        df = _make_panel()
        add_p3_factors(df)
        for name in P3_FACTOR_NAMES:
            assert name in df.columns

