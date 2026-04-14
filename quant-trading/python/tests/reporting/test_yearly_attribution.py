"""逐年业绩归因模块测试

测试覆盖：
- yearly_breakdown: 正常多年、单年、部分年、空数据
- 费用归因
- 市场状态标注
- 最大回撤计算
- format_yearly_report
"""

from __future__ import annotations

import math
from datetime import date

import pytest

from aipha.reporting.yearly_attribution import (
    YearlyMetrics,
    _calc_max_drawdown,
    format_yearly_report,
    yearly_breakdown,
)


def _make_nav_series(
    start_year: int = 2022,
    end_year: int = 2024,
    days_per_year: int = 252,
    annual_return: float = 0.10,
) -> list[tuple[date, float]]:
    """生成多年 NAV 序列（简单线性增长）"""
    nav = 1_000_000.0
    daily_ret = annual_return / days_per_year
    series = []
    for year in range(start_year, end_year + 1):
        for day in range(1, days_per_year + 1):
            # 近似日期
            month = min(12, (day - 1) // 21 + 1)
            dom = (day - 1) % 21 + 1
            try:
                d = date(year, month, min(dom, 28))
            except ValueError:
                d = date(year, month, 28)
            series.append((d, nav))
            nav *= (1 + daily_ret)
    return series


# ============================================================
# yearly_breakdown — 基本功能
# ============================================================


class TestYearlyBreakdown:

    def test_multi_year_all_covered(self):
        """多年 NAV → 返回所有年份"""
        navs = _make_nav_series(2022, 2024, days_per_year=50, annual_return=0.15)
        result = yearly_breakdown(navs)
        years = [r.year for r in result]
        assert 2022 in years
        assert 2023 in years
        assert 2024 in years

    def test_single_year_extraction(self):
        """单年 NAV 序列正确拆解"""
        navs = _make_nav_series(2024, 2024, days_per_year=50, annual_return=0.20)
        result = yearly_breakdown(navs)
        assert len(result) == 1
        r = result[0]
        assert r.year == 2024
        assert r.total_return > 0
        assert r.trading_days == 50

    def test_partial_year_head(self):
        """年初不完整（从 6 月开始）"""
        navs = [
            (date(2024, 6, 1), 1_000_000.0),
            (date(2024, 7, 1), 1_010_000.0),
            (date(2024, 8, 1), 1_020_000.0),
            (date(2024, 9, 1), 1_050_000.0),
        ]
        result = yearly_breakdown(navs)
        assert len(result) == 1
        assert result[0].year == 2024
        assert result[0].trading_days == 4

    def test_partial_year_tail(self):
        """年末不完整：2023 只有 3 个交易日 + 2024 完整"""
        navs = [
            (date(2023, 12, 28), 1_000_000.0),
            (date(2023, 12, 29), 1_005_000.0),
            (date(2023, 12, 30), 1_010_000.0),
            (date(2024, 1, 2), 1_012_000.0),
            (date(2024, 1, 3), 1_015_000.0),
        ]
        result = yearly_breakdown(navs)
        assert len(result) == 2
        assert result[0].year == 2023
        assert result[0].trading_days == 3
        assert result[1].year == 2024

    def test_empty_navs_returns_empty(self):
        """空 NAV 序列不崩溃"""
        result = yearly_breakdown([])
        assert result == []


# ============================================================
# 费用归因
# ============================================================


class TestCostAttribution:

    def test_fee_attribution_by_year(self):
        """费用按年拆分"""
        navs = [
            (date(2023, 6, 1), 1_000_000.0),
            (date(2023, 12, 31), 1_100_000.0),
            (date(2024, 6, 1), 1_150_000.0),
        ]
        costs = [
            (date(2023, 7, 1), 500.0),
            (date(2023, 10, 1), 300.0),
            (date(2024, 3, 1), 200.0),
        ]
        result = yearly_breakdown(navs, cost_series=costs)
        assert result[0].total_cost == 800.0  # 2023: 500+300
        assert result[1].total_cost == 200.0  # 2024: 200

    def test_cost_before_after_comparison(self):
        """成本前后对比"""
        navs = [
            (date(2024, 1, 1), 1_000_000.0),
            (date(2024, 12, 31), 1_100_000.0),
        ]
        costs = [(date(2024, 6, 1), 5_000.0)]
        result = yearly_breakdown(navs, cost_series=costs)
        r = result[0]
        assert r.total_return == pytest.approx(0.10)
        assert r.return_before_cost is not None
        assert r.return_before_cost > r.total_return

    def test_no_cost_no_before_cost(self):
        """无费用 → return_before_cost = None"""
        navs = [
            (date(2024, 1, 1), 1_000_000.0),
            (date(2024, 12, 31), 1_100_000.0),
        ]
        result = yearly_breakdown(navs)
        assert result[0].return_before_cost is None


# ============================================================
# 市场状态标注
# ============================================================


class TestRegimeLabel:

    def test_bull_label(self):
        """年收益 > 10% → BULL"""
        navs = [
            (date(2024, 1, 1), 1_000_000.0),
            (date(2024, 12, 31), 1_200_000.0),  # +20%
        ]
        result = yearly_breakdown(navs)
        assert result[0].regime == "BULL"

    def test_bear_label(self):
        """年收益 < -10% → BEAR"""
        navs = [
            (date(2024, 1, 1), 1_000_000.0),
            (date(2024, 12, 31), 850_000.0),  # -15%
        ]
        result = yearly_breakdown(navs)
        assert result[0].regime == "BEAR"

    def test_neutral_label(self):
        """-10% < 年收益 < 10% → NEUTRAL"""
        navs = [
            (date(2024, 1, 1), 1_000_000.0),
            (date(2024, 12, 31), 1_050_000.0),  # +5%
        ]
        result = yearly_breakdown(navs)
        assert result[0].regime == "NEUTRAL"

    def test_custom_thresholds(self):
        """自定义阈值"""
        navs = [
            (date(2024, 1, 1), 1_000_000.0),
            (date(2024, 12, 31), 1_060_000.0),  # +6%
        ]
        # 使用 5% 作为牛市阈值
        result = yearly_breakdown(navs, bull_threshold=0.05)
        assert result[0].regime == "BULL"


# ============================================================
# 最大回撤
# ============================================================


class TestMaxDrawdown:

    def test_no_drawdown(self):
        """单调递增 → 回撤 = 0"""
        assert _calc_max_drawdown([100, 110, 120, 130]) == 0.0

    def test_simple_drawdown(self):
        """简单回撤"""
        dd = _calc_max_drawdown([100, 120, 96, 110])
        assert dd == pytest.approx(0.20)  # 120 → 96 = 20%

    def test_single_value(self):
        """单值 → 0"""
        assert _calc_max_drawdown([100]) == 0.0

    def test_empty_list(self):
        """空列表 → 0"""
        assert _calc_max_drawdown([]) == 0.0

    def test_deep_drawdown(self):
        """深度回撤"""
        dd = _calc_max_drawdown([100, 150, 50, 80])
        expected = (150 - 50) / 150  # ~66.7%
        assert dd == pytest.approx(expected)


# ============================================================
# YearlyMetrics.to_dict
# ============================================================


class TestYearlyMetricsDict:

    def test_to_dict_all_fields(self):
        m = YearlyMetrics(
            year=2024,
            start_nav=1_000_000.0,
            end_nav=1_100_000.0,
            total_return=0.10,
            annual_return=0.10,
            max_drawdown=0.05,
            sharpe_ratio=1.5,
            volatility=0.15,
            trading_days=252,
            regime="BULL",
            total_cost=500.0,
            return_before_cost=0.105,
        )
        d = m.to_dict()
        assert d["year"] == 2024
        assert d["regime"] == "BULL"
        assert d["return_before_cost"] == 0.105


# ============================================================
# format_yearly_report
# ============================================================


class TestFormatReport:

    def test_format_nonempty(self):
        """格式化输出包含所有年份"""
        navs = _make_nav_series(2022, 2023, days_per_year=20, annual_return=0.15)
        yearly = yearly_breakdown(navs)
        text = format_yearly_report(yearly, strategy_name="TestStrategy")
        assert "TestStrategy" in text
        assert "2022" in text
        assert "2023" in text

    def test_format_empty(self):
        """空列表不崩溃"""
        text = format_yearly_report([])
        assert isinstance(text, str)
