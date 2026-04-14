"""估值分位因子测试"""

import math
from datetime import date

import pytest

from aipha.factors.base import FactorData
from aipha.factors.value import ValuationPercentileFactor


class TestValuationPercentileFactor:
    """估值历史分位因子测试"""

    def test_name_pe(self):
        """PE 分位名称"""
        f = ValuationPercentileFactor(metric="pe")
        assert f.name == "PE_PERCENTILE"

    def test_name_pb(self):
        """PB 分位名称"""
        f = ValuationPercentileFactor(metric="pb")
        assert f.name == "PB_PERCENTILE"

    def test_lowest_percentile(self):
        """当前值为历史最低 → 分位 ≈ 0 → 因子值 ≈ 1"""
        f = ValuationPercentileFactor(metric="pe", min_history=5)
        history = list(range(10, 110, 10))  # 10..100
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"pe_ttm": 5.0, "pe_history": history},
        )
        r = f.compute(data)
        assert r.value > 0.9  # Very cheap

    def test_highest_percentile(self):
        """当前值为历史最高 → 分位 ≈ 1 → 因子值 ≈ 0"""
        f = ValuationPercentileFactor(metric="pe", min_history=5)
        history = list(range(10, 110, 10))
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"pe_ttm": 200.0, "pe_history": history},
        )
        r = f.compute(data)
        assert r.value < 0.1  # Very expensive

    def test_median_percentile(self):
        """中位数 → 因子值 ≈ 0.5"""
        f = ValuationPercentileFactor(metric="pe", min_history=5)
        history = list(range(10, 110, 10))  # 10..100, median ~55
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"pe_ttm": 55.0, "pe_history": history},
        )
        r = f.compute(data)
        assert 0.3 < r.value < 0.7

    def test_insufficient_history(self):
        """历史不足 → NaN"""
        f = ValuationPercentileFactor(metric="pe", min_history=60)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"pe": 20.0, "pe_history": [15.0, 18.0, 22.0]},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_missing_current_value(self):
        """缺少当前值 → NaN"""
        f = ValuationPercentileFactor(metric="pe", min_history=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"pe_history": list(range(10, 50, 5))},  # no pe_ttm
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_missing_history(self):
        """缺少历史数据 → NaN"""
        f = ValuationPercentileFactor(metric="pe", min_history=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"pe_ttm": 20.0},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_empty_metadata(self):
        """空 metadata"""
        f = ValuationPercentileFactor(metric="pe")
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={},
        )
        r = f.compute(data)
        assert math.isnan(r.value)

    def test_pb_metric(self):
        """PB 分位计算"""
        f = ValuationPercentileFactor(
            metric="pb", current_field="pb",
            lookback_field="pb_history", min_history=3,
        )
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "pb": 1.0,
                "pb_history": [0.5, 0.8, 1.2, 1.5, 2.0, 2.5, 3.0],
            },
        )
        r = f.compute(data)
        assert not math.isnan(r.value)
        assert 0 <= r.value <= 1

    def test_all_same_values(self):
        """所有历史值相同"""
        f = ValuationPercentileFactor(metric="pe", min_history=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"pe_ttm": 10.0, "pe_history": [10.0] * 10},
        )
        r = f.compute(data)
        assert not math.isnan(r.value)

    def test_negative_pe_in_history(self):
        """历史含负 PE（亏损）→ 被过滤（要求 >0）"""
        f = ValuationPercentileFactor(metric="pe", min_history=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={
                "pe_ttm": 15.0,
                "pe_history": [-20.0, -5.0, 10.0, 20.0, 30.0, 40.0],
            },
        )
        r = f.compute(data)
        # 负值被过滤，只剩 4 个正值 >= min_history=3
        assert not math.isnan(r.value)

    def test_result_metadata(self):
        """结果元数据包含分位信息"""
        f = ValuationPercentileFactor(metric="pe", min_history=3)
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 6, 15),
            metadata={"pe_ttm": 20.0, "pe_history": [10.0, 15.0, 20.0, 25.0, 30.0]},
        )
        r = f.compute(data)
        assert "percentile" in r.metadata
        assert "history_length" in r.metadata
