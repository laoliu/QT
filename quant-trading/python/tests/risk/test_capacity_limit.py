"""tests/risk/test_capacity_limit.py — CapacityLimit 单元测试"""
from __future__ import annotations

import pytest

from aipha.risk.capacity_limit import CapacityLimit, CapacityEstimate, CapacityCheckResult


# ---------------------------------------------------------------------------
# 构造参数校验
# ---------------------------------------------------------------------------

def test_invalid_participation_zero():
    with pytest.raises(ValueError, match="max_participation"):
        CapacityLimit(max_participation=0.0)


def test_invalid_participation_above_one():
    with pytest.raises(ValueError, match="max_participation"):
        CapacityLimit(max_participation=1.1)


def test_invalid_min_capacity_negative():
    with pytest.raises(ValueError, match="min_capacity_cny"):
        CapacityLimit(min_capacity_cny=-1.0)


# ---------------------------------------------------------------------------
# estimate_capacity 基础场景
# ---------------------------------------------------------------------------

def test_empty_holdings_is_capacity_limited():
    limit = CapacityLimit()
    est = limit.estimate_capacity({}, {})
    assert est.total_capacity_cny == 0.0
    assert est.is_capacity_limited is True


def test_capacity_calculation():
    # 单标的：日均成交额 2_000_000 × 参与率 0.10 = 容量 200_000
    limit = CapacityLimit(max_participation=0.10, min_capacity_cny=100_000)
    est = limit.estimate_capacity(
        holdings={"A": 50_000.0},
        avg_volumes={"A": 2_000_000.0},
    )
    assert abs(est.total_capacity_cny - 200_000.0) < 1.0
    assert est.is_capacity_limited is False


def test_capacity_limited_when_below_minimum():
    limit = CapacityLimit(max_participation=0.10, min_capacity_cny=1_000_000)
    est = limit.estimate_capacity(
        holdings={"small": 10_000.0},
        avg_volumes={"small": 500_000.0},  # 容量=50_000 < 1_000_000
    )
    assert est.is_capacity_limited is True


def test_multiple_assets_total_capacity():
    # 2 个标的，各容量 100_000 → 总 200_000
    limit = CapacityLimit(max_participation=0.10, min_capacity_cny=150_000)
    est = limit.estimate_capacity(
        holdings={"A": 30_000.0, "B": 30_000.0},
        avg_volumes={"A": 1_000_000.0, "B": 1_000_000.0},
    )
    assert abs(est.total_capacity_cny - 200_000.0) < 1.0
    assert est.is_capacity_limited is False


# ---------------------------------------------------------------------------
# 瓶颈标的识别
# ---------------------------------------------------------------------------

def test_bottleneck_identified():
    # A 日均成交额很小，B/C 大
    limit = CapacityLimit(max_participation=0.10)
    est = limit.estimate_capacity(
        holdings={"tiny": 1_000.0, "big1": 50_000.0, "big2": 50_000.0},
        avg_volumes={"tiny": 10_000.0, "big1": 5_000_000.0, "big2": 5_000_000.0},
    )
    bottleneck_syms = {a.symbol for a in est.bottleneck_assets}
    assert "tiny" in bottleneck_syms


def test_get_bottleneck_assets_returns_n():
    limit = CapacityLimit(max_participation=0.10)
    holdings = {f"S{i}": 10_000.0 for i in range(10)}
    avg_vols = {f"S{i}": float((i + 1) * 100_000) for i in range(10)}
    bottlenecks = limit.get_bottleneck_assets(holdings, avg_vols, n=3)
    assert len(bottlenecks) == 3
    # 最小的应在最前
    assert bottlenecks[0].avg_daily_volume_cny <= bottlenecks[1].avg_daily_volume_cny


# ---------------------------------------------------------------------------
# check 接口（质量门禁）
# ---------------------------------------------------------------------------

def test_check_passes_when_sufficient_capacity():
    limit = CapacityLimit(max_participation=0.10, min_capacity_cny=50_000)
    result = limit.check(
        holdings={"A": 100_000.0},
        avg_volumes={"A": 2_000_000.0},  # 容量=200_000 > 50_000
    )
    assert result.passed is True
    assert result.is_capacity_limited is False
    assert result.message == ""


def test_check_fails_when_insufficient_capacity():
    limit = CapacityLimit(max_participation=0.10, min_capacity_cny=5_000_000)
    result = limit.check(
        holdings={"A": 10_000.0},
        avg_volumes={"A": 100_000.0},   # 容量=10_000 << 5_000_000
    )
    assert result.passed is False
    assert result.is_capacity_limited is True
    assert len(result.message) > 0
    assert "bottleneck" in result.message.lower() or len(result.bottleneck_symbols) > 0
