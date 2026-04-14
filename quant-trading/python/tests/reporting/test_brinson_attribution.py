"""Brinson 收益归因测试"""

from datetime import date

import pytest

from aipha.reporting.brinson_attribution import (
    BrinsonMultiPeriodResult,
    BrinsonSingleResult,
    brinson_multi_period,
    brinson_single_period,
)


# ---------------------------------------------------------------------------
# brinson_single_period 测试
# ---------------------------------------------------------------------------


class TestBrinsonSinglePeriod:
    """单期归因测试"""

    def test_identical_portfolios(self):
        """组合 == 基准 → 超额为 0"""
        w = {"tech": 0.4, "finance": 0.6}
        r = {"tech": 0.05, "finance": 0.02}
        result = brinson_single_period(w, w, r, r)

        assert result.total_allocation == pytest.approx(0.0)
        assert result.total_selection == pytest.approx(0.0)
        assert result.total_interaction == pytest.approx(0.0)
        assert result.total_active == pytest.approx(0.0)

    def test_pure_allocation_effect(self):
        """仅权重不同、收益相同 → 只有配置效应"""
        pw = {"tech": 0.6, "finance": 0.4}
        bw = {"tech": 0.4, "finance": 0.6}
        r = {"tech": 0.05, "finance": 0.02}  # 相同收益

        result = brinson_single_period(pw, bw, r, r)

        # selection = w_b * (r_p - r_b) = 0, interaction = Δw * Δr = 0
        assert result.total_selection == pytest.approx(0.0)
        assert result.total_interaction == pytest.approx(0.0)
        # allocation > 0 因为超配了 tech（高收益）
        assert result.total_allocation > 0
        assert result.total_active == pytest.approx(result.total_allocation)

    def test_pure_selection_effect(self):
        """仅收益不同、权重相同 → 只有选择效应"""
        w = {"tech": 0.5, "finance": 0.5}
        pr = {"tech": 0.06, "finance": 0.03}
        br = {"tech": 0.04, "finance": 0.02}

        result = brinson_single_period(w, w, pr, br)

        assert result.total_allocation == pytest.approx(0.0)
        assert result.total_interaction == pytest.approx(0.0)
        assert result.total_selection > 0

    def test_decomposition_sums_correctly(self):
        """配置 + 选择 + 交互 = 超额收益"""
        pw = {"tech": 0.5, "fin": 0.3, "cons": 0.2}
        bw = {"tech": 0.3, "fin": 0.4, "cons": 0.3}
        pr = {"tech": 0.06, "fin": 0.02, "cons": 0.04}
        br = {"tech": 0.04, "fin": 0.01, "cons": 0.03}

        result = brinson_single_period(pw, bw, pr, br)

        expected_active = result.portfolio_return - result.benchmark_return
        assert result.total_active == pytest.approx(expected_active, abs=1e-10)

    def test_sector_breakdown(self):
        """检查各行业的分解细节"""
        pw = {"tech": 0.6, "finance": 0.4}
        bw = {"tech": 0.4, "finance": 0.6}
        pr = {"tech": 0.05, "finance": 0.02}
        br = {"tech": 0.04, "finance": 0.01}

        result = brinson_single_period(pw, bw, pr, br)

        # tech: allocation = (0.6-0.4)*0.04 = 0.008
        assert result.allocation["tech"] == pytest.approx(0.008)
        # tech: selection = 0.4*(0.05-0.04) = 0.004
        assert result.selection["tech"] == pytest.approx(0.004)
        # tech: interaction = (0.6-0.4)*(0.05-0.04) = 0.002
        assert result.interaction["tech"] == pytest.approx(0.002)

    def test_missing_sector_in_portfolio(self):
        """组合缺少某行业 → 该行业权重视为 0"""
        pw = {"tech": 1.0}
        bw = {"tech": 0.5, "finance": 0.5}
        pr = {"tech": 0.05}
        br = {"tech": 0.04, "finance": 0.02}

        result = brinson_single_period(pw, bw, pr, br)

        # finance: allocation = (0 - 0.5) * 0.02 = -0.01
        assert result.allocation["finance"] == pytest.approx(-0.01)
        # finance: selection = 0.5 * (0 - 0.02) = -0.01
        assert result.selection["finance"] == pytest.approx(-0.01)

    def test_to_dict(self):
        """序列化测试"""
        pw = {"tech": 0.5, "finance": 0.5}
        bw = {"tech": 0.5, "finance": 0.5}
        pr = {"tech": 0.05, "finance": 0.02}
        br = {"tech": 0.04, "finance": 0.02}

        result = brinson_single_period(pw, bw, pr, br)
        d = result.to_dict()

        assert "allocation" in d
        assert "selection" in d
        assert "interaction" in d
        assert "total_active" in d
        assert isinstance(d["allocation"], dict)

    def test_negative_returns(self):
        """负收益时归因依然正确"""
        pw = {"A": 0.6, "B": 0.4}
        bw = {"A": 0.4, "B": 0.6}
        pr = {"A": -0.02, "B": -0.05}
        br = {"A": -0.03, "B": -0.04}

        result = brinson_single_period(pw, bw, pr, br)
        expected_active = result.portfolio_return - result.benchmark_return
        assert result.total_active == pytest.approx(expected_active, abs=1e-10)

    def test_single_sector(self):
        """单一行业时配置效应为 0"""
        pw = {"tech": 1.0}
        bw = {"tech": 1.0}
        pr = {"tech": 0.06}
        br = {"tech": 0.04}

        result = brinson_single_period(pw, bw, pr, br)

        assert result.total_allocation == pytest.approx(0.0)
        assert result.total_selection == pytest.approx(0.02)
        assert result.total_interaction == pytest.approx(0.0)

    def test_empty_portfolios(self):
        """空组合"""
        result = brinson_single_period({}, {}, {}, {})

        assert result.total_active == pytest.approx(0.0)
        assert result.portfolio_return == pytest.approx(0.0)
        assert result.benchmark_return == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# brinson_multi_period 测试
# ---------------------------------------------------------------------------


class TestBrinsonMultiPeriod:
    """多期归因测试"""

    def test_single_period_matches(self):
        """单期多期归因结果一致"""
        pw = {"tech": 0.5, "fin": 0.5}
        bw = {"tech": 0.4, "fin": 0.6}
        pr = {"tech": 0.05, "fin": 0.02}
        br = {"tech": 0.04, "fin": 0.01}

        single = brinson_single_period(pw, bw, pr, br)
        multi = brinson_multi_period({
            date(2023, 1, 3): (pw, bw, pr, br),
        })

        assert multi.cumulative_allocation == pytest.approx(single.total_allocation)
        assert multi.cumulative_selection == pytest.approx(single.total_selection)
        assert multi.cumulative_active == pytest.approx(single.total_active)
        assert len(multi.periods) == 1

    def test_multi_period_cumulative(self):
        """多期累计约等于各期之和"""
        data = {
            date(2023, 1, 3): (
                {"tech": 0.5, "fin": 0.5},
                {"tech": 0.4, "fin": 0.6},
                {"tech": 0.05, "fin": 0.02},
                {"tech": 0.04, "fin": 0.01},
            ),
            date(2023, 1, 4): (
                {"tech": 0.55, "fin": 0.45},
                {"tech": 0.4, "fin": 0.6},
                {"tech": 0.03, "fin": 0.04},
                {"tech": 0.02, "fin": 0.03},
            ),
        }

        result = brinson_multi_period(data)
        assert len(result.periods) == 2

        sum_alloc = sum(p.total_allocation for p in result.periods.values())
        assert result.cumulative_allocation == pytest.approx(sum_alloc)

    def test_empty_data(self):
        """空数据"""
        result = brinson_multi_period({})
        assert result.cumulative_active == pytest.approx(0.0)
        assert len(result.periods) == 0

    def test_to_dict(self):
        """序列化测试"""
        data = {
            date(2023, 1, 3): (
                {"A": 1.0}, {"A": 1.0}, {"A": 0.05}, {"A": 0.03}
            ),
        }
        result = brinson_multi_period(data)
        d = result.to_dict()

        assert "num_periods" in d
        assert d["num_periods"] == 1
        assert "cumulative_allocation" in d

    def test_periods_sorted_by_date(self):
        """期间按日期排序"""
        dates = [date(2023, 1, 5), date(2023, 1, 3), date(2023, 1, 4)]
        data = {
            d: ({"A": 1.0}, {"A": 1.0}, {"A": 0.01}, {"A": 0.01})
            for d in dates
        }
        result = brinson_multi_period(data)
        assert list(result.periods.keys()) == sorted(dates)


# ---------------------------------------------------------------------------
# 通过 reporting 包导入
# ---------------------------------------------------------------------------


class TestBrinsonNotionalImport:
    """通过 reporting 包导入 Brinson"""

    def test_import_from_package(self):
        """测试从 reporting 包导入"""
        from aipha.reporting import (
            BrinsonMultiPeriodResult,
            BrinsonSingleResult,
            brinson_multi_period,
            brinson_single_period,
        )

        assert BrinsonSingleResult is not None
        assert BrinsonMultiPeriodResult is not None
        assert callable(brinson_single_period)
        assert callable(brinson_multi_period)
