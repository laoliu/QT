"""
成长因子测试

测试覆盖：
- RevenueGrowthFactor: 正常/缺失/NaN/metadata降级
- EarningsGrowthFactor: 扣非/非扣非/缺失
- RevenueAccelerationFactor: 正常/缺失/零加速
- SUEFactor: 正常计算/降级路径/短历史/零标准差
"""

from dataclasses import dataclass
from datetime import date

import pytest

from aipha.factors.base import FactorData, FactorResult
from aipha.factors.growth import (
    EarningsGrowthFactor,
    RevenueAccelerationFactor,
    RevenueGrowthFactor,
    SUEFactor,
)


def _make_data(
    symbol: str = "000001.SZ",
    trade_date: date | None = None,
    close: float = 10.0,
    metadata: dict | None = None,
) -> FactorData:
    d = trade_date or date(2024, 6, 30)
    return FactorData(
        symbol=symbol,
        trade_date=d,
        close=close,
        metadata=metadata or {},
    )


# ============================================================
# RevenueGrowthFactor
# ============================================================


class TestRevenueGrowthFactor:
    def test_name(self):
        f = RevenueGrowthFactor()
        assert f.name == "REVENUE_GROWTH"

    def test_compute_from_metadata(self):
        data = _make_data(metadata={"revenue_yoy": 25.5})
        result = RevenueGrowthFactor().compute(data)
        assert result.value == pytest.approx(25.5)
        assert result.symbol == "000001.SZ"

    def test_compute_missing_metadata_returns_nan(self):
        data = _make_data(metadata={})
        result = RevenueGrowthFactor().compute(data)
        assert result.value != result.value  # NaN

    def test_compute_none_value(self):
        data = _make_data(metadata={"revenue_yoy": None})
        result = RevenueGrowthFactor().compute(data)
        assert result.value != result.value  # NaN

    def test_compute_negative_growth(self):
        data = _make_data(metadata={"revenue_yoy": -10.0})
        result = RevenueGrowthFactor().compute(data)
        assert result.value == pytest.approx(-10.0)

    def test_compute_zero_growth(self):
        data = _make_data(metadata={"revenue_yoy": 0.0})
        result = RevenueGrowthFactor().compute(data)
        assert result.value == pytest.approx(0.0)


# ============================================================
# EarningsGrowthFactor
# ============================================================


class TestEarningsGrowthFactor:
    def test_name_deducted(self):
        f = EarningsGrowthFactor(use_deducted=True)
        assert "DEDUCTED" in f.name

    def test_name_non_deducted(self):
        f = EarningsGrowthFactor(use_deducted=False)
        assert f.name == "EARNINGS_GROWTH"

    def test_compute_deducted(self):
        data = _make_data(metadata={"dt_netprofit_yoy": 30.0})
        result = EarningsGrowthFactor(use_deducted=True).compute(data)
        assert result.value == pytest.approx(30.0)

    def test_compute_non_deducted(self):
        data = _make_data(metadata={"net_profit_yoy": 15.0})
        result = EarningsGrowthFactor(use_deducted=False).compute(data)
        assert result.value == pytest.approx(15.0)

    def test_compute_missing(self):
        data = _make_data(metadata={})
        result = EarningsGrowthFactor().compute(data)
        assert result.value != result.value

    def test_compute_extreme_growth(self):
        data = _make_data(metadata={"dt_netprofit_yoy": 500.0})
        result = EarningsGrowthFactor().compute(data)
        assert result.value == pytest.approx(500.0)


# ============================================================
# RevenueAccelerationFactor
# ============================================================


class TestRevenueAccelerationFactor:
    def test_name(self):
        assert RevenueAccelerationFactor().name == "REVENUE_ACCELERATION"

    def test_compute_positive_acceleration(self):
        data = _make_data(metadata={
            "revenue_yoy": 30.0,
            "revenue_yoy_prev": 20.0,
        })
        result = RevenueAccelerationFactor().compute(data)
        assert result.value == pytest.approx(10.0)

    def test_compute_negative_acceleration(self):
        data = _make_data(metadata={
            "revenue_yoy": 10.0,
            "revenue_yoy_prev": 25.0,
        })
        result = RevenueAccelerationFactor().compute(data)
        assert result.value == pytest.approx(-15.0)

    def test_compute_zero_acceleration(self):
        data = _make_data(metadata={
            "revenue_yoy": 20.0,
            "revenue_yoy_prev": 20.0,
        })
        result = RevenueAccelerationFactor().compute(data)
        assert result.value == pytest.approx(0.0)

    def test_compute_missing_prev(self):
        data = _make_data(metadata={"revenue_yoy": 30.0})
        result = RevenueAccelerationFactor().compute(data)
        assert result.value != result.value


# ============================================================
# SUEFactor
# ============================================================


class TestSUEFactor:
    def test_name(self):
        assert SUEFactor().name == "SUE"

    def test_compute_from_eps_history(self):
        # 6个EPS数据点 → 5个差异 → 可以计算 SUE
        eps_history = [1.0, 1.2, 1.1, 1.3, 1.15, 1.4]
        data = _make_data(metadata={"eps_history": eps_history})
        result = SUEFactor(min_history=4).compute(data)
        # 值应为有限数
        assert result.value == result.value  # not NaN

    def test_compute_short_history_fallback(self):
        # 历史太短走降级路径
        data = _make_data(metadata={
            "eps": 1.5,
            "eps_yoy_prev": 1.2,
            "eps_std": 0.3,
        })
        result = SUEFactor(min_history=4).compute(data)
        expected = (1.5 - 1.2) / 0.3
        assert result.value == pytest.approx(expected)

    def test_compute_zero_std_returns_nan(self):
        data = _make_data(metadata={
            "eps": 1.5,
            "eps_yoy_prev": 1.2,
            "eps_std": 0.0,
        })
        result = SUEFactor().compute(data)
        assert result.value != result.value  # NaN

    def test_compute_no_data(self):
        data = _make_data(metadata={})
        result = SUEFactor().compute(data)
        assert result.value != result.value

    def test_min_history_parameter(self):
        f = SUEFactor(min_history=8)
        assert f.min_history == 8
