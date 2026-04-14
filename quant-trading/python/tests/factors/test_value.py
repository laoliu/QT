"""
价值因子测试
"""

from datetime import date
import math

import pytest

from aipha.factors.base import FactorData
from aipha.factors.value import PEFactor, PBFactor, PSFactor


# ============================================================================
# PEFactor Tests
# ============================================================================


class TestPEFactor:
    """市盈率因子测试"""

    def test_ep_factor_creation(self):
        """创建 EP 因子"""
        factor = PEFactor(invert=True)
        assert factor.name == "EP"

    def test_pe_factor_creation(self):
        """创建 PE 因子"""
        factor = PEFactor(invert=False)
        assert factor.name == "PE"

    def test_compute_ep(self):
        """计算 EP 因子"""
        factor = PEFactor(invert=True)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"eps_ttm": 0.5},  # PE = 10 / 0.5 = 20
        )
        result = factor.compute(data)
        assert result.factor_name == "EP"
        assert result.value == pytest.approx(0.05)  # EP = 1/20 = 0.05
        assert result.metadata["pe"] == pytest.approx(20.0)

    def test_compute_pe(self):
        """计算 PE 因子"""
        factor = PEFactor(invert=False)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"eps_ttm": 0.5},
        )
        result = factor.compute(data)
        assert result.factor_name == "PE"
        assert result.value == pytest.approx(20.0)

    def test_missing_fundamentals(self):
        """缺少财务数据"""
        factor = PEFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
        )
        result = factor.compute(data)
        assert math.isnan(result.value)
        assert result.metadata.get("invalid") is True

    def test_missing_eps(self):
        """缺少 EPS"""
        factor = PEFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"bps": 8.0},  # 没有 EPS
        )
        result = factor.compute(data)
        assert math.isnan(result.value)

    def test_zero_eps(self):
        """EPS 为 0"""
        factor = PEFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"eps_ttm": 0.0},
        )
        result = factor.compute(data)
        assert math.isnan(result.value)

    def test_missing_close(self):
        """缺少收盘价"""
        factor = PEFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            fundamentals={"eps_ttm": 0.5},
        )
        result = factor.compute(data)
        assert math.isnan(result.value)


# ============================================================================
# PBFactor Tests
# ============================================================================


class TestPBFactor:
    """市净率因子测试"""

    def test_bp_factor_creation(self):
        """创建 BP 因子"""
        factor = PBFactor(invert=True)
        assert factor.name == "BP"

    def test_compute_bp(self):
        """计算 BP 因子"""
        factor = PBFactor(invert=True)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"bps": 5.0},  # PB = 10 / 5 = 2
        )
        result = factor.compute(data)
        assert result.factor_name == "BP"
        assert result.value == pytest.approx(0.5)  # BP = 1/2 = 0.5
        assert result.metadata["pb"] == pytest.approx(2.0)

    def test_missing_bps(self):
        """缺少每股净资产"""
        factor = PBFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"eps": 0.5},
        )
        result = factor.compute(data)
        assert math.isnan(result.value)

    def test_negative_bps(self):
        """每股净资产为负"""
        factor = PBFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"bps": -5.0},
        )
        result = factor.compute(data)
        assert math.isnan(result.value)


# ============================================================================
# PSFactor Tests
# ============================================================================


class TestPSFactor:
    """市销率因子测试"""

    def test_sp_factor_creation(self):
        """创建 SP 因子"""
        factor = PSFactor(invert=True)
        assert factor.name == "SP"

    def test_compute_sp(self):
        """计算 SP 因子"""
        factor = PSFactor(invert=True)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"revenue_per_share_ttm": 2.0},  # PS = 10 / 2 = 5
        )
        result = factor.compute(data)
        assert result.factor_name == "SP"
        assert result.value == pytest.approx(0.2)  # SP = 1/5 = 0.2
        assert result.metadata["ps"] == pytest.approx(5.0)

    def test_missing_revenue(self):
        """缺少营收数据"""
        factor = PSFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
            fundamentals={"eps": 0.5},
        )
        result = factor.compute(data)
        assert math.isnan(result.value)
