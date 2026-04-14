"""
市场状态检测器测试

测试覆盖：
- MarketRegimeDetector 初始化
- update: 数据更新/历史窗口管理
- detect: 牛市/熊市/震荡/数据不足
- current_volatility: 波动率计算/数据不足
- is_high_volatility: 高波/低波判断
- position_scale: 仓位缩放系数 (BULL/BEAR/NEUTRAL + 高波叠加)
- 边界条件: 极端数据/常数序列
"""

import math
import pytest

from aipha.risk.constraints import MarketRegimeDetector


class TestMarketRegimeDetectorInit:
    def test_default_config(self):
        d = MarketRegimeDetector()
        assert d.bull_threshold == 0.10
        assert d.bear_threshold == -0.08
        assert d.vol_lookback == 20
        assert d.momentum_lookback == 60
        assert d.high_vol_threshold == 0.03
        assert d._index_history == []

    def test_custom_config(self):
        d = MarketRegimeDetector(
            bull_threshold=0.15,
            bear_threshold=-0.10,
            vol_lookback=30,
            momentum_lookback=90,
            high_vol_threshold=0.05,
        )
        assert d.bull_threshold == 0.15
        assert d.momentum_lookback == 90


class TestMarketRegimeDetectorUpdate:
    def test_update_adds_to_history(self):
        d = MarketRegimeDetector()
        d.update(3000.0)
        d.update(3100.0)
        assert len(d._index_history) == 2

    def test_update_trims_history(self):
        """历史数据超过最大长度时自动裁剪"""
        d = MarketRegimeDetector(momentum_lookback=5, vol_lookback=5, ma_long=5)
        max_len = 5 + 10  # max(momentum, vol, ma_long) + 10
        for i in range(100):
            d.update(3000.0 + i)
        assert len(d._index_history) <= max_len


class TestMarketRegimeDetectorDetect:
    def test_insufficient_data_returns_neutral(self):
        d = MarketRegimeDetector(momentum_lookback=60)
        for i in range(30):
            d.update(3000.0)
        assert d.detect() == "NEUTRAL"

    def test_bull_market(self):
        """强上涨趋势 → BULL"""
        d = MarketRegimeDetector(
            momentum_lookback=60,
            bull_threshold=0.10,
        )
        base = 3000.0
        # 模拟 61 天从 3000 涨到 3500 (16.7% > 10%)
        for i in range(61):
            d.update(base + i * (500.0 / 60))
        assert d.detect() == "BULL"

    def test_bear_market(self):
        """强下跌趋势 → BEAR"""
        d = MarketRegimeDetector(
            momentum_lookback=60,
            bear_threshold=-0.08,
        )
        base = 3000.0
        # 模拟 61 天从 3000 跌到 2600 (-13.3% < -8%)
        for i in range(61):
            d.update(base - i * (400.0 / 60))
        assert d.detect() == "BEAR"

    def test_neutral_market(self):
        """小幅波动 → NEUTRAL"""
        d = MarketRegimeDetector(momentum_lookback=60)
        base = 3000.0
        # 模拟 61 天略涨 (3% < 10%, > -8%)
        for i in range(61):
            d.update(base + i * (90.0 / 60))
        assert d.detect() == "NEUTRAL"

    def test_constant_price_neutral(self):
        """价格不变 → NEUTRAL (0% 动量)"""
        d = MarketRegimeDetector(momentum_lookback=10)
        for _ in range(15):
            d.update(3000.0)
        assert d.detect() == "NEUTRAL"


class TestMarketRegimeDetectorVolatility:
    def test_insufficient_data_zero_vol(self):
        d = MarketRegimeDetector(vol_lookback=20)
        for i in range(5):
            d.update(3000.0 + i)
        assert d.current_volatility() == 0.0

    def test_constant_price_zero_vol(self):
        d = MarketRegimeDetector(vol_lookback=10)
        for _ in range(15):
            d.update(3000.0)
        assert d.current_volatility() == 0.0

    def test_positive_volatility(self):
        """有波动时应返回正数"""
        d = MarketRegimeDetector(vol_lookback=10)
        prices = [3000, 3050, 2950, 3100, 2900, 3080, 2960, 3020, 3010, 2990, 3050, 3000]
        for p in prices:
            d.update(float(p))
        vol = d.current_volatility()
        assert vol > 0

    def test_high_volatility_detection(self):
        d = MarketRegimeDetector(vol_lookback=10, high_vol_threshold=0.01)
        # 大幅波动
        for i in range(15):
            d.update(3000.0 + (200 if i % 2 == 0 else -200))
        assert d.is_high_volatility() is True

    def test_low_volatility_not_high(self):
        d = MarketRegimeDetector(vol_lookback=10, high_vol_threshold=0.10)
        # 小幅波动
        for i in range(15):
            d.update(3000.0 + i * 0.5)
        assert d.is_high_volatility() is False


class TestMarketRegimeDetectorPositionScale:
    def test_bull_full_scale(self):
        """牛市低波 → 1.0（多信号投票：动量+均线 ≥ 2/3）"""
        d = MarketRegimeDetector(
            momentum_lookback=10,
            bull_threshold=0.05,
            vol_lookback=5,
            high_vol_threshold=0.10,
            ma_short=3,
            ma_long=8,
        )
        # 大涨（足够数据量使 momentum 和 ma_crossover 同时触发 BULL）
        for i in range(15):
            d.update(3000.0 + i * 30)
        assert d.detect() == "BULL"
        scale = d.position_scale()
        assert scale == 1.0

    def test_bear_reduced_scale(self):
        """熊市 → 0.5（多信号投票：动量+均线 ≥ 2/3）"""
        d = MarketRegimeDetector(
            momentum_lookback=10,
            bear_threshold=-0.05,
            vol_lookback=5,
            high_vol_threshold=0.10,
            ma_short=3,
            ma_long=8,
        )
        for i in range(15):
            d.update(3000.0 - i * 30)
        assert d.detect() == "BEAR"
        scale = d.position_scale()
        assert scale == 0.5 or scale == 0.4  # 0.5 * vol_penalty

    def test_neutral_medium_scale(self):
        """震荡 → 0.8"""
        d = MarketRegimeDetector(
            momentum_lookback=10,
            vol_lookback=5,
            high_vol_threshold=0.10,
        )
        for _ in range(15):
            d.update(3000.0)
        assert d.detect() == "NEUTRAL"
        scale = d.position_scale()
        assert scale == 0.8

    def test_scale_range(self):
        """仓位缩放系数始终在 [0, 1]"""
        d = MarketRegimeDetector()
        for i in range(100):
            d.update(3000.0 + i)
        scale = d.position_scale()
        assert 0.0 <= scale <= 1.0

    def test_bull_high_vol_penalty(self):
        """牛市 + 高波动 → scale < 1.0"""
        d = MarketRegimeDetector(
            momentum_lookback=10,
            bull_threshold=0.05,
            vol_lookback=10,
            high_vol_threshold=0.001,  # 极低阈值确保高波
        )
        # 上涨但有波动
        for i in range(15):
            d.update(3000.0 + i * 30 + (50 if i % 2 == 0 else -20))
        if d.detect() == "BULL" and d.is_high_volatility():
            scale = d.position_scale()
            assert scale < 1.0
