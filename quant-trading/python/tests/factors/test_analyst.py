"""分析师因子占位接口测试"""

import math
from datetime import date

import pytest

from aipha.factors.base import FactorData
from aipha.factors.analyst import AnalystConsensusFactor, EarningsRevisionFactor


class TestAnalystConsensusFactor:
    """分析师一致预期因子测试"""

    def test_name(self):
        """因子名称"""
        f = AnalystConsensusFactor()
        assert f.name == "ANALYST_CONSENSUS"

    def test_no_data_returns_nan(self):
        """无注入数据时返回 NaN"""
        f = AnalystConsensusFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
        )
        r = f.compute(data)
        assert math.isnan(r.value)
        assert "预留" in r.metadata.get("reason", "") or "未接入" in r.metadata.get("reason", "")

    def test_injected_data(self):
        """通过 metadata 注入评级数据（需 rating + count >= min_coverage）"""
        f = AnalystConsensusFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"analyst_rating": 4.5, "analyst_count": 5},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(4.5)

    def test_insufficient_coverage(self):
        """覆盖不足依然返回 NaN"""
        f = AnalystConsensusFactor(min_coverage=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"analyst_rating": 4.0, "analyst_count": 2},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_rating_boundary(self):
        """评级边界值"""
        f = AnalystConsensusFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"analyst_rating": 1.0, "analyst_count": 3},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(1.0)

    def test_only_rating_no_count(self):
        """只有 rating 没有 count → NaN"""
        f = AnalystConsensusFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"analyst_rating": 4.0},
        )
        r = f.compute(data)
        assert math.isnan(r.value)


class TestEarningsRevisionFactor:
    """盈利预期修正因子测试"""

    def test_name(self):
        """因子名称"""
        f = EarningsRevisionFactor()
        assert f.name == "EARNINGS_REVISION"

    def test_no_data_returns_nan(self):
        """无注入数据时返回 NaN"""
        f = EarningsRevisionFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_injected_data(self):
        """通过 metadata 注入修正数据"""
        f = EarningsRevisionFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"earnings_revision": 0.15},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(0.15)

    def test_negative_revision(self):
        """负向修正"""
        f = EarningsRevisionFactor()
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"earnings_revision": -0.10},
        )
        r = f.compute(data)
        assert r.value == pytest.approx(-0.10)
