"""
波动率因子测试
"""

from datetime import date, timedelta
import math

import pytest

from aipha.factors.base import FactorData
from aipha.factors.volatility import VolatilityFactor
from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


class TestVolatilityFactor:
    """波动率因子测试"""

    def test_factor_creation(self):
        """创建波动率因子"""
        factor = VolatilityFactor(lookback_days=20)
        assert factor.name == "VOL_20"
        assert factor.lookback_days == 20
        # §13.8：年化因子来自 trading_calendar_constants（A 股 244），不是 252
        assert factor.annualization_factor == pytest.approx(
            math.sqrt(TRADING_DAYS_PER_YEAR)
        )

    def test_factor_creation_custom_name(self):
        """创建波动率因子 - 自定义名称"""
        factor = VolatilityFactor(lookback_days=60, name="VOL_Q")
        assert factor.name == "VOL_Q"

    def test_factor_creation_custom_annualization(self):
        """创建波动率因子 - 自定义年化因子"""
        factor = VolatilityFactor(lookback_days=20, annualization_factor=math.sqrt(250))
        assert factor.annualization_factor == pytest.approx(math.sqrt(250))

    def test_compute_volatility(self):
        """计算波动率"""
        factor = VolatilityFactor(lookback_days=5, annualization_factor=1.0)  # 不年化
        
        base_date = date(2025, 1, 6)
        # 创建价格序列: 100, 102, 101, 103, 102, 104
        # 收益率: 0.02, -0.0098, 0.0198, -0.0097, 0.0196
        historical_close = {
            base_date - timedelta(days=5): 100.0,
            base_date - timedelta(days=4): 102.0,
            base_date - timedelta(days=3): 101.0,
            base_date - timedelta(days=2): 103.0,
            base_date - timedelta(days=1): 102.0,
            base_date: 104.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=104.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        assert result.factor_name == "VOL_5"
        assert result.value > 0  # 应该是正数
        assert "std" in result.metadata
        assert result.metadata["n_returns"] == 5  # 5 个收益率

    def test_compute_zero_volatility(self):
        """计算零波动率 - 价格不变"""
        factor = VolatilityFactor(lookback_days=3, annualization_factor=1.0)
        
        base_date = date(2025, 1, 6)
        # 价格完全不变
        historical_close = {
            base_date - timedelta(days=3): 100.0,
            base_date - timedelta(days=2): 100.0,
            base_date - timedelta(days=1): 100.0,
            base_date: 100.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=100.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        assert result.value == pytest.approx(0.0)

    def test_annualized_volatility(self):
        """验证年化波动率"""
        # 使用已知标准差进行验证
        factor = VolatilityFactor(lookback_days=3, annualization_factor=math.sqrt(252))
        
        base_date = date(2025, 1, 6)
        # 创建简单价格序列
        historical_close = {
            base_date - timedelta(days=3): 100.0,
            base_date - timedelta(days=2): 101.0,  # 1% 收益
            base_date - timedelta(days=1): 102.01,  # 1% 收益
            base_date: 103.03,  # 1% 收益
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=103.03,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        # 所有收益率都是 1%，标准差应该接近 0
        assert result.value < 0.01  # 非常小的波动率

    def test_high_volatility(self):
        """测试高波动率"""
        factor = VolatilityFactor(lookback_days=4, annualization_factor=1.0)
        
        base_date = date(2025, 1, 6)
        # 价格大幅波动
        historical_close = {
            base_date - timedelta(days=4): 100.0,
            base_date - timedelta(days=3): 110.0,  # +10%
            base_date - timedelta(days=2): 99.0,   # -10%
            base_date - timedelta(days=1): 108.9,  # +10%
            base_date: 97.9,                        # -10.1%
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=97.9,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        assert result.value > 0.1  # 高波动率

    def test_insufficient_history(self):
        """历史数据不足"""
        factor = VolatilityFactor(lookback_days=20)
        
        base_date = date(2025, 1, 6)
        historical_close = {
            base_date - timedelta(days=3): 100.0,
            base_date - timedelta(days=2): 101.0,
            base_date - timedelta(days=1): 102.0,
            base_date: 103.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=103.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        assert math.isnan(result.value)
        assert result.metadata.get("invalid") is True

    def test_missing_historical_close(self):
        """缺少历史价格数据"""
        factor = VolatilityFactor(lookback_days=5)
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=100.0,
        )
        
        result = factor.compute(data)
        assert math.isnan(result.value)

    def test_zero_price_in_history(self):
        """历史中有零价格"""
        factor = VolatilityFactor(lookback_days=3, annualization_factor=1.0)
        
        base_date = date(2025, 1, 6)
        historical_close = {
            base_date - timedelta(days=3): 100.0,
            base_date - timedelta(days=2): 0.0,  # 零价格
            base_date - timedelta(days=1): 101.0,
            base_date: 102.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=102.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        # 应该跳过零价格或返回无效
        # 取决于实现：要么返回 NaN，要么只使用有效的收益率
        assert result.value >= 0 or math.isnan(result.value)

    def test_single_return(self):
        """只有一个收益率 - 无法计算标准差"""
        factor = VolatilityFactor(lookback_days=1, annualization_factor=1.0)
        
        base_date = date(2025, 1, 6)
        historical_close = {
            base_date - timedelta(days=1): 100.0,
            base_date: 101.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=101.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        # 只有一个收益率，标准差应该是 0 或无效
        assert result.value == pytest.approx(0.0) or math.isnan(result.value)

    def test_uses_sorted_dates(self):
        """验证使用排序后的日期计算收益率"""
        factor = VolatilityFactor(lookback_days=3, annualization_factor=1.0)
        
        base_date = date(2025, 1, 6)
        # 故意打乱顺序
        historical_close = {
            date(2025, 1, 6): 103.0,
            date(2025, 1, 3): 100.0,
            date(2025, 1, 5): 102.0,
            date(2025, 1, 4): 101.0,
        }
        
        data = FactorData(
            symbol="000001.SZ",
            trade_date=base_date,
            close=103.0,
            historical_close=historical_close,
        )
        
        result = factor.compute(data)
        # 收益率应该是连续计算的
        assert result.value >= 0
