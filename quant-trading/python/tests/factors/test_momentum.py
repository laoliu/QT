"""
动量因子测试
"""

from datetime import date, timedelta
import math

import pytest

from aipha.factors.base import FactorData
from aipha.factors.momentum import ReturnMomentumFactor


class TestReturnMomentumFactor:
    """动量因子测试"""

    def test_factor_creation(self):
        """创建动量因子"""
        factor = ReturnMomentumFactor(lookback_days=20)
        assert factor.name == "MOM_20"
        assert factor.lookback_days == 20

    def test_factor_creation_custom_name(self):
        """创建动量因子 - 自定义名称"""
        factor = ReturnMomentumFactor(lookback_days=60, name="MOM_Q")
        assert factor.name == "MOM_Q"

    def test_compute_momentum(self):
        """计算动量"""
        factor = ReturnMomentumFactor(lookback_days=5)
        
        # 创建历史价格数据
        base_date = date(2025, 1, 6)
        historical_close = {}
        prices = [10.0, 10.5, 11.0, 10.8, 11.2, 12.0]  # 6 天数据
        for i, price in enumerate(prices):
            d = base_date - timedelta(days=len(prices) - 1 - i)
            historical_close[d] = price
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=12.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        assert result.factor_name == "MOM_5"
        # 收益率 = (12.0 - 10.0) / 10.0 = 0.2 = 20%
        assert result.value == pytest.approx(0.2)
        assert result.metadata["start_price"] == pytest.approx(10.0)
        assert result.metadata["end_price"] == pytest.approx(12.0)

    def test_compute_negative_momentum(self):
        """计算负动量"""
        factor = ReturnMomentumFactor(lookback_days=3)
        
        base_date = date(2025, 1, 6)
        historical_close = {
            base_date - timedelta(days=3): 15.0,
            base_date - timedelta(days=2): 14.0,
            base_date - timedelta(days=1): 12.0,
            base_date: 10.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=10.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        # 收益率 = (10.0 - 15.0) / 15.0 = -0.333...
        assert result.value == pytest.approx(-1/3)

    def test_insufficient_history(self):
        """历史数据不足"""
        factor = ReturnMomentumFactor(lookback_days=20)
        
        base_date = date(2025, 1, 6)
        historical_close = {
            base_date - timedelta(days=3): 10.0,
            base_date - timedelta(days=2): 10.5,
            base_date - timedelta(days=1): 11.0,
            base_date: 11.5,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=11.5,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        assert math.isnan(result.value)
        assert result.metadata.get("invalid") is True
        assert "insufficient_history" in result.metadata.get("reason", "")

    def test_missing_historical_close(self):
        """缺少历史价格数据"""
        factor = ReturnMomentumFactor(lookback_days=5)
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.0,
        )
        
        result = factor.compute(data)
        assert math.isnan(result.value)

    def test_zero_start_price(self):
        """起始价格为零"""
        factor = ReturnMomentumFactor(lookback_days=3)
        
        base_date = date(2025, 1, 6)
        historical_close = {
            base_date - timedelta(days=3): 0.0,  # 零价格
            base_date - timedelta(days=2): 10.0,
            base_date - timedelta(days=1): 10.5,
            base_date: 11.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=11.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        assert math.isnan(result.value)

    def test_missing_current_close(self):
        """缺少当前收盘价"""
        factor = ReturnMomentumFactor(lookback_days=3)
        
        base_date = date(2025, 1, 6)
        historical_close = {
            base_date - timedelta(days=3): 10.0,
            base_date - timedelta(days=2): 10.5,
            base_date - timedelta(days=1): 11.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        assert math.isnan(result.value)

    def test_skip_recent_excludes_recent_days(self):
        """skip_recent > 0 时跳过最近 N 天计算动量"""
        factor = ReturnMomentumFactor(lookback_days=3, skip_recent=2)
        assert factor.name == "MOM_3_2"

        base_date = date(2025, 1, 10)
        # 需要 lookback_days + skip_recent + 1 = 6 个数据点
        historical_close = {
            date(2025, 1, 5): 10.0,   # start (idx -6)
            date(2025, 1, 6): 10.5,
            date(2025, 1, 7): 11.0,
            date(2025, 1, 8): 15.0,   # end (idx -3)
            date(2025, 1, 9): 20.0,   # skipped
            date(2025, 1, 10): 25.0,  # skipped
        }

        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=25.0,
            historical_close=historical_close,
        )

        result = factor.compute(data)
        # 动量 = (end - start) / start = (15.0 - 10.0) / 10.0 = 0.5
        assert result.value == pytest.approx(0.5)
        assert result.metadata["start_price"] == pytest.approx(10.0)
        assert result.metadata["end_price"] == pytest.approx(15.0)

    def test_lookback_uses_sorted_dates(self):
        """验证 lookback 使用排序后的日期"""
        factor = ReturnMomentumFactor(lookback_days=5)
        
        base_date = date(2025, 1, 10)
        # 故意打乱顺序的历史价格
        historical_close = {
            date(2025, 1, 7): 11.0,
            date(2025, 1, 5): 10.0,  # 最早的日期，应该是起点
            date(2025, 1, 6): 10.5,
            date(2025, 1, 9): 11.5,
            date(2025, 1, 8): 11.2,
            date(2025, 1, 10): 12.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=12.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        # 应该使用最早的价格 10.0 作为起点
        assert result.value == pytest.approx(0.2)  # (12 - 10) / 10 = 0.2
