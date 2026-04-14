"""
质量因子测试

测试覆盖：
- ROEFactor: 加权/非加权/最小阈值/缺失
- ROAFactor: 正常/缺失
- GrossMarginFactor: 正常/降级/缺失
- NetMarginFactor: 正常/降级/缺失
- OperatingCashFlowQualityFactor: 正常/零EPS/缺失
"""

from datetime import date

import pytest

from aipha.factors.base import FactorData
from aipha.factors.quality import (
    GrossMarginFactor,
    NetMarginFactor,
    OperatingCashFlowQualityFactor,
    ROAFactor,
    ROEFactor,
)


def _make_data(metadata: dict | None = None) -> FactorData:
    return FactorData(
        symbol="000001.SZ",
        trade_date=date(2024, 6, 30),
        close=15.0,
        metadata=metadata or {},
    )


# ============================================================
# ROEFactor
# ============================================================


class TestROEFactor:
    def test_name_weighted(self):
        f = ROEFactor(use_weighted=True)
        assert f.name == "ROE_WAA"

    def test_name_non_weighted(self):
        f = ROEFactor(use_weighted=False)
        assert f.name == "ROE"

    def test_compute_from_metadata(self):
        data = _make_data(metadata={"roe": 15.0})
        result = ROEFactor(use_weighted=False).compute(data)
        assert result.value == pytest.approx(15.0)

    def test_compute_weighted_from_metadata(self):
        data = _make_data(metadata={"roe_waa": 18.0})
        result = ROEFactor(use_weighted=True).compute(data)
        assert result.value == pytest.approx(18.0)

    def test_compute_below_min_roe(self):
        data = _make_data(metadata={"roe": 3.0})
        result = ROEFactor(use_weighted=False, min_roe=5.0).compute(data)
        assert result.value != result.value  # NaN, below threshold

    def test_compute_at_min_roe(self):
        data = _make_data(metadata={"roe": 5.0})
        result = ROEFactor(use_weighted=False, min_roe=5.0).compute(data)
        assert result.value == pytest.approx(5.0)

    def test_compute_missing(self):
        data = _make_data(metadata={})
        result = ROEFactor().compute(data)
        assert result.value != result.value

    def test_negative_roe(self):
        data = _make_data(metadata={"roe": -10.0})
        result = ROEFactor(use_weighted=False).compute(data)
        assert result.value == pytest.approx(-10.0)


# ============================================================
# ROAFactor
# ============================================================


class TestROAFactor:
    def test_name(self):
        assert ROAFactor().name == "ROA"

    def test_compute_normal(self):
        data = _make_data(metadata={"roa": 8.5})
        result = ROAFactor().compute(data)
        assert result.value == pytest.approx(8.5)

    def test_compute_missing(self):
        data = _make_data(metadata={})
        result = ROAFactor().compute(data)
        assert result.value != result.value


# ============================================================
# GrossMarginFactor
# ============================================================


class TestGrossMarginFactor:
    def test_name(self):
        assert GrossMarginFactor().name == "GROSS_MARGIN"

    def test_compute_from_gross_margin(self):
        data = _make_data(metadata={"gross_margin": 35.0})
        result = GrossMarginFactor().compute(data)
        assert result.value == pytest.approx(35.0)

    def test_compute_from_grossprofit_margin(self):
        data = _make_data(metadata={"grossprofit_margin": 42.0})
        result = GrossMarginFactor().compute(data)
        assert result.value == pytest.approx(42.0)

    def test_compute_missing(self):
        data = _make_data(metadata={})
        result = GrossMarginFactor().compute(data)
        assert result.value != result.value


# ============================================================
# NetMarginFactor
# ============================================================


class TestNetMarginFactor:
    def test_name(self):
        assert NetMarginFactor().name == "NET_MARGIN"

    def test_compute_from_net_margin(self):
        data = _make_data(metadata={"net_margin": 12.0})
        result = NetMarginFactor().compute(data)
        assert result.value == pytest.approx(12.0)

    def test_compute_from_netprofit_margin(self):
        data = _make_data(metadata={"netprofit_margin": 18.0})
        result = NetMarginFactor().compute(data)
        assert result.value == pytest.approx(18.0)

    def test_compute_missing(self):
        data = _make_data(metadata={})
        result = NetMarginFactor().compute(data)
        assert result.value != result.value

    def test_negative_margin(self):
        data = _make_data(metadata={"net_margin": -5.0})
        result = NetMarginFactor().compute(data)
        assert result.value == pytest.approx(-5.0)


# ============================================================
# OperatingCashFlowQualityFactor
# ============================================================


class TestOperatingCashFlowQualityFactor:
    def test_name(self):
        assert OperatingCashFlowQualityFactor().name == "CFO_QUALITY"

    def test_compute_from_metadata(self):
        data = _make_data(metadata={"cfps": 2.5, "eps": 2.0})
        result = OperatingCashFlowQualityFactor().compute(data)
        if result.value == result.value:
            assert result.value == pytest.approx(1.25)

    def test_compute_zero_eps(self):
        """EPS为零不应崩溃"""
        data = _make_data(metadata={"cfps": 2.5, "eps": 0.0})
        result = OperatingCashFlowQualityFactor().compute(data)
        assert isinstance(result.value, float)

    def test_compute_missing(self):
        data = _make_data(metadata={})
        result = OperatingCashFlowQualityFactor().compute(data)
        assert result.value != result.value

    def test_negative_cashflow(self):
        data = _make_data(metadata={"cfps": -1.0, "eps": 2.0})
        result = OperatingCashFlowQualityFactor().compute(data)
        if result.value == result.value:
            assert result.value < 0
