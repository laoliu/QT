"""Tests for CB Credit Spread Factor Strategy.

Covers:
- rating_to_score mapping
- CreditSpreadCBConfig defaults
- CBCreditSpreadStrategy instantiation
- _extra_filter (premium, age, rating)
- _score_candidates (6-factor ranking)
- Edge cases (empty, single, all same, missing data)
- Weight normalization
- Config override
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import MagicMock

from aipha.strategy.cb_credit_spread import (
    CBCreditSpreadStrategy,
    CreditSpreadCBConfig,
    RATING_SCORE_MAP,
    DEFAULT_RATING_SCORE,
    rating_to_score,
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
        "stk_code": kwargs.get("stk_code", "000001.SZ"),
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
# rating_to_score tests
# ---------------------------------------------------------------------------

class TestRatingToScore:
    """信用评级映射测试。"""

    def test_aaa_score(self):
        assert rating_to_score("AAA") == 8.0

    def test_aa_plus_score(self):
        assert rating_to_score("AA+") == 7.0

    def test_aa_score(self):
        assert rating_to_score("AA") == 6.0

    def test_aa_minus_score(self):
        assert rating_to_score("AA-") == 5.0

    def test_a_plus_score(self):
        assert rating_to_score("A+") == 4.0

    def test_a_score(self):
        assert rating_to_score("A") == 3.0

    def test_bbb_score(self):
        assert rating_to_score("BBB") == 1.0

    def test_bb_score(self):
        assert rating_to_score("BB") == 0.0

    def test_empty_string(self):
        assert rating_to_score("") == DEFAULT_RATING_SCORE

    def test_whitespace_stripped(self):
        assert rating_to_score("  AA+  ") == 7.0

    def test_case_insensitive(self):
        assert rating_to_score("aa+") == 7.0
        assert rating_to_score("aaa") == 8.0

    def test_unknown_rating(self):
        assert rating_to_score("XYZ") == DEFAULT_RATING_SCORE

    def test_map_completeness(self):
        """所有映射值应在 [0, 8] 范围内。"""
        for score in RATING_SCORE_MAP.values():
            assert 0.0 <= score <= 8.0


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestCreditSpreadCBConfig:
    """配置测试。"""

    def test_default_weights_sum_to_one(self):
        cfg = CreditSpreadCBConfig()
        total = (
            cfg.weight_ytm + cfg.weight_rating + cfg.weight_bond_floor
            + cfg.weight_price + cfg.weight_premium + cfg.weight_issue_size
        )
        assert abs(total - 1.0) < 1e-9

    def test_default_top_k(self):
        cfg = CreditSpreadCBConfig()
        assert cfg.top_k == 20

    def test_default_rebalance(self):
        cfg = CreditSpreadCBConfig()
        assert cfg.rebalance_days == 5

    def test_default_max_price(self):
        cfg = CreditSpreadCBConfig()
        assert cfg.max_price == 150.0

    def test_default_ytm_mode(self):
        cfg = CreditSpreadCBConfig()
        assert cfg.ytm_mode == "enhanced"

    def test_custom_weights(self):
        cfg = CreditSpreadCBConfig(weight_ytm=0.40, weight_rating=0.20)
        assert cfg.weight_ytm == 0.40
        assert cfg.weight_rating == 0.20


# ---------------------------------------------------------------------------
# Strategy instantiation tests
# ---------------------------------------------------------------------------

class TestCBCreditSpreadInstantiation:
    """策略实例化测试。"""

    def test_default_init(self):
        s = CBCreditSpreadStrategy()
        assert s.name == "cb_credit_spread"
        assert isinstance(s.config, CreditSpreadCBConfig)

    def test_custom_config(self):
        cfg = CreditSpreadCBConfig(top_k=10)
        s = CBCreditSpreadStrategy(config=cfg)
        assert s.config.top_k == 10

    def test_kwargs_init(self):
        s = CBCreditSpreadStrategy(top_k=15, rebalance_days=3)
        assert s.config.top_k == 15
        assert s.config.rebalance_days == 3


# ---------------------------------------------------------------------------
# _extra_filter tests
# ---------------------------------------------------------------------------

class TestExtraFilter:
    """过滤器测试。"""

    def test_passes_normal_candidate(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        c = _make_candidate()
        assert s._extra_filter(c, ctx) is True

    def test_rejects_high_premium(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        c = _make_candidate(cb_over_rate=120.0)
        assert s._extra_filter(c, ctx) is False

    def test_passes_premium_at_limit(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        c = _make_candidate(cb_over_rate=100.0)
        assert s._extra_filter(c, ctx) is True

    def test_rejects_new_bond(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context("2024-06-15")
        c = _make_candidate(list_date="20240614")
        assert s._extra_filter(c, ctx) is False

    def test_passes_old_bond(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context("2024-06-15")
        c = _make_candidate(list_date="20200101")
        assert s._extra_filter(c, ctx) is True

    def test_rating_filter_rejects_low(self):
        cfg = CreditSpreadCBConfig(min_rating="AA-")
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        c = _make_candidate(rating="A+")  # score=4 < AA-(5)
        assert s._extra_filter(c, ctx) is False

    def test_rating_filter_passes_high(self):
        cfg = CreditSpreadCBConfig(min_rating="AA-")
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        c = _make_candidate(rating="AA")
        assert s._extra_filter(c, ctx) is True

    def test_no_rating_filter(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        c = _make_candidate(rating="A")
        assert s._extra_filter(c, ctx) is True

    def test_premium_none_passes(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        c = _make_candidate(cb_over_rate=None)
        assert s._extra_filter(c, ctx) is True

    def test_age_filter_disabled(self):
        cfg = CreditSpreadCBConfig(use_age_filter=False)
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context("2024-06-15")
        c = _make_candidate(list_date="20240614")
        assert s._extra_filter(c, ctx) is True


# ---------------------------------------------------------------------------
# _score_candidates tests
# ---------------------------------------------------------------------------

class TestScoreCandidates:
    """评分/选股测试。"""

    def test_empty_candidates(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        result = s._score_candidates([], ctx)
        assert result == []

    def test_single_candidate(self):
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        c = _make_candidate(symbol="128001.SZ")
        result = s._score_candidates([c], ctx)
        assert result == ["128001.SZ"]

    def test_returns_top_k(self):
        cfg = CreditSpreadCBConfig(top_k=3)
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        candidates = [
            _make_candidate(symbol=f"1280{i:02d}.SZ", close=100 + i * 5)
            for i in range(10)
        ]
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_low_price_preferred(self):
        """低价 CB 应排名更高。"""
        cfg = CreditSpreadCBConfig(
            top_k=1,
            weight_price=1.0,
            weight_ytm=0.0,
            weight_rating=0.0,
            weight_bond_floor=0.0,
            weight_premium=0.0,
            weight_issue_size=0.0,
        )
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        cheap = _make_candidate(symbol="CHEAP", close=90.0)
        expensive = _make_candidate(symbol="EXPENSIVE", close=140.0)
        result = s._score_candidates([cheap, expensive], ctx)
        assert result[0] == "CHEAP"

    def test_high_ytm_preferred(self):
        """高 YTM 的 CB 应排名更高。"""
        cfg = CreditSpreadCBConfig(
            top_k=1,
            weight_ytm=1.0,
            weight_price=0.0,
            weight_rating=0.0,
            weight_bond_floor=0.0,
            weight_premium=0.0,
            weight_issue_size=0.0,
        )
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        # 低价 → 高 YTM
        high_ytm = _make_candidate(symbol="HIGH_YTM", close=85.0, maturity_date="20280101")
        low_ytm = _make_candidate(symbol="LOW_YTM", close=130.0, maturity_date="20280101")
        result = s._score_candidates([high_ytm, low_ytm], ctx)
        assert result[0] == "HIGH_YTM"

    def test_high_rating_preferred(self):
        """高评级 CB 应排名更高（给定其他因子相同）。"""
        cfg = CreditSpreadCBConfig(
            top_k=1,
            weight_rating=1.0,
            weight_ytm=0.0,
            weight_price=0.0,
            weight_bond_floor=0.0,
            weight_premium=0.0,
            weight_issue_size=0.0,
        )
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        aaa = _make_candidate(symbol="AAA_BOND", rating="AAA")
        a = _make_candidate(symbol="A_BOND", rating="A")
        result = s._score_candidates([aaa, a], ctx)
        assert result[0] == "AAA_BOND"

    def test_bond_floor_preferred(self):
        """近面值的 CB 应在债底因子中排名更高。"""
        cfg = CreditSpreadCBConfig(
            top_k=1,
            weight_bond_floor=1.0,
            weight_ytm=0.0,
            weight_price=0.0,
            weight_rating=0.0,
            weight_premium=0.0,
            weight_issue_size=0.0,
        )
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        near = _make_candidate(symbol="NEAR_PAR", close=100.0)
        far = _make_candidate(symbol="FAR_PAR", close=150.0)
        result = s._score_candidates([near, far], ctx)
        assert result[0] == "NEAR_PAR"

    def test_low_premium_preferred(self):
        """低溢价 CB 应排名更高。"""
        cfg = CreditSpreadCBConfig(
            top_k=1,
            weight_premium=1.0,
            weight_ytm=0.0,
            weight_price=0.0,
            weight_rating=0.0,
            weight_bond_floor=0.0,
            weight_issue_size=0.0,
        )
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        low = _make_candidate(symbol="LOW_PREM", cb_over_rate=5.0)
        high = _make_candidate(symbol="HIGH_PREM", cb_over_rate=80.0)
        result = s._score_candidates([low, high], ctx)
        assert result[0] == "LOW_PREM"

    def test_small_size_preferred(self):
        """小规模 CB 应排名更高。"""
        cfg = CreditSpreadCBConfig(
            top_k=1,
            weight_issue_size=1.0,
            weight_ytm=0.0,
            weight_price=0.0,
            weight_rating=0.0,
            weight_bond_floor=0.0,
            weight_premium=0.0,
        )
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        small = _make_candidate(symbol="SMALL", issue_size=10000.0)
        big = _make_candidate(symbol="BIG", issue_size=500000.0)
        result = s._score_candidates([small, big], ctx)
        assert result[0] == "SMALL"

    def test_all_same_returns_all_up_to_top_k(self):
        """所有候选相同时不应崩溃。"""
        cfg = CreditSpreadCBConfig(top_k=3)
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        candidates = [
            _make_candidate(symbol=f"SAME_{i}")
            for i in range(5)
        ]
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_missing_maturity_date(self):
        """maturity_date 为空时 YTM 应降级。"""
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        c1 = _make_candidate(symbol="A", maturity_date="", close=95.0)
        c2 = _make_candidate(symbol="B", maturity_date="20280101", close=95.0)
        result = s._score_candidates([c1, c2], ctx)
        assert len(result) == 2

    def test_missing_rating(self):
        """评级为空时使用默认分。"""
        s = CBCreditSpreadStrategy()
        ctx = _make_context()
        c = _make_candidate(symbol="NO_RATING", rating="")
        result = s._score_candidates([c], ctx)
        assert result == ["NO_RATING"]

    def test_simple_ytm_mode(self):
        """ytm_mode='simple' 应使用简易公式。"""
        cfg = CreditSpreadCBConfig(ytm_mode="simple")
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        c1 = _make_candidate(symbol="A", close=90.0)
        c2 = _make_candidate(symbol="B", close=110.0)
        result = s._score_candidates([c1, c2], ctx)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Weight normalization tests
# ---------------------------------------------------------------------------

class TestWeightNormalization:
    """权重归一化测试。"""

    def test_zero_weights(self):
        """所有权重为 0 时不应崩溃。"""
        cfg = CreditSpreadCBConfig(
            weight_ytm=0.0, weight_rating=0.0, weight_bond_floor=0.0,
            weight_price=0.0, weight_premium=0.0, weight_issue_size=0.0,
        )
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        candidates = [_make_candidate(symbol=f"C{i}") for i in range(3)]
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 3

    def test_single_factor_weight(self):
        """单因子权重 = 1.0 时，结果等同于纯该因子排序。"""
        cfg = CreditSpreadCBConfig(
            top_k=2,
            weight_ytm=0.0, weight_rating=0.0, weight_bond_floor=0.0,
            weight_price=1.0, weight_premium=0.0, weight_issue_size=0.0,
        )
        s = CBCreditSpreadStrategy(config=cfg)
        ctx = _make_context()
        c_cheap = _make_candidate(symbol="CHEAP", close=85.0)
        c_mid = _make_candidate(symbol="MID", close=110.0)
        c_exp = _make_candidate(symbol="EXP", close=140.0)
        result = s._score_candidates([c_exp, c_cheap, c_mid], ctx)
        assert result[0] == "CHEAP"
        assert result[1] == "MID"


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestRegistration:
    """注册表测试。"""

    def test_registered_name(self):
        from aipha.strategy.registry import StrategyRegistry
        reg = StrategyRegistry()
        assert "cb_credit_spread" in reg.list_strategies()

    def test_create_from_registry(self):
        from aipha.strategy.registry import StrategyRegistry
        reg = StrategyRegistry()
        s = reg.create("cb_credit_spread")
        assert isinstance(s, CBCreditSpreadStrategy)
