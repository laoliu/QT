"""Tests for SmallSizeEnsembleStrategy."""

from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.small_size import (
    SmallSizeConfig,
    SmallSizeEnsembleConfig,
    SmallSizeEnsembleStrategy,
    SmallSizeStrategy,
)


def _make_bar(symbol: str, close: float, metadata: dict | None = None) -> Bar:
    dt = datetime(2024, 3, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 3, 15, 9, 30),
        end_ts=dt,
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=1000,
        metadata=metadata,
    )


def _make_context() -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 3, 15, 15, 0),
        positions={},
        cash=500_000.0,
        nav=500_000.0,
    )


def _candidate(symbol: str, close: float = 100.0, issue_size: float = 5000.0,
               amount: float = 100.0) -> CandidateInfo:
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata={"issue_size": issue_size},
        amount=amount,
        issue_size=issue_size,
    )


# =========================================================================
# SmallSizeStrategy (non-ensemble)
# =========================================================================

class TestSmallSizeStrategy:
    def test_registration(self):
        assert "small_size" in StrategyRegistry().list_strategies()

    def test_default_config(self):
        s = SmallSizeStrategy()
        assert s.config.top_k == 20
        assert s.config.max_price == 130.0

    def test_custom_config(self):
        cfg = SmallSizeConfig(top_k=5, max_price=110.0)
        s = SmallSizeStrategy(cfg)
        assert s.config.top_k == 5
        assert s.config.max_price == 110.0

    def test_score_sorts_by_issue_size(self):
        s = SmallSizeStrategy(SmallSizeConfig(top_k=2))
        candidates = [
            _candidate("A.SZ", issue_size=5000),
            _candidate("B.SZ", issue_size=1000),
            _candidate("C.SZ", issue_size=3000),
        ]
        ranked = s._score_candidates(candidates, _make_context())
        assert ranked == ["B.SZ", "C.SZ"]

    def test_top_k_limits_output(self):
        s = SmallSizeStrategy(SmallSizeConfig(top_k=1))
        candidates = [_candidate(f"{i}.SZ", issue_size=i * 1000) for i in range(1, 6)]
        ranked = s._score_candidates(candidates, _make_context())
        assert len(ranked) == 1

    def test_empty_candidates(self):
        s = SmallSizeStrategy(SmallSizeConfig(top_k=5))
        ranked = s._score_candidates([], _make_context())
        assert ranked == []


# =========================================================================
# SmallSizeEnsembleStrategy
# =========================================================================

class TestSmallSizeEnsembleStrategy:
    def test_registration(self):
        assert "small_size_ensemble" in StrategyRegistry().list_strategies()

    def test_degenerate_to_single(self):
        cfg = SmallSizeEnsembleConfig(top_k=2, variants=[])
        s = SmallSizeEnsembleStrategy(cfg)
        candidates = [
            _candidate("A.SZ", issue_size=3000),
            _candidate("B.SZ", issue_size=1000),
            _candidate("C.SZ", issue_size=5000),
        ]
        ranked = s._score_candidates(candidates, _make_context())
        assert ranked == ["B.SZ", "A.SZ"]

    def test_variants_vote(self):
        # 候选池：A 最小规模但价格稍高；B 次小规模；C 大规模但低价
        candidates = [
            _candidate("A.SZ", close=121.0, issue_size=1000),
            _candidate("B.SZ", close=110.0, issue_size=2000),
            _candidate("C.SZ", close=105.0, issue_size=9000),
        ]

        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            max_price=130.0,
            min_issue_size=0.0,
            variants=[
                {"top_k": 2, "max_price": 120.0},  # A 被过滤，仅 B/C 参与
                {"top_k": 2, "max_price": 130.0},  # A/B 参与
            ],
        )
        s = SmallSizeEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())

        # 预期：B 在两个变体都入选，综合得票最高
        assert ranked[0] == "B.SZ"
        assert set(ranked) == {"A.SZ", "B.SZ"}

    def test_weights_length_mismatch(self):
        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            variants=[{"top_k": 2}, {"top_k": 2}],
            weights=[1.0],
        )
        s = SmallSizeEnsembleStrategy(cfg)
        with pytest.raises(ValueError):
            s._score_candidates([], _make_context())

    # ----- New tests below -----

    def test_empty_candidates_with_variants(self):
        """有 variants 但无候选，应返回空。"""
        cfg = SmallSizeEnsembleConfig(
            top_k=3,
            min_issue_size=0.0,
            variants=[{"top_k": 3}, {"top_k": 3}],
        )
        s = SmallSizeEnsembleStrategy(cfg)
        ranked = s._score_candidates([], _make_context())
        assert ranked == []

    def test_single_candidate_single_variant(self):
        """仅 1 个候选和 1 个 variant，应正常返回。"""
        cfg = SmallSizeEnsembleConfig(
            top_k=5,
            min_issue_size=0.0,
            variants=[{"top_k": 5}],
        )
        s = SmallSizeEnsembleStrategy(cfg)
        candidates = [_candidate("X.SZ", close=100.0, issue_size=2000)]
        ranked = s._score_candidates(candidates, _make_context())
        assert ranked == ["X.SZ"]

    def test_weighted_voting(self):
        """不同权重影响排名。"""
        candidates = [
            _candidate("A.SZ", close=100.0, issue_size=1000),
            _candidate("B.SZ", close=100.0, issue_size=2000),
        ]
        # variant0 权重高: picks A first (smallest)
        # variant1 权重低: picks A first (smallest)
        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            min_issue_size=0.0,
            variants=[{"top_k": 2}, {"top_k": 2}],
            weights=[10.0, 1.0],
        )
        s = SmallSizeEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())
        assert ranked[0] == "A.SZ"

    def test_tiebreak_by_issue_size(self):
        """相同得分时按 issue_size 升序打破平局。"""
        candidates = [
            _candidate("B.SZ", close=100.0, issue_size=3000),
            _candidate("A.SZ", close=100.0, issue_size=1000),
        ]
        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            min_issue_size=0.0,
            variants=[{"top_k": 1}],  # 只选 1，另一个得分=0
        )
        s = SmallSizeEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())
        # A (smallest) picked by variant, B not picked
        assert ranked[0] == "A.SZ"

    def test_variant_filter_by_turnover(self):
        """variant 可以用 min_turnover 过滤低成交候选。"""
        candidates = [
            _candidate("A.SZ", close=100.0, issue_size=1000, amount=200.0),
            _candidate("B.SZ", close=100.0, issue_size=2000, amount=10.0),  # 低成交
        ]
        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            min_issue_size=0.0,
            variants=[{"top_k": 2, "min_turnover": 100.0}],  # B 被过滤
        )
        s = SmallSizeEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())
        assert ranked == ["A.SZ"]

    def test_variant_filter_by_max_issue_size(self):
        """variant 可以通过 max_issue_size 过滤大规模发行。"""
        candidates = [
            _candidate("A.SZ", close=100.0, issue_size=1000),
            _candidate("B.SZ", close=100.0, issue_size=50000),  # 大规模
        ]
        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            min_issue_size=0.0,
            variants=[{"top_k": 2, "max_issue_size": 10000}],  # B 被过滤
        )
        s = SmallSizeEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())
        assert ranked == ["A.SZ"]

    def test_unsupported_aggregation_raises(self):
        """不支持的聚合方式抛 ValueError。"""
        candidates = [_candidate("A.SZ", close=100.0, issue_size=1000)]
        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            min_issue_size=0.0,
            variants=[{"top_k": 2}],
            aggregation="unknown_method",
        )
        s = SmallSizeEnsembleStrategy(cfg)
        with pytest.raises(ValueError, match="unsupported aggregation"):
            s._score_candidates(candidates, _make_context())

    def test_zero_close_filtered(self):
        """close=0 的候选在 variant 过滤中被排除。"""
        candidates = [
            _candidate("A.SZ", close=0.0, issue_size=1000),
            _candidate("B.SZ", close=100.0, issue_size=2000),
        ]
        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            min_issue_size=0.0,
            variants=[{"top_k": 2}],
        )
        s = SmallSizeEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())
        assert "A.SZ" not in ranked
        assert ranked == ["B.SZ"]

    def test_many_variants_stabilize(self):
        """多个 variants 对同一候选池投票，稳定候选应排在前面。"""
        candidates = [
            _candidate("A.SZ", close=100.0, issue_size=1000, amount=200),
            _candidate("B.SZ", close=100.0, issue_size=2000, amount=200),
            _candidate("C.SZ", close=100.0, issue_size=3000, amount=200),
        ]
        cfg = SmallSizeEnsembleConfig(
            top_k=2,
            min_issue_size=0.0,
            variants=[
                {"top_k": 3},
                {"top_k": 3},
                {"top_k": 3},
            ],
        )
        s = SmallSizeEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())
        # A (smallest) should always be #1 across all variants
        assert ranked[0] == "A.SZ"
        assert len(ranked) == 2

    def test_default_config_values(self):
        cfg = SmallSizeEnsembleConfig()
        assert cfg.variants == []
        assert cfg.weights == []
        assert cfg.aggregation == "reciprocal_rank"
        assert cfg.top_k == 20
