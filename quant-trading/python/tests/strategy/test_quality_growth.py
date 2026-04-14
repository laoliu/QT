"""Tests for QualityGrowthStrategy (A 股质量成长策略).

≥12 tests covering:
- QualityGrowthConfig defaults and overrides
- _extra_filter: all 5 fundamental screening conditions
- _score_candidates: 5-factor scoring with ranking correctness
- Boundary cases: empty candidates, single candidate, None metadata values
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.quality_growth import (
    QualityGrowthConfig,
    QualityGrowthStrategy,
)
from aipha.strategy.registry import StrategyRegistry


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def _make_ctx() -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15),
        positions={},
        cash=1_000_000.0,
        nav=1_000_000.0,
    )


def _make_candidate(
    symbol: str = "000001.SZ",
    close: float = 30.0,
    circ_mv: float = 500_000.0,  # 50 亿
    pe_ttm: float = 15.0,
    roe: float | None = 12.0,
    debt_to_assets: float | None = 40.0,
    revenue_yoy: float | None = 15.0,
    dt_netprofit_yoy: float | None = 20.0,
    rich: bool = False,
    **extra_meta,
) -> EquityCandidateInfo:
    """Build a candidate with sensible defaults that pass all Stage-1 filters.

    When ``rich=True``, include Piotroski and valuation-percentile metadata
    so that all factor functions return valid (non-NaN) values.
    """
    meta: dict = {
        "pe_ttm": pe_ttm,
        "circ_mv": circ_mv,
        "amount": 5000.0,
        "name": "测试",
        "list_date": "20180101",
        "industry": "电子",
    }
    if roe is not None:
        meta["roe"] = roe
    if debt_to_assets is not None:
        meta["debt_to_assets"] = debt_to_assets
    if revenue_yoy is not None:
        meta["revenue_yoy"] = revenue_yoy
    if dt_netprofit_yoy is not None:
        meta["dt_netprofit_yoy"] = dt_netprofit_yoy
    if rich:
        # Piotroski F-Score fields (≥5 dimensions)
        meta.setdefault("roa", 8.0)
        meta.setdefault("roa_prev", 6.0)
        meta.setdefault("cfps", 1.5)
        meta.setdefault("eps", 1.0)
        meta.setdefault("debt_to_assets_prev", (debt_to_assets or 40) + 2)
        meta.setdefault("current_ratio", 2.0)
        meta.setdefault("current_ratio_prev", 1.8)
        meta.setdefault("grossprofit_margin", 35.0)
        meta.setdefault("grossprofit_margin_prev", 32.0)
        # Valuation percentile: pe_history (≥60 entries)
        meta.setdefault("pe_history", [pe_ttm * (0.8 + 0.4 * i / 80) for i in range(80)])
    meta.update(extra_meta)
    return EquityCandidateInfo(
        symbol=symbol,
        close=close,
        circ_mv=circ_mv,
        turnover_rate=3.0,
        amount=5000.0,
        list_date="20180101",
        name="测试",
        metadata=meta,
    )


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestQualityGrowthConfig:
    """Config dataclass correctness."""

    def test_defaults(self):
        cfg = QualityGrowthConfig()
        assert cfg.top_k == 25
        assert cfg.rebalance_days == 15
        assert cfg.min_roe == 10.0
        assert cfg.max_pe == 50.0
        assert cfg.max_debt_to_assets == 65.0
        assert cfg.weight_earnings_growth == pytest.approx(0.30)
        assert cfg.weight_momentum == pytest.approx(0.25)
        assert cfg.weight_quality == pytest.approx(0.20)
        assert cfg.weight_volatility == pytest.approx(0.15)
        assert cfg.weight_valuation_pct == pytest.approx(0.10)

    def test_weights_sum_to_one(self):
        cfg = QualityGrowthConfig()
        total = (
            cfg.weight_earnings_growth
            + cfg.weight_momentum
            + cfg.weight_quality
            + cfg.weight_volatility
            + cfg.weight_valuation_pct
        )
        assert total == pytest.approx(1.0)

    def test_custom_override(self):
        cfg = QualityGrowthConfig(top_k=15, min_roe=8.0, max_pe=60)
        assert cfg.top_k == 15
        assert cfg.min_roe == 8.0
        assert cfg.max_pe == 60

    def test_inherits_rotation_config(self):
        cfg = QualityGrowthConfig()
        # Should have fields from parent EquityRotationConfig
        assert hasattr(cfg, "buffer_k")
        assert hasattr(cfg, "inverse_vol_weighting")
        assert hasattr(cfg, "max_industry_pct")


# ---------------------------------------------------------------------------
# _extra_filter tests (Stage 1: Fundamental screening)
# ---------------------------------------------------------------------------


class TestExtraFilter:
    """Stage-1 fundamental quality pool screening."""

    @pytest.fixture
    def strategy(self):
        return QualityGrowthStrategy()

    @pytest.fixture
    def ctx(self):
        return _make_ctx()

    def test_pass_all_conditions(self, strategy, ctx):
        c = _make_candidate(roe=15.0, pe_ttm=12.0, debt_to_assets=30.0,
                            revenue_yoy=20.0, dt_netprofit_yoy=25.0)
        assert strategy._extra_filter(c, ctx) is True

    def test_reject_low_roe(self, strategy, ctx):
        c = _make_candidate(roe=5.0)  # below 10%
        assert strategy._extra_filter(c, ctx) is False

    def test_reject_negative_pe(self, strategy, ctx):
        c = _make_candidate(pe_ttm=-8.0)  # 亏损
        assert strategy._extra_filter(c, ctx) is False

    def test_reject_high_pe(self, strategy, ctx):
        c = _make_candidate(pe_ttm=80.0)  # > 50
        assert strategy._extra_filter(c, ctx) is False

    def test_reject_high_debt(self, strategy, ctx):
        c = _make_candidate(debt_to_assets=70.0)  # > 65
        assert strategy._extra_filter(c, ctx) is False

    def test_reject_negative_revenue_growth(self, strategy, ctx):
        c = _make_candidate(revenue_yoy=-5.0)  # < 0
        assert strategy._extra_filter(c, ctx) is False

    def test_reject_negative_profit_growth(self, strategy, ctx):
        c = _make_candidate(dt_netprofit_yoy=-10.0)
        assert strategy._extra_filter(c, ctx) is False

    def test_none_values_pass(self, strategy, ctx):
        """Missing metadata = 宽容处理 (allow through)."""
        c = _make_candidate(roe=None, pe_ttm=15.0,
                            debt_to_assets=None, revenue_yoy=None,
                            dt_netprofit_yoy=None)
        assert strategy._extra_filter(c, ctx) is True

    def test_pe_zero_rejected(self, strategy, ctx):
        """PE == 0 should be rejected (meaningless)."""
        c = _make_candidate(pe_ttm=0.0)
        assert strategy._extra_filter(c, ctx) is False

    def test_boundary_roe_exactly_ten(self, strategy, ctx):
        """ROE == 10.0 should pass (≥ threshold)."""
        c = _make_candidate(roe=10.0)
        assert strategy._extra_filter(c, ctx) is True

    def test_boundary_pe_exactly_fifty(self, strategy, ctx):
        """PE == 50.0 should pass (condition is > max_pe to reject)."""
        c = _make_candidate(pe_ttm=50.0)
        assert strategy._extra_filter(c, ctx) is True


# ---------------------------------------------------------------------------
# _score_candidates tests (Stage 2: Quantitative selection)
# ---------------------------------------------------------------------------


class TestScoreCandidates:
    """Stage-2 multi-factor quantitative ranking."""

    @pytest.fixture
    def strategy(self):
        return QualityGrowthStrategy()

    @pytest.fixture
    def ctx(self):
        return _make_ctx()

    def test_empty_candidates(self, strategy, ctx):
        result = strategy._score_candidates([], ctx)
        assert result == []

    def test_single_candidate(self, strategy, ctx):
        c = _make_candidate("A.SZ", roe=15, pe_ttm=10, dt_netprofit_yoy=30, rich=True)
        strategy._price_history["A.SZ"] = [30.0] * 65
        result = strategy._score_candidates([c], ctx)
        assert result == ["A.SZ"]

    def test_ranking_order(self, strategy, ctx):
        """Better fundamentals + uptrend should rank higher."""
        good = _make_candidate("GOOD.SZ", roe=25, pe_ttm=8,
                               dt_netprofit_yoy=50, revenue_yoy=40,
                               debt_to_assets=20, rich=True)
        bad = _make_candidate("BAD.SZ", roe=10, pe_ttm=45,
                              dt_netprofit_yoy=2, revenue_yoy=1,
                              debt_to_assets=60, rich=True,
                              roa=2.0, roa_prev=3.0)
        mid = _make_candidate("MID.SZ", roe=15, pe_ttm=20,
                              dt_netprofit_yoy=20, revenue_yoy=15,
                              debt_to_assets=40, rich=True)

        # Good stock: uptrend; Bad stock: downtrend; Mid stock: flat
        strategy._price_history["GOOD.SZ"] = [25 + i * 0.3 for i in range(65)]
        strategy._price_history["BAD.SZ"] = [50 - i * 0.3 for i in range(65)]
        strategy._price_history["MID.SZ"] = [30.0] * 65

        ranked = strategy._score_candidates([good, bad, mid], ctx)
        assert ranked[0] == "GOOD.SZ"
        assert ranked[-1] == "BAD.SZ"

    def test_all_nan_metadata_no_crash(self, strategy, ctx):
        """Candidates with no usable metadata should not crash."""
        c1 = _make_candidate("X.SZ", roe=None, pe_ttm=None,
                             dt_netprofit_yoy=None, revenue_yoy=None,
                             debt_to_assets=None)
        c2 = _make_candidate("Y.SZ", roe=None, pe_ttm=None,
                             dt_netprofit_yoy=None, revenue_yoy=None,
                             debt_to_assets=None)
        strategy._price_history["X.SZ"] = [30.0] * 65
        strategy._price_history["Y.SZ"] = [30.0] * 65
        # Should not raise
        result = strategy._score_candidates([c1, c2], ctx)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Registry & construction tests
# ---------------------------------------------------------------------------


class TestRegistryAndConstruction:
    """Strategy registry integration."""

    def test_registered_name(self):
        assert StrategyRegistry().get("quality_growth") is not None

    def test_construct_default(self):
        s = QualityGrowthStrategy()
        assert s.name == "quality_growth"

    def test_construct_with_kwargs(self):
        s = QualityGrowthStrategy(top_k=10, min_roe=8.0)
        assert s.config.top_k == 10
        assert s.config.min_roe == 8.0
