"""Tests for cb_delta_zone — 可转债 Delta 分档因子。

测试覆盖（≥10 用例）：
- compute_delta_proxy: 正常值、边界、None
- classify_delta_zone: 三区分类 + UNKNOWN
- compute_gamma_proxy: ATM 最大、边界递减
- compute_premium_rate: 正常、边界
"""

from __future__ import annotations

import pytest

from aipha.factors.cb_delta_zone import (
    DEFAULT_BOND_THRESHOLD,
    DEFAULT_EQUITY_THRESHOLD,
    DeltaZone,
    classify_delta_zone,
    compute_delta_proxy,
    compute_gamma_proxy,
    compute_premium_rate,
)


# ---------------------------------------------------------------------------
# 1. compute_delta_proxy
# ---------------------------------------------------------------------------

class TestComputeDeltaProxy:
    def test_atm(self) -> None:
        """转股价值=面值 → delta≈1.0 (简易版)。"""
        assert compute_delta_proxy(100.0, 100.0) == pytest.approx(1.0)

    def test_deep_in_money(self) -> None:
        """转股价值远大于面值 → delta=1.0 (截断)。"""
        assert compute_delta_proxy(150.0, 100.0) == pytest.approx(1.0)

    def test_deep_out_money(self) -> None:
        """转股价值远小于面值 → delta 小。"""
        result = compute_delta_proxy(30.0, 100.0)
        assert result is not None
        assert result == pytest.approx(0.3)

    def test_zero_cb_value(self) -> None:
        """cb_value=0 → delta=0。"""
        assert compute_delta_proxy(0.0, 100.0) == pytest.approx(0.0)

    def test_none_cb_value(self) -> None:
        assert compute_delta_proxy(None) is None

    def test_with_bond_floor(self) -> None:
        """精确版带 bond_floor。"""
        # bond_floor=70, par=100, cb_value=85
        # delta = (85-70)/(100-70) = 15/30 = 0.5
        result = compute_delta_proxy(85.0, 100.0, bond_floor=70.0)
        assert result == pytest.approx(0.5)

    def test_bond_floor_above_par(self) -> None:
        """bond_floor >= par_value → 降级到简易模式。"""
        result = compute_delta_proxy(95.0, 100.0, bond_floor=100.0)
        assert result == pytest.approx(0.95)

    def test_negative_par(self) -> None:
        """par_value<=0 → None。"""
        assert compute_delta_proxy(95.0, 0.0) is None
        assert compute_delta_proxy(95.0, -100.0) is None


# ---------------------------------------------------------------------------
# 2. classify_delta_zone
# ---------------------------------------------------------------------------

class TestClassifyDeltaZone:
    def test_bond_like(self) -> None:
        """转股价值很低 → BOND_LIKE。"""
        zone = classify_delta_zone(cb_value=20.0, par_value=100.0)
        assert zone == DeltaZone.BOND_LIKE

    def test_balanced(self) -> None:
        """转股价值在平衡区 → BALANCED。"""
        zone = classify_delta_zone(cb_value=50.0, par_value=100.0)
        assert zone == DeltaZone.BALANCED

    def test_equity_like(self) -> None:
        """转股价值接近面值 → EQUITY_LIKE。"""
        zone = classify_delta_zone(cb_value=80.0, par_value=100.0)
        assert zone == DeltaZone.EQUITY_LIKE

    def test_deep_in_money(self) -> None:
        """转股价值大于面值 → EQUITY_LIKE。"""
        zone = classify_delta_zone(cb_value=130.0, par_value=100.0)
        assert zone == DeltaZone.EQUITY_LIKE

    def test_unknown_none(self) -> None:
        """cb_value=None → UNKNOWN。"""
        zone = classify_delta_zone(cb_value=None)
        assert zone == DeltaZone.UNKNOWN

    def test_direct_delta(self) -> None:
        """直接传入 delta 值。"""
        assert classify_delta_zone(delta=0.1) == DeltaZone.BOND_LIKE
        assert classify_delta_zone(delta=0.5) == DeltaZone.BALANCED
        assert classify_delta_zone(delta=0.8) == DeltaZone.EQUITY_LIKE

    def test_boundary_bond_balanced(self) -> None:
        """delta=0.3 边界 → BALANCED（含下界）。"""
        zone = classify_delta_zone(delta=DEFAULT_BOND_THRESHOLD)
        assert zone == DeltaZone.BALANCED

    def test_boundary_balanced_equity(self) -> None:
        """delta=0.7 边界 → BALANCED（不含上界）。"""
        zone = classify_delta_zone(delta=DEFAULT_EQUITY_THRESHOLD)
        assert zone == DeltaZone.BALANCED

    def test_custom_thresholds(self) -> None:
        """自定义阈值。"""
        zone = classify_delta_zone(
            delta=0.4, bond_threshold=0.5, equity_threshold=0.8,
        )
        assert zone == DeltaZone.BOND_LIKE


# ---------------------------------------------------------------------------
# 3. compute_gamma_proxy
# ---------------------------------------------------------------------------

class TestComputeGammaProxy:
    def test_atm_gamma_max(self) -> None:
        """delta=0.5 → gamma_proxy=1.0 (最大)。"""
        gamma = compute_gamma_proxy(50.0, 100.0)
        assert gamma == pytest.approx(1.0)

    def test_deep_out_money(self) -> None:
        """delta≈0 → gamma≈0。"""
        gamma = compute_gamma_proxy(0.0, 100.0)
        assert gamma == pytest.approx(0.0)

    def test_deep_in_money(self) -> None:
        """delta=1 → gamma=0。"""
        gamma = compute_gamma_proxy(150.0, 100.0)
        assert gamma == pytest.approx(0.0)

    def test_none_cb_value(self) -> None:
        assert compute_gamma_proxy(None) is None

    def test_balanced_zone(self) -> None:
        """平衡区 gamma 应较高。"""
        gamma = compute_gamma_proxy(40.0, 100.0)  # delta=0.4
        assert gamma is not None
        assert gamma > 0.5  # 0.4 * 0.6 * 4 = 0.96


# ---------------------------------------------------------------------------
# 4. compute_premium_rate
# ---------------------------------------------------------------------------

class TestComputePremiumRate:
    def test_positive_premium(self) -> None:
        """close > cb_value → 正溢价。"""
        rate = compute_premium_rate(close=120.0, cb_value=100.0)
        assert rate == pytest.approx(20.0)

    def test_negative_premium(self) -> None:
        """close < cb_value → 负溢价（折价）。"""
        rate = compute_premium_rate(close=90.0, cb_value=100.0)
        assert rate == pytest.approx(-10.0)

    def test_zero_premium(self) -> None:
        rate = compute_premium_rate(close=100.0, cb_value=100.0)
        assert rate == pytest.approx(0.0)

    def test_none_cb_value(self) -> None:
        assert compute_premium_rate(close=100.0, cb_value=None) is None

    def test_zero_cb_value(self) -> None:
        assert compute_premium_rate(close=100.0, cb_value=0.0) is None

    def test_zero_close(self) -> None:
        assert compute_premium_rate(close=0.0, cb_value=100.0) is None


# ---------------------------------------------------------------------------
# 5. DeltaZone 枚举
# ---------------------------------------------------------------------------

class TestDeltaZoneEnum:
    def test_values(self) -> None:
        assert DeltaZone.BOND_LIKE.value == "bond_like"
        assert DeltaZone.BALANCED.value == "balanced"
        assert DeltaZone.EQUITY_LIKE.value == "equity_like"
        assert DeltaZone.UNKNOWN.value == "unknown"

    def test_from_string(self) -> None:
        assert DeltaZone("balanced") == DeltaZone.BALANCED
