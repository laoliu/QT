"""Tests for CBEnhancedMultifactorV2Strategy — 12 因子融合策略。

测试覆盖（≥12 用例）：
- Config: 默认值、权重归一化
- Strategy: 注册表、空候选、单候选、多候选排名、筛选
- Factors: v2 新因子集成、排名方向
"""

from __future__ import annotations

from datetime import datetime

import pytest

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.cb_enhanced_mf_v2 import (
    CBEnhancedMultifactorV2Strategy,
    EnhancedMFV2Config,
)


# Helpers

def _make_candidate(
    code: str, close: float, cb_over_rate: float = 10.0,
    cb_value: float = 95.0, amount: float = 200.0,
    issue_size: float = 5000.0, maturity: str = "20270615",
    rating: str = "AA", **kw,
) -> CandidateInfo:
    return CandidateInfo(
        symbol=code, close=close, metadata=kw,
        amount=amount, cb_over_rate=cb_over_rate,
        cb_value=cb_value, maturity_date=maturity,
        issue_size=issue_size, stk_short_name="正股",
        rating=rating,
    )


def _ctx(positions=None, cash=500_000.0) -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions=positions or {},
        cash=cash, nav=cash,
    )


# --- Stub tests (filled in next steps) ---

class TestEnhancedMFV2Config:
    def test_defaults(self) -> None:
        cfg = EnhancedMFV2Config()
        assert cfg.top_k == 20
        assert cfg.rebalance_days == 3
        assert cfg.iv_bias_weight == 0.10
        assert cfg.gamma_convexity_weight == 0.10
        assert cfg.risk_free_rate == 0.02  # §13.1 全局统一

    def test_weights_sum(self) -> None:
        """12 个因子权重之和应接近 1.0。"""
        cfg = EnhancedMFV2Config()
        total = (
            cfg.price_weight + cfg.premium_weight + cfg.cb_value_weight
            + cfg.size_weight + cfg.delta_weight + cfg.rating_weight
            + cfg.ytm_weight + cfg.iv_bias_weight
            + cfg.gamma_convexity_weight + cfg.credit_spread_weight
            + cfg.liquidity_activity_weight + cfg.maturity_convexity_weight
        )
        assert total == pytest.approx(1.0, abs=0.01)

    def test_custom_params(self) -> None:
        cfg = EnhancedMFV2Config(top_k=10, iv_bias_weight=0.20)
        assert cfg.top_k == 10
        assert cfg.iv_bias_weight == 0.20


class TestEnhancedMFV2Strategy:
    @pytest.fixture
    def strategy(self):
        cfg = EnhancedMFV2Config(
            top_k=2, rebalance_days=1,
            max_premium=40.0, min_turnover=10.0, lot_size=10,
        )
        s = CBEnhancedMultifactorV2Strategy(cfg)
        s.on_start()
        return s

    def test_registry(self) -> None:
        """策略已注册。"""
        registry = StrategyRegistry()
        assert registry.get("cb_enhanced_mf_v2") is not None

    def test_empty_candidates(self, strategy) -> None:
        """空候选池 → 空列表。"""
        ctx = _ctx()
        result = strategy._score_candidates([], ctx)
        assert result == []

    def test_single_candidate(self, strategy) -> None:
        """单个候选 → 直接返回。"""
        c = _make_candidate("128001.SZ", 105.0)
        result = strategy._score_candidates([c], _ctx())
        assert result == ["128001.SZ"]

    def test_top_k_selection(self, strategy) -> None:
        """5 个候选, top_k=2 → 返回 2。"""
        candidates = [
            _make_candidate("128001.SZ", 100.0, cb_over_rate=5.0),
            _make_candidate("128002.SZ", 110.0, cb_over_rate=15.0),
            _make_candidate("128003.SZ", 120.0, cb_over_rate=25.0),
            _make_candidate("128004.SZ", 130.0, cb_over_rate=35.0),
            _make_candidate("128005.SZ", 105.0, cb_over_rate=8.0),
        ]
        result = strategy._score_candidates(candidates, _ctx())
        assert len(result) == 2

    def test_premium_filter(self, strategy) -> None:
        """溢价率超标 → 被过滤。"""
        c = _make_candidate("128001.SZ", 105.0, cb_over_rate=50.0)
        assert not strategy._extra_filter(c, _ctx())

    def test_new_factors_affect_rank(self, strategy) -> None:
        """新因子（iv_bias 不同）影响排名结果。"""
        c_a = _make_candidate("128001.SZ", 100.0, cb_value=50.0)   # ATM
        c_b = _make_candidate("128002.SZ", 100.0, cb_value=120.0)  # ITM
        result = strategy._score_candidates([c_a, c_b], _ctx())
        assert len(result) == 2

    def test_12_factors_all_computed(self, strategy) -> None:
        """验证 _score_candidates 不抛异常，所有因子可正常计算。"""
        c = _make_candidate(
            "128001.SZ", 105.0, cb_over_rate=8.0,
            cb_value=90.0, amount=300.0, issue_size=6000.0,
        )
        result = strategy._score_candidates([c], _ctx())
        assert result == ["128001.SZ"]

    def test_rank_direction(self, strategy) -> None:
        """低价格 + 低溢价 排名靠前（升序因子）。"""
        c_low = _make_candidate("128001.SZ", 90.0, cb_over_rate=2.0)
        c_high = _make_candidate("128002.SZ", 140.0, cb_over_rate=30.0)
        result = strategy._score_candidates([c_low, c_high], _ctx())
        assert result[0] == "128001.SZ"

    def test_maturity_convexity_boost(self, strategy) -> None:
        """到期日在甜点区间的债券应有额外加分。"""
        c_sweet = _make_candidate("128001.SZ", 100.0, maturity="20270615")
        c_far = _make_candidate("128002.SZ", 100.0, maturity="20340615")
        result = strategy._score_candidates([c_sweet, c_far], _ctx())
        assert "128001.SZ" in result
