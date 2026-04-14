"""到期催化因子 (MaturityCatalyst) 测试。

覆盖：
- MaturityZone 分类 (classify_maturity_zone)
- compute_maturity_catalyst 催化分数（各区间 + 边界）
- compute_maturity_urgency 紧迫度
- days_to_maturity_from_str 日期解析
- MaturityCatalystThresholds 自定义阈值
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from aipha.factors.maturity_catalyst import (
    MaturityCatalystThresholds,
    MaturityZone,
    classify_maturity_zone,
    compute_maturity_catalyst,
    compute_maturity_urgency,
    days_to_maturity_from_str,
)


# ── classify_maturity_zone ────────────────────────────────


class TestClassifyMaturityZone:
    """classify_maturity_zone 分类测试。"""

    def test_imminent_zone_0_days(self):
        assert classify_maturity_zone(0) == MaturityZone.IMMINENT

    def test_imminent_zone_100_days(self):
        assert classify_maturity_zone(100) == MaturityZone.IMMINENT

    def test_imminent_boundary_179(self):
        assert classify_maturity_zone(179) == MaturityZone.IMMINENT

    def test_accelerating_zone_180(self):
        assert classify_maturity_zone(180) == MaturityZone.ACCELERATING

    def test_accelerating_zone_300(self):
        assert classify_maturity_zone(300) == MaturityZone.ACCELERATING

    def test_accelerating_boundary_364(self):
        assert classify_maturity_zone(364) == MaturityZone.ACCELERATING

    def test_peak_zone_365(self):
        assert classify_maturity_zone(365) == MaturityZone.PEAK

    def test_peak_zone_730(self):
        """约 2 年 → PEAK。"""
        assert classify_maturity_zone(730) == MaturityZone.PEAK

    def test_peak_boundary_1094(self):
        assert classify_maturity_zone(1094) == MaturityZone.PEAK

    def test_moderate_zone_1095(self):
        """约 3 年 → MODERATE。"""
        assert classify_maturity_zone(1095) == MaturityZone.MODERATE

    def test_moderate_zone_1500(self):
        assert classify_maturity_zone(1500) == MaturityZone.MODERATE

    def test_moderate_boundary_1824(self):
        assert classify_maturity_zone(1824) == MaturityZone.MODERATE

    def test_distant_zone_1825(self):
        """5 年 → DISTANT。"""
        assert classify_maturity_zone(1825) == MaturityZone.DISTANT

    def test_distant_zone_3650(self):
        """10 年 → DISTANT。"""
        assert classify_maturity_zone(3650) == MaturityZone.DISTANT

    def test_custom_thresholds(self):
        th = MaturityCatalystThresholds(
            imminent_days=90, accel_days=180,
            peak_days=730, moderate_days=1460,
        )
        assert classify_maturity_zone(85, th) == MaturityZone.IMMINENT
        assert classify_maturity_zone(95, th) == MaturityZone.ACCELERATING
        assert classify_maturity_zone(185, th) == MaturityZone.PEAK
        assert classify_maturity_zone(735, th) == MaturityZone.MODERATE
        assert classify_maturity_zone(1500, th) == MaturityZone.DISTANT


# ── compute_maturity_catalyst ─────────────────────────────


class TestComputeMaturityCatalyst:
    """compute_maturity_catalyst 催化分数测试。"""

    def test_negative_days_returns_zero(self):
        assert compute_maturity_catalyst(-10) == 0.0

    def test_zero_days_imminent_lower_bound(self):
        """0 天 → IMMINENT 区最低分 0.1"""
        assert compute_maturity_catalyst(0) == pytest.approx(0.1, abs=0.01)

    def test_imminent_180_boundary(self):
        """接近 180 天边界时 → 约 0.3"""
        score = compute_maturity_catalyst(179)
        assert 0.28 <= score <= 0.32

    def test_accelerating_zone_270(self):
        """约 9 个月 → 在 [0.4, 0.7] 范围中间"""
        score = compute_maturity_catalyst(270)
        assert 0.45 <= score <= 0.65

    def test_accelerating_boundary_approaching_peak(self):
        """接近 365 天 → 约 0.7"""
        score = compute_maturity_catalyst(364)
        assert 0.65 <= score <= 0.72

    def test_peak_zone_730_highest_region(self):
        """2 年 → PEAK 区核心，应接近最高分"""
        score = compute_maturity_catalyst(730)
        assert score >= 0.85

    def test_peak_center_is_maximum(self):
        """PEAK 区中心（约 730 天）的分数高于边界"""
        center = compute_maturity_catalyst(730)
        edge_low = compute_maturity_catalyst(400)
        edge_high = compute_maturity_catalyst(1050)
        assert center >= edge_low
        assert center >= edge_high

    def test_peak_zone_all_above_0_7(self):
        """PEAK 区所有天数的催化分数 ≥ 0.70"""
        for d in range(365, 1095, 50):
            assert compute_maturity_catalyst(d) >= 0.70

    def test_moderate_zone_1200(self):
        """约 3.3 年 → 中等催化"""
        score = compute_maturity_catalyst(1200)
        assert 0.30 <= score <= 0.55

    def test_moderate_declines_with_distance(self):
        """MODERATE 区内 → 越远分越低"""
        s1 = compute_maturity_catalyst(1100)
        s2 = compute_maturity_catalyst(1500)
        s3 = compute_maturity_catalyst(1800)
        assert s1 > s2 > s3

    def test_distant_zone_2000(self):
        """约 5.5 年 → 低催化"""
        score = compute_maturity_catalyst(2000)
        assert score <= 0.25

    def test_distant_zone_3650(self):
        """10 年 → 极低催化"""
        score = compute_maturity_catalyst(3650)
        assert score <= 0.10

    def test_very_distant_approaches_zero(self):
        """30 年 → ≈ 0"""
        score = compute_maturity_catalyst(10950)
        assert score < 0.01

    def test_values_always_in_range(self):
        """所有合理天数的催化分数在 [0, 1] 范围内"""
        for d in range(0, 5000, 30):
            score = compute_maturity_catalyst(d)
            assert 0.0 <= score <= 1.0, f"d={d}: score={score}"

    def test_monotonic_in_distant(self):
        """DISTANT 区内单调递减"""
        prev = compute_maturity_catalyst(1825)
        for d in range(1900, 4000, 100):
            cur = compute_maturity_catalyst(d)
            assert cur <= prev + 1e-9, f"d={d}: {cur} > {prev}"
            prev = cur


# ── compute_maturity_urgency ──────────────────────────────


class TestComputeMaturityUrgency:
    """compute_maturity_urgency 紧迫度测试。"""

    def test_zero_days_max_urgency(self):
        assert compute_maturity_urgency(0) == 1.0

    def test_negative_days_max_urgency(self):
        assert compute_maturity_urgency(-5) == 1.0

    def test_max_days_zero_urgency(self):
        assert compute_maturity_urgency(2190) == 0.0

    def test_beyond_max_zero_urgency(self):
        assert compute_maturity_urgency(5000) == 0.0

    def test_half_max_not_half(self):
        """sqrt 衰减：半程天数的紧迫度 > 0.5（非线性）"""
        u = compute_maturity_urgency(1095)  # half of 2190
        assert u == pytest.approx(1.0 - math.sqrt(0.5), abs=0.01)
        assert u > 0.25  # sqrt衰减在半程约0.293

    def test_monotonically_decreasing(self):
        prev = compute_maturity_urgency(0)
        for d in range(30, 2200, 30):
            cur = compute_maturity_urgency(d)
            assert cur <= prev
            prev = cur

    def test_custom_max_days(self):
        assert compute_maturity_urgency(500, max_days=500) == 0.0
        assert compute_maturity_urgency(0, max_days=500) == 1.0

    def test_values_in_range(self):
        for d in range(0, 3000, 50):
            u = compute_maturity_urgency(d)
            assert 0.0 <= u <= 1.0


# ── days_to_maturity_from_str ─────────────────────────────


class TestDaysToMaturityFromStr:
    """days_to_maturity_from_str 日期解析测试。"""

    def test_yyyymmdd_format(self):
        result = days_to_maturity_from_str("20260303", current_date=date(2026, 3, 3))
        assert result == 0

    def test_dash_format(self):
        result = days_to_maturity_from_str("2026-03-04", current_date=date(2026, 3, 3))
        assert result == 1

    def test_future_date(self):
        result = days_to_maturity_from_str("20270303", current_date=date(2026, 3, 3))
        assert result == 365

    def test_past_date_returns_zero(self):
        """已过期返回 0（不返回负数）"""
        result = days_to_maturity_from_str("20250101", current_date=date(2026, 3, 3))
        assert result == 0

    def test_empty_string_returns_none(self):
        assert days_to_maturity_from_str("") is None

    def test_invalid_string_returns_none(self):
        assert days_to_maturity_from_str("not-a-date") is None

    def test_none_current_date_uses_today(self):
        """不传 current_date 时使用 today（可运行但不精确断言）"""
        result = days_to_maturity_from_str("20990101")
        assert result is not None
        assert result > 0

    def test_consistent_with_manual_calc(self):
        result = days_to_maturity_from_str("20260310", current_date=date(2026, 3, 3))
        assert result == 7


# ── MaturityCatalystThresholds ────────────────────────────


class TestMaturityCatalystThresholds:
    """MaturityCatalystThresholds 配置测试。"""

    def test_defaults(self):
        th = MaturityCatalystThresholds()
        assert th.imminent_days == 180
        assert th.accel_days == 365
        assert th.peak_days == 1095
        assert th.moderate_days == 1825

    def test_custom_values(self):
        th = MaturityCatalystThresholds(
            imminent_days=90, accel_days=270,
            peak_days=900, moderate_days=1500,
        )
        assert th.imminent_days == 90
        assert th.accel_days == 270

    def test_frozen(self):
        th = MaturityCatalystThresholds()
        with pytest.raises(AttributeError):
            th.imminent_days = 100  # type: ignore[misc]
