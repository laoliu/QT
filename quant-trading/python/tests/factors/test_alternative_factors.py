"""替代数据因子计算模块单元测试。

覆盖: compute_chips_factors, compute_flow_factors,
       compute_analyst_factors, compute_margin_factors,
       merge_alternative_factors
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.factors.alternative_factors import (
    compute_analyst_factors,
    compute_chips_factors,
    compute_flow_factors,
    compute_margin_factors,
    merge_alternative_factors,
)


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture
def chips_raw() -> pd.DataFrame:
    """筹码分布原始数据 (cyq_perf 格式)。"""
    return pd.DataFrame({
        "ts_code": ["000001.SZ"] * 5 + ["000002.SZ"] * 5,
        "trade_date": (
            ["20240101", "20240102", "20240103",
             "20240104", "20240105"] * 2
        ),
        "cost_5pct": [8.0, 8.1, 8.2, 8.3, 8.4] * 2,
        "cost_15pct": [8.5, 8.6, 8.7, 8.8, 8.9] * 2,
        "cost_50pct": [9.5, 9.6, 9.7, 9.8, 9.9] * 2,
        "cost_85pct": [10.5, 10.6, 10.7, 10.8, 10.9] * 2,
        "cost_95pct": [11.0, 11.1, 11.2, 11.3, 11.4] * 2,
        "winner_rate": [0.6, 0.55, 0.5, 0.45, 0.4] * 2,
    })


@pytest.fixture
def flow_raw() -> pd.DataFrame:
    """资金流原始数据 (moneyflow 格式)。"""
    return pd.DataFrame({
        "ts_code": ["000001.SZ"] * 5,
        "trade_date": [
            "20240101", "20240102", "20240103",
            "20240104", "20240105",
        ],
        "buy_sm_vol": [100, 120, 80, 90, 110],
        "sell_sm_vol": [90, 100, 110, 80, 100],
        "buy_md_vol": [200, 210, 190, 200, 220],
        "sell_md_vol": [180, 200, 210, 190, 200],
        "buy_lg_vol": [500, 520, 480, 510, 530],
        "sell_lg_vol": [450, 500, 520, 480, 490],
        "buy_elg_vol": [300, 310, 280, 320, 350],
        "sell_elg_vol": [280, 290, 310, 300, 310],
        "net_mf_vol": [100, 70, -120, 70, 110],
    })


@pytest.fixture
def margin_raw() -> pd.DataFrame:
    """融资融券原始数据 (margin_detail 格式)。"""
    return pd.DataFrame({
        "ts_code": ["000001.SZ"] * 5,
        "trade_date": [
            "20240101", "20240102", "20240103",
            "20240104", "20240105",
        ],
        "rzye": [1e8, 1.1e8, 1.05e8, 1.15e8, 1.2e8],
        "rzmre": [5e6, 6e6, 4e6, 7e6, 8e6],
        "rzche": [3e6, 4e6, 5e6, 3e6, 4e6],
        "rqye": [2e7, 2.1e7, 2.2e7, 2.0e7, 1.9e7],
        "rqmcl": [1e6, 1.2e6, 1.5e6, 0.8e6, 1.0e6],
        "rqchl": [0.8e6, 1.0e6, 1.1e6, 1.2e6, 0.9e6],
        "rzrqye": [1.2e8, 1.31e8, 1.27e8, 1.35e8, 1.39e8],
    })


@pytest.fixture
def prices_df() -> pd.DataFrame:
    """价格主表 (用于 merge)。"""
    return pd.DataFrame({
        "symbol": ["000001.SZ"] * 5,
        "datetime": pd.to_datetime([
            "2024-01-01", "2024-01-02", "2024-01-03",
            "2024-01-04", "2024-01-05",
        ]),
        "close": [10.0, 10.2, 9.8, 10.1, 10.3],
    })


# ── chips_factors 测试 ────────────────────────────────────────

class TestChipsFactors:
    """筹码因子计算测试。"""

    def test_basic_output_shape(self, chips_raw):
        result = compute_chips_factors(chips_raw)
        assert len(result) == 10
        assert "chips_winner_rate" in result.columns
        assert "chips_cost_concentration" in result.columns
        assert "chips_cost_skew" in result.columns
        assert "chips_trapped_ratio" in result.columns

    def test_winner_rate_values(self, chips_raw):
        result = compute_chips_factors(chips_raw)
        assert result["chips_winner_rate"].iloc[0] == pytest.approx(
            0.6, abs=0.01,
        )

    def test_trapped_ratio_complement(self, chips_raw):
        result = compute_chips_factors(chips_raw)
        wr = result["chips_winner_rate"].values
        tr = result["chips_trapped_ratio"].values
        np.testing.assert_allclose(wr + tr, 1.0, atol=1e-6)

    def test_cost_concentration_positive(self, chips_raw):
        result = compute_chips_factors(chips_raw)
        assert (result["chips_cost_concentration"] > 0).all()

    def test_empty_input(self):
        result = compute_chips_factors(pd.DataFrame())
        assert result.empty

    def test_column_rename(self, chips_raw):
        """ts_code→symbol, trade_date→datetime 重命名。"""
        result = compute_chips_factors(chips_raw)
        assert "symbol" in result.columns
        assert "datetime" in result.columns
        assert "ts_code" not in result.columns


# ── flow_factors 测试 ─────────────────────────────────────────

class TestFlowFactors:
    """资金流因子计算测试。"""

    def test_basic_output_shape(self, flow_raw):
        result = compute_flow_factors(flow_raw)
        assert len(result) == 5
        assert "flow_smart_money" in result.columns
        assert "flow_retail" in result.columns
        assert "flow_divergence" in result.columns
        assert "flow_persistence" in result.columns
        assert "flow_buy_ratio" in result.columns

    def test_smart_money_sign(self, flow_raw):
        """大单净流入为正时 smart_money 应为正。"""
        result = compute_flow_factors(flow_raw)
        assert result["flow_smart_money"].iloc[0] > 0

    def test_buy_ratio_range(self, flow_raw):
        result = compute_flow_factors(flow_raw)
        assert (result["flow_buy_ratio"] >= 0).all()
        assert (result["flow_buy_ratio"] <= 1).all()

    def test_empty_input(self):
        result = compute_flow_factors(pd.DataFrame())
        assert result.empty

    def test_divergence_values(self, flow_raw):
        """分歧值在 {-2, -1, 0, 1, 2} 范围内。"""
        result = compute_flow_factors(flow_raw)
        valid_vals = {-2.0, -1.0, 0.0, 1.0, 2.0}
        for v in result["flow_divergence"].dropna():
            assert v in valid_vals


# ── margin_factors 测试 ───────────────────────────────────────

class TestMarginFactors:
    """融资融券因子计算测试。"""

    def test_basic_output_shape(self, margin_raw):
        result = compute_margin_factors(margin_raw)
        assert len(result) == 5
        assert "margin_balance_ratio" in result.columns
        assert "margin_buy_intensity" in result.columns
        assert "margin_short_pressure" in result.columns
        assert "margin_net_momentum" in result.columns

    def test_balance_ratio_range(self, margin_raw):
        result = compute_margin_factors(margin_raw)
        br = result["margin_balance_ratio"]
        assert (br >= 0).all()
        assert (br <= 1.1).all()

    def test_short_pressure_nonneg(self, margin_raw):
        result = compute_margin_factors(margin_raw)
        assert (result["margin_short_pressure"] >= 0).all()

    def test_buy_intensity_positive(self, margin_raw):
        result = compute_margin_factors(margin_raw)
        assert (result["margin_buy_intensity"] > 0).all()

    def test_empty_input(self):
        result = compute_margin_factors(pd.DataFrame())
        assert result.empty

    def test_column_rename(self, margin_raw):
        result = compute_margin_factors(margin_raw)
        assert "symbol" in result.columns
        assert "datetime" in result.columns

    def test_momentum_computation(self, margin_raw):
        """净买入动量应非零（融资买入>偿还）。"""
        result = compute_margin_factors(margin_raw)
        assert not result["margin_net_momentum"].isna().all()

    def test_balance_plus_short_approx_one(self, margin_raw):
        """融资+融券占比之和应接近1。"""
        result = compute_margin_factors(margin_raw)
        total = (
            result["margin_balance_ratio"]
            + result["margin_short_pressure"]
        )
        np.testing.assert_allclose(total, 1.0, atol=0.05)


# ── merge 测试 ────────────────────────────────────────────────

class TestMergeFactors:
    """替代因子合并测试。"""

    def test_merge_all_sources(
        self, prices_df, chips_raw, flow_raw, margin_raw,
    ):
        chips = compute_chips_factors(chips_raw)
        flow = compute_flow_factors(flow_raw)
        margin = compute_margin_factors(margin_raw)
        result = merge_alternative_factors(
            prices_df,
            chips_factors=chips,
            flow_factors=flow,
            margin_factors=margin,
        )
        assert len(result) >= len(prices_df)
        alt_cols = [
            c for c in result.columns
            if c.startswith(("chips_", "flow_", "margin_"))
        ]
        assert len(alt_cols) >= 10

    def test_merge_none_sources(self, prices_df):
        result = merge_alternative_factors(prices_df)
        assert len(result) == len(prices_df)

    def test_merge_partial(self, prices_df, flow_raw):
        flow = compute_flow_factors(flow_raw)
        result = merge_alternative_factors(
            prices_df, flow_factors=flow,
        )
        assert "flow_smart_money" in result.columns

    def test_merge_preserves_close(
        self, prices_df, flow_raw,
    ):
        flow = compute_flow_factors(flow_raw)
        result = merge_alternative_factors(
            prices_df, flow_factors=flow,
        )
        assert "close" in result.columns
