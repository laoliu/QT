"""Tests for NewBondEnsembleStrategy."""

from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.new_bond import (
    NewBondEnsembleConfig,
    NewBondEnsembleStrategy,
)


def _make_context() -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 3, 15, 15, 0),
        positions={},
        cash=500_000.0,
        nav=500_000.0,
    )


class TestNewBondEnsembleStrategy:
    def test_registration(self):
        assert "new_bond_ensemble" in StrategyRegistry().list_strategies()

    def test_degenerate_to_single(self):
        """无 variants 时退化为普通 new_bond 逻辑（按价格升序）。"""
        cfg = NewBondEnsembleConfig(top_k=2, variants=[])
        s = NewBondEnsembleStrategy(cfg)
        candidates = [
            CandidateInfo(
                symbol="A.SZ",
                close=105.0,
                metadata={},
                amount=100.0,
                issue_size=10000.0,
                list_date="20240301",
            ),
            CandidateInfo(
                symbol="B.SZ",
                close=98.0,
                metadata={},
                amount=100.0,
                issue_size=10000.0,
                list_date="20240305",
            ),
            CandidateInfo(
                symbol="C.SZ",
                close=110.0,
                metadata={},
                amount=100.0,
                issue_size=10000.0,
                list_date="20240310",
            ),
        ]
        ranked = s._score_candidates(candidates, _make_context())
        # 按价格升序：B < A < C
        assert ranked == ["B.SZ", "A.SZ"]

    def test_variants_vote(self):
        """多变体投票聚合。"""
        # A: 上市10天，价格102
        # B: 上市30天，价格100
        # C: 上市60天，价格98
        candidates = [
            CandidateInfo(
                symbol="A.SZ",
                close=102.0,
                metadata={},
                amount=100.0,
                issue_size=10000.0,
                list_date="20240305",  # 距 3/15 = 10天
            ),
            CandidateInfo(
                symbol="B.SZ",
                close=100.0,
                metadata={},
                amount=100.0,
                issue_size=10000.0,
                list_date="20240214",  # 距 3/15 = 30天
            ),
            CandidateInfo(
                symbol="C.SZ",
                close=98.0,
                metadata={},
                amount=100.0,
                issue_size=10000.0,
                list_date="20240115",  # 距 3/15 = 60天
            ),
        ]

        cfg = NewBondEnsembleConfig(
            top_k=2,
            max_price=130.0,
            max_age_days=90,
            min_age_days=5,
            variants=[
                {"top_k": 2, "max_age_days": 30},  # 只有 A, B 符合
                {"top_k": 2, "max_age_days": 90},  # A, B, C 都符合
            ],
        )
        s = NewBondEnsembleStrategy(cfg)
        ranked = s._score_candidates(candidates, _make_context())

        # 变体1: B(排1), A(排2)
        # 变体2: C(排1), B(排2), A(排3)
        # B得分: 1/1 + 1/2 = 1.5
        # A得分: 1/2 + 1/3 = 0.83
        # C得分: 1/1 = 1.0
        # 排序: B > C > A
        assert ranked[0] == "B.SZ"
        assert set(ranked) == {"B.SZ", "C.SZ"}

    def test_weights_length_mismatch(self):
        cfg = NewBondEnsembleConfig(
            top_k=2,
            variants=[{"top_k": 2}, {"top_k": 2}],
            weights=[1.0],
        )
        s = NewBondEnsembleStrategy(cfg)
        with pytest.raises(ValueError):
            s._score_candidates([], _make_context())

    def test_extra_filter_uses_max_age(self):
        """_extra_filter 应使用所有 variant 中最宽松的 max_age。"""
        cfg = NewBondEnsembleConfig(
            top_k=2,
            max_age_days=30,
            min_age_days=5,
            variants=[
                {"max_age_days": 60},  # 最宽松
                {"max_age_days": 30},
            ],
        )
        s = NewBondEnsembleStrategy(cfg)

        # 上市 45 天的候选（超过 base 的 30 天，但在 variant 的 60 天内）
        candidate = CandidateInfo(
            symbol="X.SZ",
            close=100.0,
            metadata={},
            amount=100.0,
            issue_size=10000.0,
            list_date="20240130",  # 距 3/15 = 45 天
        )
        ctx = _make_context()
        assert s._extra_filter(candidate, ctx) is True
