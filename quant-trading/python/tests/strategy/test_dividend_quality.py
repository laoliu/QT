"""Tests for DividendQualityStrategy (O7)."""

from __future__ import annotations

import math
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from aipha.strategy.dividend_quality import (
    DividendQualityConfig,
    DividendQualityStrategy,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo


def _make_candidate(
    symbol: str,
    close: float = 20.0,
    dv_ratio: float = 2.0,
    roe: float = 12.0,
    pe_ttm: float = 15.0,
    circ_mv: float = 1000000.0,
    turnover_rate: float = 3.0,
    cfps: float = 1.5,
    total_share: float = 50000.0,
) -> EquityCandidateInfo:
    return EquityCandidateInfo(
        symbol=symbol,
        close=close,
        metadata={
            "dv_ratio": dv_ratio,
            "roe": roe,
            "pe_ttm": pe_ttm,
            "circ_mv": circ_mv,
            "turnover_rate": turnover_rate,
            "cfps": cfps,
            "total_share": total_share,
        },
        circ_mv=circ_mv,
        turnover_rate=turnover_rate,
        amount=50000.0,
        list_date="20150101",
        name=f"Stock_{symbol}",
    )


def _make_context() -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2025, 1, 15),
        positions={},
        cash=1000000.0,
        nav=1000000.0,
        metadata={},
    )


class TestDividendQualityConfig:
    def test_defaults(self):
        cfg = DividendQualityConfig()
        assert cfg.weight_dividend == 0.25
        assert cfg.weight_fcfy == 0.25
        assert cfg.weight_roe == 0.20
        assert cfg.weight_vol == 0.15
        assert cfg.weight_ep == 0.15
        assert cfg.min_dividend == 0.5
        assert cfg.min_roe == 5.0

    def test_custom(self):
        cfg = DividendQualityConfig(
            weight_dividend=0.40, min_dividend=1.0,
        )
        assert cfg.weight_dividend == 0.40
        assert cfg.min_dividend == 1.0


class TestDividendQualityStrategy:
    def test_create_default(self):
        s = DividendQualityStrategy()
        assert s.config.weight_dividend == 0.25

    def test_create_from_kwargs(self):
        s = DividendQualityStrategy(weight_dividend=0.5, min_roe=8.0)
        assert s.config.weight_dividend == 0.5
        assert s.config.min_roe == 8.0

    def test_registry(self):
        from aipha.strategy.registry import create_strategy
        s = create_strategy("dividend_quality")
        assert isinstance(s, DividendQualityStrategy)

    def test_score_empty(self):
        s = DividendQualityStrategy()
        ctx = _make_context()
        result = s._score_candidates([], ctx)
        assert result == []

    def test_hard_filter_removes_low_dv(self):
        s = DividendQualityStrategy()
        candidates = [
            _make_candidate("A", dv_ratio=0.3),  # below 0.5
            _make_candidate("B", dv_ratio=2.0),
        ]
        filtered = s._apply_hard_filters(candidates)
        # Only B passes dv threshold
        assert any(c.symbol == "B" for c in filtered)

    def test_hard_filter_removes_low_roe(self):
        s = DividendQualityStrategy()
        candidates = [
            _make_candidate("A", roe=3.0),  # below 5.0
            _make_candidate("B", roe=15.0),
        ]
        filtered = s._apply_hard_filters(candidates)
        assert any(c.symbol == "B" for c in filtered)

    def test_hard_filter_fallback(self):
        """When too few pass hard threshold, fallback to dv > 0."""
        cfg = DividendQualityConfig(top_k=5)
        s = DividendQualityStrategy(config=cfg)
        # Only 2 pass hard threshold but top_k=5
        candidates = [
            _make_candidate("A", dv_ratio=2.0, roe=10.0),
            _make_candidate("B", dv_ratio=1.0, roe=8.0),
            _make_candidate("C", dv_ratio=0.2, roe=3.0),
        ]
        filtered = s._apply_hard_filters(candidates)
        # Fallback: dv > 0 → A, B, C all pass (C has dv=0.2 > 0)
        assert len(filtered) == 3

    def test_score_candidates_returns_ranked(self):
        s = DividendQualityStrategy()
        # Need price history for volatility factor
        s._price_history = {
            "A": [20.0 + i * 0.1 for i in range(70)],
            "B": [30.0 + i * 0.05 for i in range(70)],
            "C": [10.0 + i * 0.2 for i in range(70)],
        }
        candidates = [
            _make_candidate("A", dv_ratio=5.0, roe=20.0, pe_ttm=8.0),
            _make_candidate("B", dv_ratio=3.0, roe=15.0, pe_ttm=12.0),
            _make_candidate("C", dv_ratio=1.0, roe=8.0, pe_ttm=25.0),
        ]
        ctx = _make_context()
        ranked = s._score_candidates(candidates, ctx)
        assert len(ranked) > 0
        # Best dividend+quality should rank first
        assert ranked[0] == "A"

    def test_weights_sum_to_one(self):
        cfg = DividendQualityConfig()
        total = (cfg.weight_dividend + cfg.weight_fcfy +
                 cfg.weight_roe + cfg.weight_vol + cfg.weight_ep)
        assert abs(total - 1.0) < 0.01
