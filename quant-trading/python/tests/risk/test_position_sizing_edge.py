"""Position sizing edge-case tests.

Covers untested boundaries in position_sizing.py:
- VolatilityTargetOverlay: empty returns, all-zero, exact lookback, max_scale>1
- DrawdownPositionReducer: zero NAV, negative NAV, threshold boundary,
  max_threshold boundary, threshold==max_threshold (bug: ZeroDivisionError)
- PositionSizingOverlay: vol-only, dd-only
"""
from __future__ import annotations

import math

import pytest

from aipha.risk.position_sizing import (
    DrawdownControlConfig,
    DrawdownPositionReducer,
    PositionSizingOverlay,
    VolatilityTargetConfig,
    VolatilityTargetOverlay,
)


# ============================================================================
# VolatilityTargetOverlay edge cases
# ============================================================================


class TestVolOverlayEdge:

    def test_empty_returns(self):
        """空列表 → max_scale (数据不足分支)"""
        overlay = VolatilityTargetOverlay(VolatilityTargetConfig(max_scale=1.0))
        assert overlay.compute_scale([]) == 1.0

    def test_all_zero_returns(self):
        """全零收益 → realized_vol=0 → max_scale"""
        overlay = VolatilityTargetOverlay(
            VolatilityTargetConfig(lookback_days=5, max_scale=1.0)
        )
        scale = overlay.compute_scale([0.0] * 10)
        assert scale == 1.0

    def test_exact_lookback_days(self):
        """恰好 lookback_days 条数据 → 正常计算"""
        overlay = VolatilityTargetOverlay(
            VolatilityTargetConfig(
                lookback_days=5, target_vol=0.20, min_scale=0.1, max_scale=1.0
            )
        )
        rets = [0.01, -0.01, 0.02, -0.02, 0.01]
        scale = overlay.compute_scale(rets)
        assert 0.1 <= scale <= 1.0

    def test_max_scale_above_one_leverage(self):
        """max_scale > 1.0 即杠杆场景"""
        overlay = VolatilityTargetOverlay(
            VolatilityTargetConfig(
                lookback_days=5,
                target_vol=0.50,  # very high target
                min_scale=0.2,
                max_scale=2.0,  # allow leverage
            )
        )
        # Very low vol → scale capped at max_scale
        rets = [0.0001, -0.0001, 0.0001, -0.0001, 0.0001]
        scale = overlay.compute_scale(rets)
        assert scale == 2.0

    def test_disabled_returns_one(self):
        overlay = VolatilityTargetOverlay(
            VolatilityTargetConfig(enabled=False)
        )
        assert overlay.compute_scale([0.1, -0.1, 0.2]) == 1.0

    def test_high_vol_clamps_to_min(self):
        """极高波动率 → 钳位到 min_scale"""
        overlay = VolatilityTargetOverlay(
            VolatilityTargetConfig(
                lookback_days=5, target_vol=0.01, min_scale=0.2, max_scale=1.0
            )
        )
        rets = [0.10, -0.10, 0.10, -0.10, 0.10]  # very volatile
        scale = overlay.compute_scale(rets)
        assert scale == 0.2


# ============================================================================
# DrawdownPositionReducer edge cases
# ============================================================================


class TestDrawdownReducerEdge:

    def test_zero_nav(self):
        """current_nav=0 after peak → drawdown=1.0 → min_scale"""
        reducer = DrawdownPositionReducer(
            DrawdownControlConfig(threshold=0.05, max_threshold=0.10, min_scale=0.3)
        )
        reducer.compute_scale(100.0)  # set peak
        scale = reducer.compute_scale(0.0)
        assert scale == 0.3

    def test_negative_nav(self):
        """负 NAV → drawdown > 1.0 → min_scale, no crash"""
        reducer = DrawdownPositionReducer(
            DrawdownControlConfig(threshold=0.05, max_threshold=0.10, min_scale=0.3)
        )
        reducer.compute_scale(100.0)  # set peak
        scale = reducer.compute_scale(-10.0)
        assert scale == 0.3

    def test_exact_threshold_boundary(self):
        """drawdown == threshold → scale = 1.0 (or very close due to float)"""
        reducer = DrawdownPositionReducer(
            DrawdownControlConfig(threshold=0.05, max_threshold=0.10, min_scale=0.3)
        )
        reducer.compute_scale(100.0)  # peak
        # drawdown = 1 - 95/100 ≈ 0.05; float imprecision may push it just above
        scale = reducer.compute_scale(95.0)
        assert scale == pytest.approx(1.0, abs=0.01)

    def test_exact_max_threshold_boundary(self):
        """drawdown == max_threshold → scale ≈ min_scale"""
        reducer = DrawdownPositionReducer(
            DrawdownControlConfig(threshold=0.05, max_threshold=0.10, min_scale=0.3)
        )
        reducer.compute_scale(100.0)
        # drawdown = 1 - 90/100 ≈ 0.10 == max_threshold
        scale = reducer.compute_scale(90.0)
        assert scale == pytest.approx(0.3, abs=0.01)

    def test_threshold_equals_max_threshold_no_crash(self):
        """threshold == max_threshold → 退化配置, 不应崩溃"""
        reducer = DrawdownPositionReducer(
            DrawdownControlConfig(
                threshold=0.05, max_threshold=0.05, min_scale=0.3
            )
        )
        reducer.compute_scale(100.0)
        # drawdown ≈ 0.04 < threshold → 1.0
        assert reducer.compute_scale(96.0) == 1.0
        # drawdown ≈ 0.06 >= max_threshold → min_scale
        assert reducer.compute_scale(94.0) == 0.3
        # drawdown ≈ 0.05; due to float, either ≤ or ≥ triggers, no ZeroDivision
        scale = reducer.compute_scale(95.0)
        assert scale in (1.0, 0.3)  # one of the boundary handlers catches it

    def test_peak_never_set(self):
        """peak=0 → 1.0 (no drawdown possible)"""
        reducer = DrawdownPositionReducer()
        # First call with any positive NAV
        scale = reducer.compute_scale(50.0)
        assert scale == 1.0  # new peak, no drawdown

    def test_linear_interpolation_midpoint(self):
        """线性插值中间点"""
        reducer = DrawdownPositionReducer(
            DrawdownControlConfig(
                threshold=0.10, max_threshold=0.20, min_scale=0.0
            )
        )
        reducer.compute_scale(100.0)
        # drawdown = 0.15 → midpoint → scale = 0.5
        scale = reducer.compute_scale(85.0)
        assert abs(scale - 0.5) < 1e-9


# ============================================================================
# PositionSizingOverlay: vol-only / dd-only
# ============================================================================


class TestOverlayPartialEnable:

    def test_vol_only_dd_disabled(self):
        """仅 vol 启用, dd disabled → dd 天然返回 1.0"""
        overlay = PositionSizingOverlay(
            vol_config=VolatilityTargetConfig(
                lookback_days=5, target_vol=0.01, min_scale=0.2
            ),
            dd_config=DrawdownControlConfig(enabled=False),
        )
        overlay.reset()
        rets = [0.10, -0.10, 0.10, -0.10, 0.10]
        scale = overlay.compute_scale(rets, current_nav=1000.0)
        # dd disabled → 1.0, vol should dominate
        assert scale <= 1.0

    def test_dd_only_vol_disabled(self):
        """仅 dd 启用, vol disabled → vol 返回 1.0"""
        overlay = PositionSizingOverlay(
            vol_config=VolatilityTargetConfig(enabled=False),
            dd_config=DrawdownControlConfig(
                threshold=0.05, max_threshold=0.10, min_scale=0.3
            ),
        )
        overlay.reset()
        # First call sets peak
        s1 = overlay.compute_scale([], current_nav=100.0)
        assert s1 == 1.0
        # Then big drawdown
        s2 = overlay.compute_scale([], current_nav=85.0)
        assert s2 < 1.0  # dd is reducing

    def test_both_disabled(self):
        """两者都 disabled → scale = 1.0"""
        overlay = PositionSizingOverlay(
            vol_config=VolatilityTargetConfig(enabled=False),
            dd_config=DrawdownControlConfig(enabled=False),
        )
        scale = overlay.compute_scale([0.1, -0.1], current_nav=1000.0)
        assert scale == 1.0

    def test_reset_clears_dd_peak(self):
        """reset 应清除 DD 峰值"""
        overlay = PositionSizingOverlay(
            dd_config=DrawdownControlConfig(
                threshold=0.05, max_threshold=0.20, min_scale=0.1
            ),
        )
        overlay.compute_scale([], 100.0)  # peak = 100
        overlay.compute_scale([], 80.0)   # drawdown
        overlay.reset()
        # After reset, new peak from current call
        scale = overlay.compute_scale([], 80.0)
        assert scale == 1.0  # 80 is new peak, no drawdown
