"""test_slippage_stress.py — 滑点压力测试工具测试。

覆盖要求：≥ 10 测试，正常/边界/空数据/极端值。
"""
from __future__ import annotations

import pytest

from aipha.backtest.slippage_stress import (
    DEFAULT_STRESS_BPS_LEVELS,
    SlippagePoint,
    SlippageStressResult,
    stress_test_slippage,
    _find_survival_bps,
    _interpolate,
    _linear_decay_rate,
)


# ============================================================================
# SlippagePoint
# ============================================================================


class TestSlippagePoint:
    def test_basic(self):
        p = SlippagePoint(bps=10.0, ann_ret=0.15, sharpe=0.8)
        assert p.bps == 10.0
        assert p.ann_ret == 0.15

    def test_frozen(self):
        p = SlippagePoint(bps=5.0, ann_ret=0.20, sharpe=1.0)
        with pytest.raises(AttributeError):
            p.bps = 20.0  # type: ignore[misc]


# ============================================================================
# _find_survival_bps
# ============================================================================


class TestFindSurvivalBps:
    def test_all_positive(self):
        pts = (
            SlippagePoint(3, 0.25, 1.2),
            SlippagePoint(10, 0.15, 0.8),
            SlippagePoint(25, 0.05, 0.2),
        )
        result = _find_survival_bps(pts)
        assert result == 25.0

    def test_transition_to_negative(self):
        pts = (
            SlippagePoint(3, 0.25, 1.2),
            SlippagePoint(10, 0.10, 0.5),
            SlippagePoint(25, -0.05, -0.2),
        )
        result = _find_survival_bps(pts)
        # 线性插值: 10 + 0.10 * (25-10)/(0.10+0.05) = 10 + 10.0 = 20.0
        assert result is not None
        assert 15.0 <= result <= 25.0

    def test_all_negative(self):
        pts = (
            SlippagePoint(3, -0.01, -0.1),
            SlippagePoint(10, -0.05, -0.3),
        )
        result = _find_survival_bps(pts)
        assert result is None

    def test_single_positive(self):
        pts = (SlippagePoint(5, 0.10, 0.5),)
        result = _find_survival_bps(pts)
        assert result == 5.0


# ============================================================================
# _interpolate
# ============================================================================


class TestInterpolate:
    def test_exact_match(self):
        pts = (
            SlippagePoint(5, 0.20, 1.0),
            SlippagePoint(15, 0.10, 0.5),
        )
        result = _interpolate(pts, 5.0, attr="ann_ret")
        assert result == pytest.approx(0.20)

    def test_midpoint(self):
        pts = (
            SlippagePoint(0, 0.30, 1.5),
            SlippagePoint(20, 0.10, 0.5),
        )
        result = _interpolate(pts, 10, attr="ann_ret")
        assert result == pytest.approx(0.20)

    def test_below_range(self):
        pts = (
            SlippagePoint(5, 0.20, 1.0),
            SlippagePoint(15, 0.10, 0.5),
        )
        result = _interpolate(pts, 2.0, attr="ann_ret")
        assert result == 0.20  # clamp to first

    def test_above_range(self):
        pts = (
            SlippagePoint(5, 0.20, 1.0),
            SlippagePoint(15, 0.10, 0.5),
        )
        result = _interpolate(pts, 30.0, attr="ann_ret")
        assert result == 0.10  # clamp to last

    def test_empty_points(self):
        result = _interpolate((), 10.0, attr="ann_ret")
        assert result == 0.0


# ============================================================================
# _linear_decay_rate
# ============================================================================


class TestLinearDecayRate:
    def test_negative_slope(self):
        pts = (
            SlippagePoint(0, 0.30, 1.5),
            SlippagePoint(10, 0.20, 1.0),
            SlippagePoint(20, 0.10, 0.5),
        )
        rate = _linear_decay_rate(pts)
        assert rate < 0
        assert rate == pytest.approx(-0.01, abs=0.001)

    def test_single_point(self):
        pts = (SlippagePoint(5, 0.15, 0.7),)
        assert _linear_decay_rate(pts) == 0.0

    def test_flat(self):
        pts = (
            SlippagePoint(5, 0.15, 0.7),
            SlippagePoint(25, 0.15, 0.7),
        )
        assert _linear_decay_rate(pts) == pytest.approx(0.0)


# ============================================================================
# stress_test_slippage（集成测试）
# ============================================================================


class TestStressTestSlippage:
    def test_robust_strategy(self):
        """在高滑点下收益仍为正 → 高分。"""
        pts = [
            SlippagePoint(3, 0.30, 1.5),
            SlippagePoint(10, 0.22, 1.1),
            SlippagePoint(15, 0.18, 0.9),
            SlippagePoint(25, 0.10, 0.5),
            SlippagePoint(40, 0.03, 0.1),
        ]
        result = stress_test_slippage(pts, realistic_bps=15.0)
        assert result.score >= 6.0
        assert "鲁棒" in result.verdict
        assert result.return_at_realistic == pytest.approx(0.18, abs=0.01)

    def test_fragile_strategy(self):
        """在合理滑点下为负 → 低分。"""
        pts = [
            SlippagePoint(3, 0.20, 1.0),
            SlippagePoint(5, 0.05, 0.2),
            SlippagePoint(10, -0.10, -0.5),
            SlippagePoint(15, -0.25, -1.0),
        ]
        result = stress_test_slippage(pts, realistic_bps=15.0)
        assert result.score < 4.0
        assert "脆弱" in result.verdict

    def test_insufficient_data(self):
        """不足 2 个点 → 数据不足。"""
        pts = [SlippagePoint(3, 0.20, 1.0)]
        result = stress_test_slippage(pts)
        assert result.score == 0.0
        assert "数据不足" in result.verdict

    def test_to_dict(self):
        pts = [
            SlippagePoint(5, 0.15, 0.7),
            SlippagePoint(15, 0.08, 0.4),
        ]
        result = stress_test_slippage(pts, realistic_bps=10.0)
        d = result.to_dict()
        assert "points" in d
        assert "score" in d
        assert "verdict" in d
        assert len(d["points"]) == 2

    def test_survival_bps_in_result(self):
        pts = [
            SlippagePoint(3, 0.20, 1.0),
            SlippagePoint(15, 0.05, 0.2),
            SlippagePoint(30, -0.10, -0.5),
        ]
        result = stress_test_slippage(pts, realistic_bps=15.0)
        assert result.survival_bps is not None
        assert result.survival_bps >= 15.0

    def test_default_stress_levels_exist(self):
        """默认测试序列至少包含 5 个水平。"""
        assert len(DEFAULT_STRESS_BPS_LEVELS) >= 5

    def test_sharpe_interpolation(self):
        pts = [
            SlippagePoint(0, 0.30, 1.5),
            SlippagePoint(20, 0.10, 0.5),
        ]
        result = stress_test_slippage(pts, realistic_bps=10.0)
        assert result.sharpe_at_realistic == pytest.approx(1.0, abs=0.01)
