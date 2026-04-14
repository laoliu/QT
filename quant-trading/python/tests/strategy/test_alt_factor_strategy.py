"""另类因子规则策略单元测试。"""

from __future__ import annotations

import pytest

from aipha.strategy.alt_factor_strategy import (
    AltFactorConfig,
    AltFactorStrategy,
)
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.base import StrategyContext

from datetime import datetime


def _make_ctx() -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2025, 1, 15),
        positions={},
        cash=1_000_000,
        nav=1_000_000,
    )


def _make_candidates(n: int = 5) -> list[EquityCandidateInfo]:
    candidates = []
    for i in range(n):
        candidates.append(EquityCandidateInfo(
            symbol=f"{600000 + i}.SH",
            close=10.0 + i,
            metadata={
                "chips_concentration": 0.1 * (i + 1),
                "flow_buy_ratio": 0.5 + 0.1 * i,
                "margin_buy_intensity": 0.02 * (i + 1),
                "chips_cost_ratio": 1.0 + 0.1 * i,
                "flow_large_pct": 0.3 + 0.05 * i,
                "flow_momentum_5d": -0.02 + 0.01 * i,
                "margin_net_momentum": 0.01 * i,
                "analyst_buy_ratio": 0.4 + 0.1 * i,
                "analyst_est_growth": 0.1 + 0.05 * i,
                "chips_asm": 0.5 + 0.1 * i,
            },
        ))
    return candidates


class TestAltFactorStrategy:
    """另类因子策略测试。"""

    def test_score_returns_all_candidates(self):
        strategy = AltFactorStrategy()
        ctx = _make_ctx()
        cands = _make_candidates(5)
        scores = strategy._score_candidates(cands, ctx)
        assert len(scores) == 5

    def test_score_ranking(self):
        strategy = AltFactorStrategy()
        ctx = _make_ctx()
        cands = _make_candidates(5)
        scores = strategy._score_candidates(cands, ctx)
        # 因子值递增 → 最后一个候选得分最高
        scores_dict = dict(scores)
        assert scores_dict["600004.SH"] > scores_dict["600000.SH"]

    def test_empty_candidates(self):
        strategy = AltFactorStrategy()
        ctx = _make_ctx()
        scores = strategy._score_candidates([], ctx)
        assert scores == []

    def test_missing_factors(self):
        """部分因子缺失时仍可打分。"""
        strategy = AltFactorStrategy()
        ctx = _make_ctx()
        cands = [
            EquityCandidateInfo(
                symbol="600000.SH", close=10.0,
                metadata={"chips_concentration": 0.5},
            ),
            EquityCandidateInfo(
                symbol="600001.SH", close=11.0,
                metadata={"chips_concentration": 0.8},
            ),
        ]
        scores = strategy._score_candidates(cands, ctx)
        assert len(scores) == 2

    def test_all_factors_nan(self):
        """全部因子缺失时返回零分。"""
        strategy = AltFactorStrategy()
        ctx = _make_ctx()
        cands = [
            EquityCandidateInfo(symbol="600000.SH", close=10.0, metadata={}),
            EquityCandidateInfo(symbol="600001.SH", close=11.0, metadata={}),
        ]
        scores = strategy._score_candidates(cands, ctx)
        assert all(s == 0.0 for _, s in scores)

    def test_config_defaults(self):
        cfg = AltFactorConfig()
        assert cfg.rank_normalize is True
        assert cfg.min_factor_coverage == pytest.approx(0.3)
        assert "chips_concentration" in cfg.factor_weights

    def test_param_space(self):
        ps = AltFactorStrategy.param_space()
        assert ps is not None

    def test_registry_registered(self):
        from aipha.strategy.registry import StrategyRegistry
        reg = StrategyRegistry()
        assert "alt_factor" in reg._strategies
