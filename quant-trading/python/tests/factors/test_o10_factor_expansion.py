"""O10 因子扩展模块测试 — northbound_factors + industry_relative。

覆盖:
- 北向因子: _standardize_columns, _compute_net_buy_ratio,
  _compute_concentration, _compute_momentum, _compute_rank_score,
  compute_northbound_factors (含聚合/空输入)
- 行业相对因子: _detect_factor_cols, _adjust_single_factor,
  compute_industry_relative (含无日期/空输入/小行业)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ===== Northbound factors =================================================

class TestStandardizeColumns:
    """测试 _standardize_columns。"""

    def test_rename_ts_code_and_trade_date(self):
        from aipha.factors.northbound_factors import _standardize_columns
        df = pd.DataFrame({
            "ts_code": ["600519.SH"], "trade_date": ["20240101"],
            "amount": [1e8],
        })
        out = _standardize_columns(df)
        assert "symbol" in out.columns
        assert "datetime" in out.columns
        assert out["symbol"].iloc[0] == "600519.SH"

    def test_already_standard(self):
        from aipha.factors.northbound_factors import _standardize_columns
        df = pd.DataFrame({
            "symbol": ["600519.SH"],
            "datetime": pd.to_datetime(["2024-01-01"]),
            "amount": [1e8],
        })
        out = _standardize_columns(df)
        assert list(out.columns) == list(df.columns)


class TestNetBuyRatio:
    """测试 _compute_net_buy_ratio。"""

    def test_basic(self):
        from aipha.factors.northbound_factors import _compute_net_buy_ratio
        df = pd.DataFrame({
            "symbol": ["A", "B"],
            "datetime": pd.to_datetime(["2024-01-01", "2024-01-01"]),
            "amount": [1e8, 2e8],
            "net_amount": [5e7, -1e8],
        })
        out = _compute_net_buy_ratio(df)
        assert "nb_net_buy_ratio" in out.columns
        assert np.isclose(out["nb_net_buy_ratio"].iloc[0], 0.5)
        assert np.isclose(out["nb_net_buy_ratio"].iloc[1], -0.5)

    def test_zero_amount(self):
        from aipha.factors.northbound_factors import _compute_net_buy_ratio
        df = pd.DataFrame({
            "symbol": ["A"],
            "datetime": pd.to_datetime(["2024-01-01"]),
            "amount": [0.0],
            "net_amount": [100.0],
        })
        out = _compute_net_buy_ratio(df)
        assert pd.isna(out["nb_net_buy_ratio"].iloc[0])


class TestConcentration:
    """测试 _compute_concentration。"""

    def test_basic_lookback(self):
        from aipha.factors.northbound_factors import _compute_concentration
        dates = pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 6)])
        df = pd.DataFrame({
            "symbol": ["A"] * 5,
            "datetime": dates,
        })
        out = _compute_concentration(df, lookback=5)
        # 第 5 天出现了 5/5 = 1.0
        assert out["nb_concentration"].iloc[-1] == pytest.approx(1.0)

    def test_single_day(self):
        from aipha.factors.northbound_factors import _compute_concentration
        df = pd.DataFrame({
            "symbol": ["A"],
            "datetime": pd.to_datetime(["2024-01-01"]),
        })
        out = _compute_concentration(df, lookback=20)
        assert out["nb_concentration"].iloc[0] == pytest.approx(1 / 20)


class TestMomentum:
    """测试 _compute_momentum。"""

    def test_all_positive(self):
        from aipha.factors.northbound_factors import _compute_momentum
        dates = pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 6)])
        df = pd.DataFrame({
            "symbol": ["A"] * 5,
            "datetime": dates,
            "net_amount": [100, 200, 50, 300, 150],  # all positive
        })
        out = _compute_momentum(df, window=5)
        assert out["nb_momentum_5d"].iloc[-1] == pytest.approx(1.0)

    def test_mixed_sign(self):
        from aipha.factors.northbound_factors import _compute_momentum
        dates = pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 6)])
        df = pd.DataFrame({
            "symbol": ["A"] * 5,
            "datetime": dates,
            "net_amount": [100, -200, 50, -300, 150],  # 3 pos, 2 neg
        })
        out = _compute_momentum(df, window=5)
        # mean([1, -1, 1, -1, 1]) = 0.2
        assert out["nb_momentum_5d"].iloc[-1] == pytest.approx(0.2)


class TestRankScore:
    """测试 _compute_rank_score。"""

    def test_rank_values(self):
        from aipha.factors.northbound_factors import _compute_rank_score
        df = pd.DataFrame({
            "symbol": ["A", "B", "C"],
            "datetime": pd.to_datetime(["2024-01-01"] * 3),
            "rank": [1, 5, 10],
        })
        out = _compute_rank_score(df)
        assert out["nb_rank_score"].iloc[0] == pytest.approx(1.0)
        assert out["nb_rank_score"].iloc[1] == pytest.approx(0.2)
        assert out["nb_rank_score"].iloc[2] == pytest.approx(0.1)


class TestComputeNorthboundFactors:
    """测试主函数 compute_northbound_factors。"""

    def test_empty_input(self):
        from aipha.factors.northbound_factors import compute_northbound_factors
        out = compute_northbound_factors(pd.DataFrame())
        assert out.empty

    def test_missing_columns(self):
        from aipha.factors.northbound_factors import compute_northbound_factors
        df = pd.DataFrame({"ts_code": ["A"], "trade_date": ["20240101"]})
        out = compute_northbound_factors(df)
        assert out.empty

    def test_full_pipeline(self):
        from aipha.factors.northbound_factors import compute_northbound_factors
        dates = pd.to_datetime([f"2024-01-{d:02d}" for d in range(1, 4)])
        rows = []
        for d in dates:
            for sym, rnk in [("A", 1), ("B", 3)]:
                rows.append({
                    "ts_code": sym, "trade_date": d.strftime("%Y%m%d"),
                    "amount": 1e8, "net_amount": 5e7 if sym == "A" else -2e7,
                    "buy": 7e7 if sym == "A" else 4e7,
                    "sell": 2e7 if sym == "A" else 6e7,
                    "rank": rnk,
                })
        df = pd.DataFrame(rows)
        out = compute_northbound_factors(df)
        assert len(out) > 0
        expected_cols = {"symbol", "datetime", "nb_net_buy_ratio",
                         "nb_buy_intensity", "nb_concentration",
                         "nb_momentum_5d", "nb_rank_score"}
        assert expected_cols == set(out.columns)

    def test_aggregation_multi_market(self):
        from aipha.factors.northbound_factors import compute_northbound_factors
        df = pd.DataFrame({
            "ts_code": ["A", "A"],
            "trade_date": ["20240101", "20240101"],
            "amount": [1e8, 2e8],
            "net_amount": [3e7, 7e7],
            "buy": [6e7, 1.2e8],
            "sell": [4e7, 5e7],
            "rank": [1, 2],
            "market_type": ["1", "3"],  # 沪+深
        })
        out = compute_northbound_factors(df)
        # 聚合: amount=3e8, net_amount=1e8
        assert len(out) == 1
        assert out["nb_net_buy_ratio"].iloc[0] == pytest.approx(1e8 / 3e8)


# ===== Industry relative factors ==========================================

class TestDetectFactorCols:
    """测试 _detect_factor_cols。"""

    def test_detects_numeric(self):
        from aipha.factors.industry_relative import _detect_factor_cols
        df = pd.DataFrame({
            "symbol": ["A"], "industry": ["bank"],
            "pe": [10.0], "pb": [1.5], "name": ["foo"],
        })
        cols = _detect_factor_cols(df, "industry")
        assert "pe" in cols
        assert "pb" in cols
        assert "name" not in cols

    def test_skips_id_cols(self):
        from aipha.factors.industry_relative import _detect_factor_cols
        df = pd.DataFrame({
            "symbol": ["A"], "datetime": [1], "industry": ["bank"],
            "pe": [10.0],
        })
        cols = _detect_factor_cols(df, "industry")
        assert "pe" in cols
        assert "symbol" not in cols
        assert "datetime" not in cols


class TestAdjustSingleFactor:
    """测试 _adjust_single_factor。"""

    def test_basic_adjustment(self):
        from aipha.factors.industry_relative import _adjust_single_factor
        df = pd.DataFrame({"pe": [10.0, 12.0, 14.0, 16.0, 18.0]})
        out = _adjust_single_factor(df, "pe", min_size=3)
        assert "pe_ind_rel" in out.columns
        assert "pe_ind_rank" in out.columns
        # median = 14, 所以 pe=14 的 _ind_rel 应为 0
        assert out.loc[out["pe"] == 14.0, "pe_ind_rel"].iloc[0] == pytest.approx(0.0)

    def test_small_group_returns_nan(self):
        from aipha.factors.industry_relative import _adjust_single_factor
        df = pd.DataFrame({"pe": [10.0, 12.0]})
        out = _adjust_single_factor(df, "pe", min_size=5)
        assert out["pe_ind_rel"].isna().all()

    def test_constant_factor(self):
        from aipha.factors.industry_relative import _adjust_single_factor
        df = pd.DataFrame({"pe": [10.0] * 6})
        out = _adjust_single_factor(df, "pe", min_size=3)
        # all same → median=10, deviation=0 → all rel should be 0
        assert (out["pe_ind_rel"] == 0.0).all()


class TestComputeIndustryRelative:
    """测试主函数 compute_industry_relative。"""

    def test_empty_input(self):
        from aipha.factors.industry_relative import compute_industry_relative
        out = compute_industry_relative(pd.DataFrame())
        assert out.empty

    def test_missing_industry_col(self):
        from aipha.factors.industry_relative import compute_industry_relative
        df = pd.DataFrame({"symbol": ["A"], "pe": [10.0]})
        out = compute_industry_relative(df)
        # 缺少 industry 列, 应返回原 df 不含新列
        assert "pe_ind_rel" not in out.columns

    def test_full_pipeline(self):
        from aipha.factors.industry_relative import compute_industry_relative
        np.random.seed(42)
        n = 30
        df = pd.DataFrame({
            "symbol": [f"S{i}" for i in range(n)],
            "datetime": pd.to_datetime(["2024-01-01"] * n),
            "industry": (["bank"] * 10 + ["tech"] * 10 + ["energy"] * 10),
            "pe": np.random.uniform(5, 30, n),
            "pb": np.random.uniform(0.5, 5, n),
        })
        out = compute_industry_relative(df, factor_cols=["pe", "pb"])
        assert "pe_ind_rel" in out.columns
        assert "pe_ind_rank" in out.columns
        assert "pb_ind_rel" in out.columns
        assert len(out) == n

    def test_no_date_col(self):
        from aipha.factors.industry_relative import compute_industry_relative
        df = pd.DataFrame({
            "symbol": ["A", "B", "C", "D", "E", "F"],
            "industry": ["bank"] * 3 + ["tech"] * 3,
            "pe": [10, 12, 14, 20, 25, 30],
        })
        out = compute_industry_relative(df, factor_cols=["pe"])
        assert "pe_ind_rel" in out.columns
        assert len(out) == 6
