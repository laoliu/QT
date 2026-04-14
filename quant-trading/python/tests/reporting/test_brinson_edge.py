"""Brinson 归因模块边界测试 — Phase 41

覆盖 brinson_attribution.py 中未覆盖的边界场景：
- 空权重/收益
- 组合与基准行业集合不对齐
- 单行业
- 多期空/单期
- to_dict 输出验证
"""

from __future__ import annotations

from datetime import date

import pytest

from aipha.reporting.brinson_attribution import (
    BrinsonMultiPeriodResult,
    BrinsonSingleResult,
    brinson_multi_period,
    brinson_single_period,
)


# ============================================================
# 1. 空权重/空收益
# ============================================================
class TestBrinsonEmpty:
    def test_all_empty(self):
        """全空输入 → 所有效应为 0"""
        r = brinson_single_period({}, {}, {}, {})
        assert r.total_allocation == 0.0
        assert r.total_selection == 0.0
        assert r.total_interaction == 0.0
        assert r.total_active == 0.0
        assert r.portfolio_return == 0.0
        assert r.benchmark_return == 0.0

    def test_empty_portfolio(self):
        """组合为空但基准有权重 → allocation 全部为负(w_p=0)"""
        r = brinson_single_period(
            {}, {"tech": 0.5, "fin": 0.5},
            {}, {"tech": 0.04, "fin": 0.02},
        )
        # allocation = (0 - 0.5)*0.04 + (0 - 0.5)*0.02 = -0.03
        assert r.total_allocation == pytest.approx(-0.03)
        # selection = w_b*(r_p - r_b) = 0.5*(0-0.04) + 0.5*(0-0.02) = -0.03
        assert r.total_selection == pytest.approx(-0.03)
        assert r.portfolio_return == 0.0


# ============================================================
# 2. 行业集合不对齐
# ============================================================
class TestBrinsonMismatchSectors:
    def test_portfolio_extra_sector(self):
        """组合有额外行业（基准没有）"""
        r = brinson_single_period(
            {"tech": 0.5, "energy": 0.5},
            {"tech": 1.0},
            {"tech": 0.04, "energy": 0.06},
            {"tech": 0.03},
        )
        # energy: w_b=0, r_b=0 → allocation=(0.5-0)*0=0, selection=0, interaction=(0.5)*(0.06)=0.03
        assert "energy" in r.allocation
        assert r.interaction["energy"] == pytest.approx(0.03)

    def test_benchmark_extra_sector(self):
        """基准有额外行业（组合没有）"""
        r = brinson_single_period(
            {"tech": 1.0},
            {"tech": 0.5, "healthcare": 0.5},
            {"tech": 0.04},
            {"tech": 0.03, "healthcare": 0.05},
        )
        # healthcare: w_p=0, r_p=0 →
        # allocation = (0-0.5)*0.05 = -0.025
        assert r.allocation["healthcare"] == pytest.approx(-0.025)


# ============================================================
# 3. 单行业（退化场景）
# ============================================================
class TestBrinsonSingleSector:
    def test_single_sector_same_weight(self):
        """同权重单行业 → allocation=0, selection=w_b*(r_p-r_b)"""
        r = brinson_single_period(
            {"tech": 1.0}, {"tech": 1.0},
            {"tech": 0.05}, {"tech": 0.03},
        )
        assert r.total_allocation == 0.0
        assert r.total_selection == pytest.approx(0.02)
        assert r.total_interaction == 0.0
        assert r.total_active == pytest.approx(0.02)


# ============================================================
# 4. 超额收益一致性检验
# ============================================================
class TestBrinsonConsistency:
    def test_active_equals_port_minus_bench(self):
        """total_active 应等于 portfolio_return - benchmark_return"""
        r = brinson_single_period(
            {"A": 0.3, "B": 0.7},
            {"A": 0.5, "B": 0.5},
            {"A": 0.06, "B": 0.02},
            {"A": 0.04, "B": 0.03},
        )
        expected_active = r.portfolio_return - r.benchmark_return
        assert r.total_active == pytest.approx(expected_active, abs=1e-10)


# ============================================================
# 5. 多期归因：空 period_data
# ============================================================
class TestBrinsonMultiPeriodEdge:
    def test_empty_periods(self):
        r = brinson_multi_period({})
        assert len(r.periods) == 0
        assert r.cumulative_active == 0.0

    def test_single_period(self):
        data = {
            date(2023, 1, 3): (
                {"tech": 0.6, "fin": 0.4},
                {"tech": 0.5, "fin": 0.5},
                {"tech": 0.05, "fin": 0.02},
                {"tech": 0.04, "fin": 0.01},
            )
        }
        r = brinson_multi_period(data)
        assert len(r.periods) == 1
        single = r.periods[date(2023, 1, 3)]
        assert r.cumulative_allocation == pytest.approx(single.total_allocation)
        assert r.cumulative_active == pytest.approx(single.total_active)

    def test_two_periods_cumulative(self):
        data = {
            date(2023, 1, 3): (
                {"A": 1.0}, {"A": 1.0},
                {"A": 0.02}, {"A": 0.01},
            ),
            date(2023, 1, 4): (
                {"A": 1.0}, {"A": 1.0},
                {"A": 0.03}, {"A": 0.02},
            ),
        }
        r = brinson_multi_period(data)
        assert len(r.periods) == 2
        # Same weight → all selection: 1*(0.02-0.01) + 1*(0.03-0.02) = 0.02
        assert r.cumulative_selection == pytest.approx(0.02)
        assert r.cumulative_allocation == 0.0


# ============================================================
# 6. to_dict 输出验证
# ============================================================
class TestBrinsonToDict:
    def test_single_result_to_dict(self):
        r = brinson_single_period(
            {"X": 0.5, "Y": 0.5},
            {"X": 0.5, "Y": 0.5},
            {"X": 0.05, "Y": 0.03},
            {"X": 0.04, "Y": 0.02},
        )
        d = r.to_dict()
        assert "allocation" in d
        assert "selection" in d
        assert "interaction" in d
        assert "total_active" in d
        assert isinstance(d["total_active"], float)

    def test_multi_result_to_dict(self):
        r = BrinsonMultiPeriodResult()
        d = r.to_dict()
        assert d["num_periods"] == 0
        assert d["cumulative_active"] == 0.0

    def test_zero_weights_to_dict(self):
        """全零权重 → to_dict 不崩溃"""
        r = brinson_single_period(
            {"A": 0.0}, {"A": 0.0}, {"A": 0.0}, {"A": 0.0}
        )
        d = r.to_dict()
        assert all(v == 0.0 for v in d["allocation"].values())
