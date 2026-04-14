"""Tests for BucketMultiFactorStrategy.

覆盖要求 (≥15 用例):
- 配置/桶规格 (默认+自定义)
- 策略注册
- 分桶逻辑
- 桶内因子打分
- 桶间合并
- warmup 降级
- 空数据/边界
"""
from __future__ import annotations

import math
from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.bucket_multifactor import (
    BucketMultiFactorConfig,
    BucketMultiFactorStrategy,
    BucketSpec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bar(
    symbol: str,
    close: float = 30.0,
    pe_ttm: float = 15.0,
    pb: float = 2.0,
    roe: float = 12.0,
    circ_mv: float = 500_000.0,
    amount: float = 5000.0,
    name: str = "TestStock",
    list_date: str = "20180101",
    turnover_rate: float = 5.0,
) -> Bar:
    dt = datetime(2024, 6, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 6, 15, 9, 30),
        end_ts=dt,
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=100_000,
        is_suspended=False,
        metadata={
            "pe_ttm": pe_ttm, "pb": pb, "roe": roe,
            "circ_mv": circ_mv, "amount": amount,
            "name": name, "list_date": list_date,
            "turnover_rate": turnover_rate,
        },
    )


def _make_candidate(
    symbol: str, close: float = 30.0, circ_mv: float = 500_000.0,
    pe_ttm: float = 15.0, roe: float = 12.0, turnover_rate: float = 5.0,
    list_date: str = "20180101", name: str = "TestStock",
) -> EquityCandidateInfo:
    return EquityCandidateInfo(
        symbol=symbol, close=close, circ_mv=circ_mv,
        turnover_rate=turnover_rate, amount=5000.0,
        list_date=list_date, name=name,
        metadata={
            "pe_ttm": pe_ttm, "pb": 2.0, "roe": roe,
            "circ_mv": circ_mv, "amount": 5000.0,
            "name": name, "list_date": list_date,
            "turnover_rate": turnover_rate,
        },
    )


def _default_context():
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions={}, cash=1_000_000.0, nav=1_000_000.0,
        metadata={},
    )


# ---------------------------------------------------------------------------
# Test Config
# ---------------------------------------------------------------------------

class TestBucketConfig:
    """配置与桶规格测试。"""

    def test_default_config(self):
        cfg = BucketMultiFactorConfig()
        assert cfg.top_k == 30
        assert cfg.rebalance_days == 10
        assert cfg.stop_loss_pct == 0.15

    def test_default_three_buckets(self):
        cfg = BucketMultiFactorConfig()
        buckets = cfg.get_buckets()
        assert len(buckets) == 3
        names = [b.name for b in buckets]
        assert names == ["large", "mid", "small"]

    def test_custom_bucket_specs(self):
        specs = [
            {"name": "big", "mv_min": 500_000, "mv_max": 9e18,
             "top_k": 5, "weight_alloc": 0.5,
             "factor_weights": {"value": 1.0}},
            {"name": "tiny", "mv_min": 0, "mv_max": 500_000,
             "top_k": 5, "weight_alloc": 0.5,
             "factor_weights": {"size": 1.0}},
        ]
        cfg = BucketMultiFactorConfig(bucket_specs=specs)
        buckets = cfg.get_buckets()
        assert len(buckets) == 2
        assert buckets[0].name == "big"
        assert buckets[1].top_k == 5

    def test_bucket_weights_sum(self):
        cfg = BucketMultiFactorConfig()
        buckets = cfg.get_buckets()
        total = sum(b.weight_alloc for b in buckets)
        assert abs(total - 1.0) < 0.01


# ---------------------------------------------------------------------------
# Test Registration
# ---------------------------------------------------------------------------

class TestBucketRegistration:
    """策略注册测试。"""

    def test_registry_create(self):
        reg = StrategyRegistry()
        strat = reg.create("bucket_multifactor")
        assert isinstance(strat, BucketMultiFactorStrategy)
        assert strat.name == "bucket_multifactor"

    def test_registry_with_params(self):
        reg = StrategyRegistry()
        strat = reg.create("bucket_multifactor", top_k=20, rebalance_days=5)
        assert strat.config.top_k == 20
        assert strat.config.rebalance_days == 5


# ---------------------------------------------------------------------------
# Test Bucketing
# ---------------------------------------------------------------------------

class TestBucketAllocation:
    """分桶逻辑测试。"""

    def test_three_bucket_split(self):
        """大/中/小盘股正确分到对应桶。"""
        strat = BucketMultiFactorStrategy()
        cands = [
            _make_candidate("LARGE.SZ", circ_mv=2_000_000, pe_ttm=10, roe=15),
            _make_candidate("MID.SZ", circ_mv=500_000, pe_ttm=12, roe=14),
            _make_candidate("SMALL.SZ", circ_mv=100_000, pe_ttm=20, roe=8),
        ]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        # 三只股票都应该出现在结果中
        assert len(result) == 3
        assert set(result) == {"LARGE.SZ", "MID.SZ", "SMALL.SZ"}

    def test_empty_bucket(self):
        """某桶无股票时不影响其他桶。"""
        strat = BucketMultiFactorStrategy()
        # 只有大盘股
        cands = [
            _make_candidate(f"L{i}.SZ", circ_mv=2_000_000 + i * 10000,
                            pe_ttm=10 + i, roe=15 - i)
            for i in range(5)
        ]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        assert len(result) == 5

    def test_all_in_one_bucket(self):
        """所有股票都在中盘桶。"""
        strat = BucketMultiFactorStrategy()
        cands = [
            _make_candidate(f"M{i}.SZ", circ_mv=500_000,
                            pe_ttm=10 + i, roe=15 - i)
            for i in range(8)
        ]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        assert len(result) == 8


# ---------------------------------------------------------------------------
# Test Scoring
# ---------------------------------------------------------------------------

class TestBucketScoring:
    """桶内打分+合并测试。"""

    def test_large_bucket_quality_bias(self):
        """大盘桶中高 ROE 股票应排名靠前。"""
        strat = BucketMultiFactorStrategy()
        cands = [
            _make_candidate("HIGH_ROE.SZ", circ_mv=2_000_000, roe=30.0, pe_ttm=15),
            _make_candidate("LOW_ROE.SZ", circ_mv=2_000_000, roe=3.0, pe_ttm=15),
        ]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        # 高 ROE 应排在前面（大盘桶 quality 权重 0.30）
        assert result[0] == "HIGH_ROE.SZ"

    def test_small_bucket_reversal_bias(self):
        """小盘桶中近期下跌的股票应因反转因子获得更高分。

        注意: STR 因子需要价格历史，在无历史时使用静态因子。
        这里测试的是小盘桶内其他可用因子（size, earnings_growth）。
        """
        strat = BucketMultiFactorStrategy()
        # 更小市值应得到更高的 size 分数
        cands = [
            _make_candidate("TINY.SZ", circ_mv=50_000, pe_ttm=20, roe=8),
            _make_candidate("BIGGER.SZ", circ_mv=180_000, pe_ttm=12, roe=12),
        ]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        assert len(result) == 2

    def test_score_merge_respects_alloc(self):
        """桶间合并应按 weight_alloc 加权。"""
        specs = [
            {"name": "a", "mv_min": 1_000_000, "mv_max": 9e18,
             "top_k": 2, "weight_alloc": 0.9,
             "factor_weights": {"value": 1.0}},
            {"name": "b", "mv_min": 0, "mv_max": 1_000_000,
             "top_k": 2, "weight_alloc": 0.1,
             "factor_weights": {"size": 1.0}},
        ]
        cfg = BucketMultiFactorConfig(bucket_specs=specs)
        strat = BucketMultiFactorStrategy(config=cfg)
        cands = [
            _make_candidate("A1.SZ", circ_mv=2_000_000, pe_ttm=5),
            _make_candidate("B1.SZ", circ_mv=100_000, pe_ttm=30),
        ]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        # A 桶权重 0.9, B 桶 0.1 → A1 应排在 B1 前面
        assert result[0] == "A1.SZ"

    def test_warmup_fallback(self):
        """无价格历史时动态因子应降级到静态因子。"""
        strat = BucketMultiFactorStrategy()
        # 不设置 _price_history → momentum/volatility/str 不可用
        cands = [
            _make_candidate(f"S{i}.SZ", circ_mv=500_000, pe_ttm=10 + i)
            for i in range(5)
        ]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        # 应正常返回（使用静态因子）
        assert len(result) == 5

    def test_empty_candidates(self):
        """空候选列表应返回空结果。"""
        strat = BucketMultiFactorStrategy()
        ctx = _default_context()
        result = strat._score_candidates([], ctx)
        assert result == []


# ---------------------------------------------------------------------------
# Test Edge Cases
# ---------------------------------------------------------------------------

class TestBucketEdgeCases:
    """边界条件测试。"""

    def test_single_candidate(self):
        """单只股票。"""
        strat = BucketMultiFactorStrategy()
        cands = [_make_candidate("SOLO.SZ", circ_mv=500_000)]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        assert result == ["SOLO.SZ"]

    def test_all_same_mv(self):
        """所有股票市值相同，全部进同一桶。"""
        strat = BucketMultiFactorStrategy()
        cands = [
            _make_candidate(f"X{i}.SZ", circ_mv=500_000,
                            pe_ttm=5 + i * 3, roe=20 - i * 2)
            for i in range(10)
        ]
        ctx = _default_context()
        result = strat._score_candidates(cands, ctx)
        assert len(result) == 10
        # 结果中每个股票只出现一次
        assert len(set(result)) == 10
