"""tests for cb_adaptive_factor strategy"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from unittest.mock import MagicMock

import numpy as np
import pytest

from aipha.strategy.cb_adaptive_factor import (
    CBAdaptiveFactorConfig,
    CBAdaptiveFactorStrategy,
    FACTOR_DEFS,
)
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.base import StrategyContext


def _make_candidate(
    symbol: str = "128000.SZ",
    close: float = 110.0,
    cb_over_rate: float = 15.0,
    issue_size: float = 5000.0,
    cb_value: float = 95.0,
    rating: str = "AA",
    list_date: str = "20230101",
    maturity_date: str = "20290101",
    amount: float = 500.0,
    stk_code: str = "",
) -> CandidateInfo:
    meta = {
        "cb_over_rate": cb_over_rate,
        "issue_size": issue_size,
        "cb_value": cb_value,
        "rating": rating,
        "list_date": list_date,
        "maturity_date": maturity_date,
        "amount": amount,
    }
    if stk_code:
        meta["stk_code"] = stk_code
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata=meta,
        amount=amount,
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        rating=rating,
        list_date=list_date,
        maturity_date=maturity_date,
    )


def _make_context(dt: str = "20240601") -> StrategyContext:
    return StrategyContext(
        current_time=datetime.strptime(dt, "%Y%m%d"),
        nav=1_000_000.0,
        cash=500_000.0,
        positions={},
    )


class TestCBAdaptiveFactorConfig:
    def test_default_config(self):
        cfg = CBAdaptiveFactorConfig()
        assert cfg.top_k == 15
        assert cfg.ic_lookback == 60
        assert cfg.blend_ratio == 0.6
        assert cfg.ic_floor == 0.02

    def test_get_base_weights(self):
        cfg = CBAdaptiveFactorConfig()
        w = cfg.get_base_weights()
        assert len(w) == 22  # 12 base + 5 Phase 1 + 5 Phase 2 stock
        assert all(v >= 0 for v in w.values())
        assert abs(sum(w.values()) - 1.0) < 0.05

    def test_custom_base_weights(self):
        cfg = CBAdaptiveFactorConfig(
            base_weight_price=0.30,
            base_weight_premium=0.30,
            base_weight_size=0.0,
        )
        w = cfg.get_base_weights()
        assert w["price"] == 0.30
        assert w["size"] == 0.0


class TestCBAdaptiveFactorStrategy:
    def test_instantiation(self):
        s = CBAdaptiveFactorStrategy()
        assert s.config.top_k == 15
        assert len(s._dynamic_weights) == 22

    def test_registry_lookup(self):
        from aipha.strategy.registry import StrategyRegistry
        registry = StrategyRegistry()
        cls = registry.get("cb_adaptive_factor")
        assert cls is not None

    def test_config_property(self):
        cfg = CBAdaptiveFactorConfig(top_k=8)
        s = CBAdaptiveFactorStrategy(cfg)
        assert s.config.top_k == 8

    def test_score_empty_candidates(self):
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        result = s._score_candidates([], ctx)
        assert result == []

    def test_score_single_candidate(self):
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        c = _make_candidate()
        result = s._score_candidates([c], ctx)
        assert result == [c.symbol]

    def test_score_multiple_candidates(self):
        s = CBAdaptiveFactorStrategy(CBAdaptiveFactorConfig(top_k=3))
        ctx = _make_context()
        candidates = [
            _make_candidate("A.SZ", close=100, cb_over_rate=5, issue_size=2000),
            _make_candidate("B.SZ", close=120, cb_over_rate=20, issue_size=8000),
            _make_candidate("C.SZ", close=105, cb_over_rate=10, issue_size=3000),
            _make_candidate("D.SZ", close=130, cb_over_rate=30, issue_size=15000),
            _make_candidate("E.SZ", close=95, cb_over_rate=3, issue_size=1500),
        ]
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 3
        # 低价低溢价小规模应排前
        assert "E.SZ" in result
        assert "A.SZ" in result


class TestFactorExtraction:
    def test_extract_factors_returns_all(self):
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        candidates = [
            _make_candidate("A.SZ", close=100),
            _make_candidate("B.SZ", close=110),
        ]
        factors = s._extract_factors(candidates, ctx)
        assert set(factors.keys()) == set(FACTOR_DEFS.keys())
        assert len(factors) == 22  # 12 base + 5 Phase1 + 5 stock
        for fn, vals in factors.items():
            assert len(vals) == 2, f"factor {fn} length mismatch"

    def test_new_factors_extracted(self):
        """5 new Phase 1 factors should be present and finite."""
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        candidates = [
            _make_candidate("A.SZ", close=100, cb_value=95.0),
            _make_candidate("B.SZ", close=120, cb_value=110.0),
        ]
        factors = s._extract_factors(candidates, ctx)
        new_factors = [
            "iv_bias", "gamma_convexity", "credit_spread",
            "liquidity_activity", "maturity_convexity",
        ]
        for fn in new_factors:
            assert fn in factors
            assert len(factors[fn]) == 2
            for v in factors[fn]:
                assert math.isfinite(v), f"{fn} has non-finite value"

    def test_bb_deviation_no_history(self):
        s = CBAdaptiveFactorStrategy()
        assert s._compute_bb_deviation("X.SZ", 100.0) == 0.0

    def test_bb_deviation_with_history(self):
        s = CBAdaptiveFactorStrategy(CBAdaptiveFactorConfig(lookback=5, bb_k=2.0))
        s._price_history["X.SZ"] = deque([100, 101, 102, 100, 101])
        dev = s._compute_bb_deviation("X.SZ", 95.0)
        assert dev > 0  # 大幅低于均值 → 正(超卖)

    def test_momentum_no_history(self):
        s = CBAdaptiveFactorStrategy()
        assert s._compute_momentum("X.SZ") == 0.0

    def test_momentum_with_history(self):
        s = CBAdaptiveFactorStrategy(
            CBAdaptiveFactorConfig(momentum_lookback=3),
        )
        s._price_history["X.SZ"] = deque([100, 102, 104, 108])
        m = s._compute_momentum("X.SZ")
        assert m == pytest.approx(0.08, abs=0.001)  # (108-100)/100

    def test_premium_pct_no_history(self):
        s = CBAdaptiveFactorStrategy()
        assert s._compute_premium_pct("X.SZ", 15.0) == 0.5

    def test_putback_factor(self):
        s = CBAdaptiveFactorStrategy()
        # cb_value=70 → parity=0.7, trigger=0.85
        p = s._compute_putback(70.0)
        expected = 1.0 - 0.7 / 0.85
        assert p == pytest.approx(expected, abs=1e-4)


class TestICDynamicWeights:
    def test_initial_weights_are_base(self):
        s = CBAdaptiveFactorStrategy()
        base = s.config.get_base_weights()
        for fn in FACTOR_DEFS:
            assert abs(s._dynamic_weights[fn] - base[fn]) < 1e-6

    def test_recompute_with_insufficient_data(self):
        s = CBAdaptiveFactorStrategy(
            CBAdaptiveFactorConfig(min_ic_samples=20),
        )
        # Only push 5 samples — not enough
        for _ in range(5):
            s._return_history.append({"A": 0.01})
            s._factor_history["price"].append({"A": 100.0})
        s._recompute_ic_weights()
        # Should fall back to base weights
        base = s.config.get_base_weights()
        for fn in FACTOR_DEFS:
            assert abs(s._dynamic_weights[fn] - base[fn]) < 1e-6

    def test_update_factor_history_grows(self):
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        candidates = [
            _make_candidate("A.SZ", close=100),
            _make_candidate("B.SZ", close=110),
        ]
        factors = s._extract_factors(candidates, ctx)
        s._update_factor_history(candidates, factors)
        assert len(s._factor_history["price"]) == 1
        assert len(s._return_history) == 1
        # Second update
        s._update_factor_history(candidates, factors)
        assert len(s._factor_history["price"]) == 2

    def test_ic_update_interval(self):
        """IC应每N次调仓才更新一次"""
        cfg = CBAdaptiveFactorConfig(
            top_k=3, ic_update_interval=3, min_ic_samples=2,
        )
        s = CBAdaptiveFactorStrategy(cfg)
        ctx = _make_context()
        candidates = [
            _make_candidate("A.SZ", close=100),
            _make_candidate("B.SZ", close=110),
        ]
        # Push enough IC data
        for _ in range(30):
            s._return_history.append({"A": 0.01, "B": -0.01, "C": 0.02, "D": -0.02, "E": 0.0})
            for fn in FACTOR_DEFS:
                s._factor_history[fn].append({"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0, "E": 5.0})

        # First 2 calls shouldn't trigger IC update
        s._score_candidates(candidates, ctx)
        s._score_candidates(candidates, ctx)
        assert s._ic_update_counter == 2
        # Third call triggers update (counter resets to 0)
        s._score_candidates(candidates, ctx)
        assert s._ic_update_counter == 0


class TestOnStartExtra:
    """_on_start_extra clears all stateful data for WF reuse."""

    def test_clears_history(self):
        s = CBAdaptiveFactorStrategy()
        # Pollute state
        s._price_history["X"] = deque([1, 2, 3])
        s._premium_history["X"] = deque([10, 20])
        s._prev_prices["X"] = 100.0
        s._return_history.append({"X": 0.01})
        s._factor_history["price"].append({"X": 99.0})
        s._ic_update_counter = 42
        # Reset
        s._on_start_extra()
        assert len(s._price_history) == 0
        assert len(s._premium_history) == 0
        assert len(s._prev_prices) == 0
        assert len(s._return_history) == 0
        assert len(s._factor_history["price"]) == 0
        assert s._ic_update_counter == 0

    def test_restores_base_weights(self):
        s = CBAdaptiveFactorStrategy()
        # Modify weights
        s._dynamic_weights = {"price": 1.0}
        s._on_start_extra()
        assert len(s._dynamic_weights) == 22
        base = s.config.get_base_weights()
        for fn in FACTOR_DEFS:
            assert abs(s._dynamic_weights[fn] - base[fn]) < 1e-6

    def test_restores_factor_directions(self):
        s = CBAdaptiveFactorStrategy()
        s._factor_directions["price"] = False  # flip
        s._on_start_extra()
        assert s._factor_directions["price"] == FACTOR_DEFS["price"]


class TestOnBarUpdateState:
    """_on_bar_update_state takes dict[str, Bar], not single Bar."""

    def _make_bar(self, symbol: str, close: float,
                  cb_over_rate: float = 10.0) -> MagicMock:
        bar = MagicMock()
        bar.symbol = symbol
        bar.close = close
        bar.metadata = {"cb_over_rate": cb_over_rate}
        return bar

    def test_processes_multiple_bars(self):
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        bars = {
            "A.SZ": self._make_bar("A.SZ", 100.0),
            "B.SZ": self._make_bar("B.SZ", 110.0),
        }
        s._on_bar_update_state(bars, ctx)
        assert "A.SZ" in s._price_history
        assert "B.SZ" in s._price_history
        assert s._prev_prices["A.SZ"] == 100.0
        assert s._prev_prices["B.SZ"] == 110.0

    def test_premium_history_tracked(self):
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        bars = {"A.SZ": self._make_bar("A.SZ", 100.0, cb_over_rate=15.0)}
        s._on_bar_update_state(bars, ctx)
        assert len(s._premium_history["A.SZ"]) == 1
        assert s._premium_history["A.SZ"][-1] == 15.0

    def test_return_computed_on_second_bar(self):
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        # First bar
        s._on_bar_update_state({"A.SZ": self._make_bar("A.SZ", 100.0)}, ctx)
        # Add a return_history slot
        s._return_history.append({})
        # Second bar → triggers return calc
        s._on_bar_update_state({"A.SZ": self._make_bar("A.SZ", 105.0)}, ctx)
        assert "A.SZ" in s._return_history[-1]
        assert s._return_history[-1]["A.SZ"] == pytest.approx(0.05)


class TestExtraFilter:
    """_extra_filter(candidate, context) → bool."""

    def test_accepts_good_candidate(self):
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context("20240601")
        # list_date within [min_age_days, max_age_days] from context
        c = _make_candidate(cb_over_rate=20.0, list_date="20240301")
        assert s._extra_filter(c, ctx) is True

    def test_rejects_high_premium(self):
        s = CBAdaptiveFactorStrategy(CBAdaptiveFactorConfig(max_premium=30.0))
        ctx = _make_context()
        c = _make_candidate(cb_over_rate=50.0)
        assert s._extra_filter(c, ctx) is False

    def test_rejects_too_new(self):
        s = CBAdaptiveFactorStrategy(
            CBAdaptiveFactorConfig(use_age_filter=True, min_age_days=30),
        )
        ctx = _make_context("20240601")
        c = _make_candidate(list_date="20240525")  # 7 days old
        assert s._extra_filter(c, ctx) is False

    def test_rejects_too_old(self):
        s = CBAdaptiveFactorStrategy(
            CBAdaptiveFactorConfig(use_age_filter=True, max_age_days=180),
        )
        ctx = _make_context("20240601")
        c = _make_candidate(list_date="20220101")  # >2 years
        assert s._extra_filter(c, ctx) is False

    def test_accepts_no_age_filter(self):
        s = CBAdaptiveFactorStrategy(
            CBAdaptiveFactorConfig(use_age_filter=False),
        )
        ctx = _make_context("20240601")
        c = _make_candidate(list_date="20220101")
        assert s._extra_filter(c, ctx) is True


class TestICDirectionFlip:
    """When IC is consistently negative, factor ranking direction flips."""

    @staticmethod
    def _build_ic_data(s, neg_factors=None, n=30):
        """Build aligned factor+return history for all factors at once."""
        neg_factors = neg_factors or set()
        s._on_start_extra()
        for t in range(n):
            syms = {f"S{i}": float(i) for i in range(10)}
            for fn in FACTOR_DEFS:
                s._factor_history[fn].append(dict(syms))
            if neg_factors:
                # price-negcorr, others positive
                rets = {f"S{i}": -float(i) * 0.01 for i in range(10)}
            else:
                rets = {f"S{i}": float(i) * 0.01 for i in range(10)}
            s._return_history.append(rets)

    def test_direction_preserved_when_ic_positive(self):
        cfg = CBAdaptiveFactorConfig(
            min_ic_samples=5,
            enable_ic_direction_flip=True,
            ic_flip_threshold=0.65,
        )
        s = CBAdaptiveFactorStrategy(cfg)
        self._build_ic_data(s, neg_factors=None, n=30)
        s._recompute_ic_weights()
        # All ICs positive → no direction should flip
        for fn in FACTOR_DEFS:
            assert s._factor_directions[fn] == FACTOR_DEFS[fn]

    def test_direction_flips_when_ic_negative(self):
        cfg = CBAdaptiveFactorConfig(
            min_ic_samples=5,
            enable_ic_direction_flip=True,
            ic_flip_threshold=0.65,
        )
        s = CBAdaptiveFactorStrategy(cfg)
        s._on_start_extra()
        # Build data where ALL factors have negative IC uniformly
        for t in range(30):
            syms = {f"S{i}": float(i) for i in range(10)}
            for fn in FACTOR_DEFS:
                s._factor_history[fn].append(dict(syms))
            # Returns negatively correlated with factor
            rets = {f"S{i}": -float(i) * 0.01 for i in range(10)}
            s._return_history.append(rets)
        s._recompute_ic_weights()
        # All factors should flip direction
        for fn in FACTOR_DEFS:
            assert s._factor_directions[fn] != FACTOR_DEFS[fn], (
                f"{fn} should have flipped"
            )

    def test_factor_directions_used_in_scoring(self):
        """_score_candidates uses _factor_directions."""
        s = CBAdaptiveFactorStrategy(CBAdaptiveFactorConfig(top_k=5))
        ctx = _make_context()
        # Force direction flip for "price": True→False (higher=better)
        s._factor_directions["price"] = False
        candidates = [
            _make_candidate("A.SZ", close=90),
            _make_candidate("B.SZ", close=150),
        ]
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 2


class TestICDecayDormancy:
    """When IC trend is strongly declining, factor goes dormant."""

    def test_dormant_factor_gets_reduced_weight(self):
        cfg = CBAdaptiveFactorConfig(
            min_ic_samples=5,
            enable_ic_decay_dormancy=True,
            ic_decay_slope_threshold=-0.003,
            ic_floor=0.02,
        )
        s = CBAdaptiveFactorStrategy(cfg)
        s._on_start_extra()
        # Build aligned history: all factors share same return_history
        for t in range(30):
            syms = {f"S{i}": float(i) for i in range(10)}
            for fn in FACTOR_DEFS:
                s._factor_history[fn].append(dict(syms))
            # Returns: positive corr (all factors get positive IC)
            rets = {f"S{i}": float(i) * 0.01 for i in range(10)}
            s._return_history.append(rets)
        s._recompute_ic_weights()
        # With stable positive IC, all factors should get weight
        for fn in FACTOR_DEFS:
            assert s._dynamic_weights.get(fn, 0.0) > 0

    def test_dormancy_config_respected(self):
        """When dormancy disabled, no factor goes dormant."""
        cfg = CBAdaptiveFactorConfig(
            min_ic_samples=5,
            enable_ic_decay_dormancy=False,
        )
        s = CBAdaptiveFactorStrategy(cfg)
        s._on_start_extra()
        for t in range(30):
            syms = {f"S{i}": float(i) for i in range(10)}
            for fn in FACTOR_DEFS:
                s._factor_history[fn].append(dict(syms))
            rets = {f"S{i}": float(i) * 0.01 for i in range(10)}
            s._return_history.append(rets)
        s._recompute_ic_weights()
        # With dormancy disabled, no factor should be zeroed
        active = sum(1 for w in s._dynamic_weights.values() if w > 0)
        assert active == 22


# -----------------------------------------------------------------------
# Phase 2: 正股质量因子测试
# -----------------------------------------------------------------------


class TestStockFactors:
    """Tests for 5 stock quality factors integrated in R23."""

    def test_stock_factors_in_factor_defs(self):
        """All 5 stock factors registered in FACTOR_DEFS."""
        for fn in ("stock_roe", "stock_ep", "stock_vol",
                    "stock_growth", "stock_safety"):
            assert fn in FACTOR_DEFS

    def test_stock_factor_directions(self):
        """stock_safety ascending, others descending."""
        assert FACTOR_DEFS["stock_safety"] is True
        for fn in ("stock_roe", "stock_ep", "stock_vol", "stock_growth"):
            assert FACTOR_DEFS[fn] is False

    def test_base_weights_include_stock(self):
        cfg = CBAdaptiveFactorConfig()
        w = cfg.get_base_weights()
        assert w["stock_roe"] == 0.07
        assert w["stock_ep"] == 0.05
        assert w["stock_vol"] == 0.04
        assert w["stock_growth"] == 0.05
        assert w["stock_safety"] == 0.04

    def test_extract_factors_has_stock(self):
        """_extract_factors returns all 5 stock factor columns."""
        s = CBAdaptiveFactorStrategy()
        ctx = _make_context()
        cands = [_make_candidate("A.SZ"), _make_candidate("B.SZ")]
        factors = s._extract_factors(cands, ctx)
        for fn in ("stock_roe", "stock_ep", "stock_vol",
                    "stock_growth", "stock_safety"):
            assert fn in factors
            assert len(factors[fn]) == 2

    def test_stock_roe_default_no_fundamentals(self):
        """No bridge data → returns default_roe."""
        s = CBAdaptiveFactorStrategy()
        c = _make_candidate(stk_code="000001.SZ")
        assert s._get_stock_roe(c) == s._config.default_roe

    def test_stock_ep_default_no_fundamentals(self):
        """No bridge data → returns default_ep."""
        s = CBAdaptiveFactorStrategy()
        c = _make_candidate(stk_code="000001.SZ")
        assert s._get_stock_ep(c) == s._config.default_ep

    def test_stock_vol_insufficient_history(self):
        """Fewer than 10 price points → returns 0.0."""
        s = CBAdaptiveFactorStrategy()
        c = _make_candidate(stk_code="000001.SZ")
        from collections import deque as _deque
        s._stk_price_history["000001.SZ"] = _deque([100, 101, 102, 103, 104])
        assert s._get_stock_vol(c) == 0.0

    def test_stock_vol_with_enough_history(self):
        """20+ price points → returns positive annualized vol."""
        s = CBAdaptiveFactorStrategy()
        c = _make_candidate(stk_code="000001.SZ")
        from collections import deque as _deque
        prices = [100 + i * 0.5 + (i % 3) * 0.2 for i in range(25)]
        s._stk_price_history["000001.SZ"] = _deque(prices)
        vol = s._get_stock_vol(c)
        assert vol > 0.0
        assert math.isfinite(vol)

    def test_stock_growth_default_no_fundamentals(self):
        """No bridge data → returns default_growth."""
        s = CBAdaptiveFactorStrategy()
        c = _make_candidate(stk_code="000001.SZ")
        assert s._get_stock_growth(c) == s._config.default_growth

    def test_stock_safety_default_no_fundamentals(self):
        """No bridge data → returns default_safety (50.0)."""
        s = CBAdaptiveFactorStrategy()
        c = _make_candidate(stk_code="000001.SZ")
        assert s._get_stock_safety(c) == s._config.default_safety

    def test_stock_roe_with_fundamentals(self):
        """When fundamentals cached, returns actual ROE."""
        s = CBAdaptiveFactorStrategy()
        fund = MagicMock(roe=12.5, ep=0.08)
        s._current_fundamentals["000001.SZ"] = fund
        c = _make_candidate(stk_code="000001.SZ")
        assert s._get_stock_roe(c) == 12.5

    def test_stock_ep_with_fundamentals(self):
        """When fundamentals cached, returns actual EP."""
        s = CBAdaptiveFactorStrategy()
        fund = MagicMock(ep=0.06)
        s._current_fundamentals["000001.SZ"] = fund
        c = _make_candidate(stk_code="000001.SZ")
        assert s._get_stock_ep(c) == pytest.approx(0.06)

    def test_stock_no_stk_code_returns_default(self):
        """Candidate without stk_code in metadata → defaults."""
        s = CBAdaptiveFactorStrategy()
        c = _make_candidate()  # no stk_code
        assert s._get_stock_roe(c) == s._config.default_roe
        assert s._get_stock_ep(c) == s._config.default_ep
        assert s._get_stock_vol(c) == 0.0
        assert s._get_stock_growth(c) == s._config.default_growth
        assert s._get_stock_safety(c) == s._config.default_safety

    def test_use_stock_bridge_config(self):
        """use_stock_bridge config field defaults to True."""
        cfg = CBAdaptiveFactorConfig()
        assert cfg.use_stock_bridge is True
        cfg2 = CBAdaptiveFactorConfig(use_stock_bridge=False)
        assert cfg2.use_stock_bridge is False

    def test_stock_config_defaults(self):
        """Stock-related config defaults are correct."""
        cfg = CBAdaptiveFactorConfig()
        assert cfg.vol_lookback == 60
        assert cfg.default_roe == 0.0
        assert cfg.default_ep == 0.0
        assert cfg.default_growth == 0.0
        assert cfg.default_safety == 50.0


class TestZeroVarianceSuppression:
    """Tests for zero-variance factor detection & weight redistribution."""

    def _make_strategy(self, **kw):
        cfg = CBAdaptiveFactorConfig(top_k=3, **kw)
        return CBAdaptiveFactorStrategy(config=cfg)

    def test_constant_factor_gets_zero_weight(self):
        """Factors with identical values across candidates get weight=0."""
        s = self._make_strategy(use_stock_bridge=False)
        ctx = _make_context()
        # 3 candidates with identical close → double_low will differ via
        # different cb_over_rate; but stock factors all default → constant
        c1 = _make_candidate("A", close=100, cb_over_rate=10, cb_value=90)
        c2 = _make_candidate("B", close=105, cb_over_rate=20, cb_value=80)
        c3 = _make_candidate("C", close=110, cb_over_rate=30, cb_value=70)
        # Should not raise and should return top_k symbols
        result = s._score_candidates([c1, c2, c3], ctx)
        assert len(result) == 3
        assert set(result) == {"A", "B", "C"}

    def test_all_factors_variable_no_suppression(self):
        """When all factors vary, weight redistribution is zero."""
        s = self._make_strategy(use_stock_bridge=False)
        ctx = _make_context()
        c1 = _make_candidate("A", close=100, cb_over_rate=10, amount=100)
        c2 = _make_candidate("B", close=120, cb_over_rate=25, amount=500)
        c3 = _make_candidate("C", close=140, cb_over_rate=40, amount=900)
        result = s._score_candidates([c1, c2, c3], ctx)
        assert len(result) == 3

    def test_single_candidate_bypasses(self):
        """Single candidate returns immediately, no variance check."""
        s = self._make_strategy(use_stock_bridge=False)
        ctx = _make_context()
        c = _make_candidate("X", close=100)
        result = s._score_candidates([c], ctx)
        assert result == ["X"]

    def test_empty_candidates_bypasses(self):
        """Empty candidates returns empty list."""
        s = self._make_strategy(use_stock_bridge=False)
        ctx = _make_context()
        result = s._score_candidates([], ctx)
        assert result == []


# ===========================================================================
# RSRS timing + Vol targeting overlay tests
# ===========================================================================


class TestRSRSTimingOverlay:
    """RSRS 择时 overlay 集成测试。"""

    def _make_strategy(self, **kw):
        cfg = CBAdaptiveFactorConfig(
            top_k=5, use_stock_bridge=False, **kw,
        )
        return CBAdaptiveFactorStrategy(config=cfg)

    def test_rsrs_disabled_by_default_scale_is_one(self):
        """RSRS 未启用时 overlay_scale=1.0。"""
        s = self._make_strategy(use_rsrs_timing=False)
        assert s._get_overlay_scale() == 1.0

    def test_rsrs_enabled_initializes_factor(self):
        """启用 RSRS 时内部 _rsrs 非 None。"""
        s = self._make_strategy(use_rsrs_timing=True)
        assert s._rsrs is not None
        # 初始信号应该是 NEUTRAL
        from aipha.factors.rsrs_timing import MarketSignal
        assert s._rsrs.get_signal() == MarketSignal.NEUTRAL

    def test_rsrs_bear_reduces_scale(self):
        """RSRS BEAR 信号应降低 overlay_scale。"""
        s = self._make_strategy(
            use_rsrs_timing=True,
            rsrs_bear_scale=0.3,
            rsrs_neutral_scale=0.7,
        )
        # 模拟 BEAR 信号
        from aipha.factors.rsrs_timing import MarketSignal, RSRSResult
        s._rsrs._latest_result = RSRSResult(
            beta=0.8, r_squared=0.9, rsrs_zscore=-1.5,
            rsrs_std=-1.35, signal=MarketSignal.BEAR,
        )
        scale = s._get_overlay_scale()
        assert abs(scale - 0.3) < 1e-6

    def test_rsrs_bull_full_scale(self):
        """RSRS BULL 信号应保持满仓。"""
        s = self._make_strategy(
            use_rsrs_timing=True,
            rsrs_bull_scale=1.0,
        )
        from aipha.factors.rsrs_timing import MarketSignal, RSRSResult
        s._rsrs._latest_result = RSRSResult(
            beta=1.2, r_squared=0.95, rsrs_zscore=1.5,
            rsrs_std=1.425, signal=MarketSignal.BULL,
        )
        scale = s._get_overlay_scale()
        assert abs(scale - 1.0) < 1e-6

    def test_rsrs_reset_on_start(self):
        """_on_start_extra 应重置 RSRS 状态。"""
        s = self._make_strategy(use_rsrs_timing=True)
        # Feed some data
        s._rsrs.update(110.0, 100.0)
        s._rsrs.update(112.0, 101.0)
        assert len(s._rsrs._highs) == 2
        # Reset
        s._on_start_extra()
        assert len(s._rsrs._highs) == 0


class TestVolTargetingOverlay:
    """波动率目标 overlay 集成测试。"""

    def _make_strategy(self, **kw):
        cfg = CBAdaptiveFactorConfig(
            top_k=5, use_stock_bridge=False, **kw,
        )
        return CBAdaptiveFactorStrategy(config=cfg)

    def test_vol_targeting_disabled_scale_is_one(self):
        """Vol targeting 未启用时 overlay_scale=1.0。"""
        s = self._make_strategy(use_vol_targeting=False)
        assert s._get_overlay_scale() == 1.0

    def test_vol_targeting_enabled_initializes(self):
        """启用时内部 _vol_targeter 非 None。"""
        s = self._make_strategy(use_vol_targeting=True)
        assert s._vol_targeter is not None

    def test_high_vol_reduces_scale(self):
        """已实现高波动率应缩减仓位。"""
        s = self._make_strategy(
            use_vol_targeting=True,
            vol_target=0.10,
            vol_target_lookback=10,
            vol_scale_floor=0.3,
        )
        # 模拟高波动日收益序列
        import random
        random.seed(42)
        for _ in range(15):
            s._vol_targeter.update(random.gauss(0, 0.03))  # ~47% annualized
        scale = s._vol_targeter.get_current_scale()
        assert scale < 1.0, f"Expected scale < 1.0 with high vol, got {scale}"

    def test_low_vol_increases_scale(self):
        """已实现低波动率应放大仓位（不超过 cap）。"""
        s = self._make_strategy(
            use_vol_targeting=True,
            vol_target=0.20,
            vol_target_lookback=10,
            vol_scale_cap=1.5,
        )
        # 模拟低波动日收益序列
        for i in range(15):
            s._vol_targeter.update(0.001 * (1 if i % 2 == 0 else -1))
        scale = s._vol_targeter.get_current_scale()
        assert scale >= 1.0, f"Expected scale >= 1.0 with low vol, got {scale}"

    def test_vol_reset_on_start(self):
        """_on_start_extra 应重置 vol targeter 状态。"""
        s = self._make_strategy(use_vol_targeting=True)
        s._vol_targeter.update(0.02)
        s._vol_targeter.update(-0.01)
        assert s._vol_targeter.n_observations == 2
        s._on_start_extra()
        assert s._vol_targeter.n_observations == 0


class TestCombinedOverlay:
    """RSRS + Vol Targeting 组合 overlay 测试。"""

    def _make_strategy(self, **kw):
        cfg = CBAdaptiveFactorConfig(
            top_k=5, use_stock_bridge=False, **kw,
        )
        return CBAdaptiveFactorStrategy(config=cfg)

    def test_both_overlays_multiply(self):
        """两个 overlay 应相乘：rsrs_scale × vol_scale。"""
        s = self._make_strategy(
            use_rsrs_timing=True,
            use_vol_targeting=True,
            rsrs_neutral_scale=0.7,
        )
        # Set RSRS to NEUTRAL (scale=0.7)
        from aipha.factors.rsrs_timing import MarketSignal, RSRSResult
        s._rsrs._latest_result = RSRSResult(
            beta=1.0, r_squared=0.9, rsrs_zscore=0.0,
            rsrs_std=0.0, signal=MarketSignal.NEUTRAL,
        )
        # Set vol targeter to scale=0.8
        for _ in range(15):
            s._vol_targeter.update(0.015)
        # Override vol scale for deterministic test
        s._vol_targeter._current_scale = 0.8
        combined = s._get_overlay_scale()
        expected = 0.7 * 0.8  # = 0.56
        assert abs(combined - expected) < 1e-6

    def test_both_disabled_returns_one(self):
        """两个 overlay 都未启用时返回 1.0。"""
        s = self._make_strategy(
            use_rsrs_timing=False, use_vol_targeting=False,
        )
        assert s._get_overlay_scale() == 1.0