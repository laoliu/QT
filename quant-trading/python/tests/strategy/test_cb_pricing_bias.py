"""Tests for CB Pricing Bias Factor Strategy.

Covers:
- Pricing model utility functions
- Config defaults
- Strategy instantiation
- _extra_filter logic
- _score_candidates (6-factor ranking)
- Edge cases
"""

from __future__ import annotations

import math
import pytest
from datetime import datetime
from unittest.mock import MagicMock

from aipha.strategy.cb_pricing_bias import (
    CBPricingBiasStrategy,
    PricingBiasCBConfig,
    DEFAULT_RISK_FREE_RATE,
    DEFAULT_VOL_PROXY,
    estimate_bond_floor,
    estimate_option_time_value,
    estimate_theoretical_price,
    compute_pricing_bias,
    compute_time_value_ratio,
    _years_to_maturity,
)
from aipha.strategy.cb_rotation_base import CandidateInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candidate(
    symbol: str = "128000.SZ",
    close: float = 110.0,
    cb_over_rate: float | None = 20.0,
    cb_value: float | None = 90.0,
    issue_size: float = 50000.0,
    maturity_date: str = "20280101",
    list_date: str = "20200101",
    rating: str = "AA",
    **kwargs,
) -> CandidateInfo:
    meta = {
        "cb_over_rate": cb_over_rate,
        "cb_value": cb_value,
        "issue_size": issue_size,
        "maturity_date": maturity_date,
        "list_date": list_date,
        "rating": rating,
        "amount": kwargs.get("amount", 500.0),
    }
    meta.update(kwargs)
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata=meta,
        amount=meta["amount"],
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        maturity_date=maturity_date,
        list_date=list_date,
        rating=rating,
    )


def _make_context(dt_str: str = "2024-06-15") -> MagicMock:
    ctx = MagicMock()
    ctx.current_time = datetime.strptime(dt_str, "%Y-%m-%d")
    return ctx


# ---------------------------------------------------------------------------
# Pricing model utility tests
# ---------------------------------------------------------------------------

class TestEstimateBondFloor:
    """纯债价值估算测试。"""

    def test_zero_years(self):
        bf = estimate_bond_floor(0.0)
        assert bf == 100.0

    def test_positive_years(self):
        bf = estimate_bond_floor(5.0, risk_free_rate=0.03)
        assert 80.0 < bf < 100.0

    def test_higher_rate_lower_floor(self):
        bf_low = estimate_bond_floor(5.0, risk_free_rate=0.02)
        bf_high = estimate_bond_floor(5.0, risk_free_rate=0.05)
        assert bf_high < bf_low

    def test_longer_maturity_lower_floor(self):
        bf_short = estimate_bond_floor(2.0)
        bf_long = estimate_bond_floor(10.0)
        assert bf_long < bf_short

    def test_coupon_contributes(self):
        # 有票息 vs 无票息
        bf_coupon = estimate_bond_floor(5.0, coupon_rate=0.01)
        bf_zero = estimate_bond_floor(5.0, coupon_rate=0.0)
        assert bf_coupon > bf_zero


class TestEstimateOptionTimeValue:
    """期权时间价值估算测试。"""

    def test_zero_cb_value(self):
        tv = estimate_option_time_value(0.0, 3.0)
        assert tv == 0.0

    def test_zero_years(self):
        tv = estimate_option_time_value(100.0, 0.0)
        assert tv == 0.0

    def test_positive_value(self):
        tv = estimate_option_time_value(100.0, 3.0)
        assert tv > 0.0

    def test_longer_maturity_higher_value(self):
        tv_short = estimate_option_time_value(100.0, 1.0)
        tv_long = estimate_option_time_value(100.0, 5.0)
        assert tv_long > tv_short

    def test_higher_vol_higher_value(self):
        tv_low = estimate_option_time_value(100.0, 3.0, vol_proxy=0.15)
        tv_high = estimate_option_time_value(100.0, 3.0, vol_proxy=0.45)
        assert tv_high > tv_low


class TestEstimateTheoreticalPrice:
    """理论价格估算测试。"""

    def test_positive_result(self):
        tp = estimate_theoretical_price(90.0, 3.0)
        assert tp > 0.0

    def test_above_both_components(self):
        """理论价应高于 max(bond_floor, cb_value)。"""
        bf = estimate_bond_floor(5.0)
        cb_value = 90.0
        tp = estimate_theoretical_price(cb_value, 5.0)
        assert tp >= max(bf, cb_value)

    def test_zero_cb_value(self):
        tp = estimate_theoretical_price(0.0, 5.0)
        assert tp > 0.0  # still = bond_floor + 0


class TestComputePricingBias:
    """定价偏差率计算测试。"""

    def test_underpriced(self):
        """市场价低于理论价 → 正偏差。"""
        # 低价 CB with 高转股价值 and 长剩余年限 → 理论价高
        bias = compute_pricing_bias(85.0, 100.0, 5.0)
        assert bias > 0.0

    def test_overpriced(self):
        """市场价远高于理论价 → 负偏差。"""
        bias = compute_pricing_bias(200.0, 50.0, 1.0)
        assert bias < 0.0

    def test_zero_price(self):
        bias = compute_pricing_bias(0.0, 100.0, 3.0)
        assert bias == 0.0


class TestComputeTimeValueRatio:
    """时间价值比计算测试。"""

    def test_positive(self):
        tvr = compute_time_value_ratio(110.0, 90.0, 3.0)
        assert tvr > 0.0

    def test_zero_price(self):
        tvr = compute_time_value_ratio(0.0, 90.0, 3.0)
        assert tvr == 0.0

    def test_zero_cb_value(self):
        tvr = compute_time_value_ratio(100.0, 0.0, 3.0)
        assert tvr == 0.0


class TestYearsToMaturity:
    """剩余年限计算测试。"""

    def test_normal_date(self):
        now = datetime(2024, 6, 15)
        years = _years_to_maturity("20280101", now)
        assert 3.0 < years < 4.0

    def test_empty_date(self):
        years = _years_to_maturity("", datetime(2024, 6, 15))
        assert years == 3.0

    def test_past_date(self):
        years = _years_to_maturity("20200101", datetime(2024, 6, 15))
        assert years == 0.01  # clipped


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestPricingBiasCBConfig:
    """配置测试。"""

    def test_default_weights_sum_to_one(self):
        cfg = PricingBiasCBConfig()
        total = (
            cfg.weight_pricing_bias + cfg.weight_time_value
            + cfg.weight_price + cfg.weight_premium
            + cfg.weight_rating + cfg.weight_issue_size
        )
        assert abs(total - 1.0) < 1e-9

    def test_default_risk_free_rate(self):
        cfg = PricingBiasCBConfig()
        assert cfg.risk_free_rate == DEFAULT_RISK_FREE_RATE

    def test_default_vol_proxy(self):
        cfg = PricingBiasCBConfig()
        assert cfg.vol_proxy == DEFAULT_VOL_PROXY


# ---------------------------------------------------------------------------
# Strategy tests
# ---------------------------------------------------------------------------

class TestCBPricingBiasInstantiation:
    """策略实例化测试。"""

    def test_default_init(self):
        s = CBPricingBiasStrategy()
        assert s.name == "cb_pricing_bias"

    def test_custom_config(self):
        cfg = PricingBiasCBConfig(top_k=10)
        s = CBPricingBiasStrategy(config=cfg)
        assert s.config.top_k == 10


class TestPricingBiasExtraFilter:
    """过滤器测试。"""

    def test_passes_normal(self):
        s = CBPricingBiasStrategy()
        ctx = _make_context()
        c = _make_candidate()
        assert s._extra_filter(c, ctx) is True

    def test_rejects_high_premium(self):
        s = CBPricingBiasStrategy()
        ctx = _make_context()
        c = _make_candidate(cb_over_rate=120.0)
        assert s._extra_filter(c, ctx) is False

    def test_rejects_new_bond(self):
        s = CBPricingBiasStrategy()
        ctx = _make_context("2024-06-15")
        c = _make_candidate(list_date="20240614")
        assert s._extra_filter(c, ctx) is False


class TestPricingBiasScoreCandidates:
    """评分测试。"""

    def test_empty(self):
        s = CBPricingBiasStrategy()
        ctx = _make_context()
        assert s._score_candidates([], ctx) == []

    def test_single(self):
        s = CBPricingBiasStrategy()
        ctx = _make_context()
        c = _make_candidate(symbol="A")
        assert s._score_candidates([c], ctx) == ["A"]

    def test_returns_top_k(self):
        cfg = PricingBiasCBConfig(top_k=3)
        s = CBPricingBiasStrategy(config=cfg)
        ctx = _make_context()
        candidates = [
            _make_candidate(symbol=f"C{i}", close=100 + i * 5)
            for i in range(8)
        ]
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_underpriced_preferred(self):
        """定价偏差大（被低估多）的 CB 应排名更高。"""
        cfg = PricingBiasCBConfig(
            top_k=1,
            weight_pricing_bias=1.0,
            weight_time_value=0.0,
            weight_price=0.0,
            weight_premium=0.0,
            weight_rating=0.0,
            weight_issue_size=0.0,
        )
        s = CBPricingBiasStrategy(config=cfg)
        ctx = _make_context()
        # 低价高转股 → 大正偏差
        underpriced = _make_candidate(
            symbol="CHEAP", close=85.0, cb_value=100.0, maturity_date="20290101",
        )
        # 高价低转股 → 负偏差
        overpriced = _make_candidate(
            symbol="EXPENSIVE", close=150.0, cb_value=60.0, maturity_date="20250601",
        )
        result = s._score_candidates([underpriced, overpriced], ctx)
        assert result[0] == "CHEAP"

    def test_missing_cb_value(self):
        """cb_value 为 None 时不应崩溃。"""
        s = CBPricingBiasStrategy()
        ctx = _make_context()
        c = _make_candidate(cb_value=None)
        result = s._score_candidates([c], ctx)
        assert len(result) == 1

    def test_missing_maturity(self):
        """maturity_date 为空时使用默认值。"""
        s = CBPricingBiasStrategy()
        ctx = _make_context()
        c = _make_candidate(maturity_date="")
        result = s._score_candidates([c], ctx)
        assert len(result) == 1

    def test_all_same(self):
        """所有候选相同时不应崩溃。"""
        cfg = PricingBiasCBConfig(top_k=2)
        s = CBPricingBiasStrategy(config=cfg)
        ctx = _make_context()
        candidates = [_make_candidate(symbol=f"S{i}") for i in range(4)]
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 2


class TestPricingBiasRegistration:
    """注册表测试。"""

    def test_registered_name(self):
        from aipha.strategy.registry import StrategyRegistry
        reg = StrategyRegistry()
        assert "cb_pricing_bias" in reg.list_strategies()
