"""Tests for NewSmallEnsembleStrategy."""

from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.new_small import (
    NewSmallEnsembleConfig,
    NewSmallEnsembleStrategy,
)


def _make_context() -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 3, 15, 15, 0),
        positions={},
        cash=500_000.0,
        nav=500_000.0,
    )


class TestNewSmallEnsembleStrategy:
    def test_registration(self):
        assert "new_small_ensemble" in StrategyRegistry().list_strategies()

    def test_degenerate_to_single(self):
        """无 variants 时退化为普通 new_small 逻辑（按规模升序）。"""
        cfg = NewSmallEnsembleConfig(
            top_k=2,
            variants=[],
            min_issue_size=0.0,
        )
        s = NewSmallEnsembleStrategy(cfg)
        candidates = [
            CandidateInfo(
                symbol="A.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=5000.0,
                list_date="20240301",
            ),
            CandidateInfo(
                symbol="B.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=3000.0,
                list_date="20240305",
            ),
            CandidateInfo(
                symbol="C.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=8000.0,
                list_date="20240310",
            ),
        ]
        ranked = s._score_candidates(candidates, _make_context())
        # 按规模升序：B < A < C
        assert ranked == ["B.SZ", "A.SZ"]

    def test_variants_vote(self):
        """多变体投票聚合。"""
        # A: 规模4000，上市10天
        # B: 规模3000，上市30天
        # C: 规模2000，上市60天
        candidates = [
            CandidateInfo(
                symbol="A.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=4000.0,
                list_date="20240305",  # 10天
            ),
            CandidateInfo(
                symbol="B.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=3000.0,
                list_date="20240214",  # 30天
            ),
            CandidateInfo(
                symbol="C.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=2000.0,
                list_date="20240115",  # 60天
            ),
        ]

        cfg = NewSmallEnsembleConfig(
            top_k=2,
            max_price=130.0,
            max_age_days=90,
            min_age_days=5,
            min_issue_size=0.0,
            max_issue_size=10000.0,
            variants=[
                {"top_k": 2, "max_age_days": 30},  # 只有 A, B 符合
                {"top_k": 2, "max_age_days": 90},  # A, B, C 都符合
            ],
        )
        s = NewSmallEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())

        # 变体1: B(排1，规模3000), A(排2，规模4000)
        # 变体2: C(排1，规模2000), B(排2), A(排3)
        # B得分: 1/1 + 1/2 = 1.5
        # A得分: 1/2 + 1/3 = 0.83
        # C得分: 1/1 = 1.0
        # 排序: B > C > A
        assert ranked[0] == "B.SZ"
        assert set(ranked) == {"B.SZ", "C.SZ"}

    def test_weights_length_mismatch(self):
        cfg = NewSmallEnsembleConfig(
            top_k=2,
            variants=[{"top_k": 2}, {"top_k": 2}],
            weights=[1.0],
        )
        s = NewSmallEnsembleStrategy(cfg)
        with pytest.raises(ValueError):
            s._score_candidates([], _make_context())

    def test_extra_filter_uses_max_age(self):
        """_extra_filter 应使用所有 variant 中最宽松的 max_age。"""
        cfg = NewSmallEnsembleConfig(
            top_k=2,
            max_age_days=30,
            min_age_days=5,
            min_issue_size=0.0,
            variants=[
                {"max_age_days": 60},  # 最宽松
                {"max_age_days": 30},
            ],
        )
        s = NewSmallEnsembleStrategy(cfg)

        # 上市 45 天的候选
        candidate = CandidateInfo(
            symbol="X.SZ",
            close=100.0,
            metadata={},
            amount=100.0,
            issue_size=5000.0,
            list_date="20240130",  # 距 3/15 = 45 天
        )
        ctx = _make_context()
        assert s._extra_filter(candidate, ctx) is True

    def test_issue_size_filter_in_variants(self):
        """变体中的 max_issue_size 过滤生效。"""
        candidates = [
            CandidateInfo(
                symbol="A.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=4000.0,
                list_date="20240305",
            ),
            CandidateInfo(
                symbol="B.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=6000.0,
                list_date="20240305",
            ),
        ]

        cfg = NewSmallEnsembleConfig(
            top_k=2,
            max_age_days=90,
            min_age_days=5,
            min_issue_size=0.0,
            max_issue_size=10000.0,
            variants=[
                {"top_k": 2, "max_issue_size": 5000.0},  # 只有 A 符合
                {"top_k": 2, "max_issue_size": 10000.0},  # A, B 都符合
            ],
        )
        s = NewSmallEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())

        # 变体1: A(排1)
        # 变体2: A(排1), B(排2)
        # A得分: 1/1 + 1/1 = 2.0
        # B得分: 1/2 = 0.5
        assert ranked[0] == "A.SZ"
