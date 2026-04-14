"""MarketRegimeDetector 多信号增强测试

测试覆盖（新增功能）：
- 均线交叉信号（_ma_crossover_signal）
- 波动率信号（_volatility_signal）
- 多信号投票机制（detect）
- signal_detail 审计方法
- 新增属性（ma_short, ma_long, vol_scale_factor）
- 向后兼容（已有测试继续通过的验证交给全量测试）
"""

import math
import pytest

from aipha.risk.constraints import MarketRegimeDetector


class TestMarketRegimeNewAttributes:
    """新增属性测试"""

    def test_default_config_new_attrs(self):
        d = MarketRegimeDetector()
        assert d.ma_short == 10
        assert d.ma_long == 30
        assert d.vol_scale_factor == 0.8

    def test_custom_ma_config(self):
        d = MarketRegimeDetector(ma_short=5, ma_long=20, vol_scale_factor=0.7)
        assert d.ma_short == 5
        assert d.ma_long == 20
        assert d.vol_scale_factor == 0.7


class TestMACrossoverSignal:
    """均线交叉信号测试"""

    def test_insufficient_data(self):
        """数据不足 → NEUTRAL"""
        d = MarketRegimeDetector(ma_long=30)
        for i in range(20):
            d.update(3000.0 + i)
        assert d._ma_crossover_signal() == "NEUTRAL"

    def test_bull_crossover(self):
        """短均线 > 长均线 → BULL"""
        d = MarketRegimeDetector(ma_short=5, ma_long=20)
        # 先给 20 个低价格，再给 5 个高价格
        for _ in range(20):
            d.update(3000.0)
        for _ in range(5):
            d.update(3200.0)
        # 短均线 ≈ 3200, 长均线 ≈ 3050
        assert d._ma_crossover_signal() == "BULL"

    def test_bear_crossover(self):
        """短均线 << 长均线 → BEAR"""
        d = MarketRegimeDetector(ma_short=5, ma_long=20)
        for _ in range(20):
            d.update(3200.0)
        for _ in range(5):
            d.update(3000.0)
        # 短均线 ≈ 3000, 长均线 ≈ 3150
        # ratio = 3000/3150 ≈ 0.952 < 0.98
        assert d._ma_crossover_signal() == "BEAR"

    def test_neutral_near_equal(self):
        """短长均线接近 → NEUTRAL"""
        d = MarketRegimeDetector(ma_short=5, ma_long=20)
        for _ in range(30):
            d.update(3000.0)
        assert d._ma_crossover_signal() == "NEUTRAL"


class TestVolatilitySignal:
    """波动率信号测试"""

    def test_low_vol_neutral(self):
        """低波动率 → NEUTRAL"""
        d = MarketRegimeDetector(vol_lookback=20, high_vol_threshold=0.03)
        # 稳定价格
        for i in range(25):
            d.update(3000.0 + i * 0.1)
        assert d._volatility_signal() == "NEUTRAL"

    def test_high_vol_bear(self):
        """高波动率 → BEAR"""
        d = MarketRegimeDetector(vol_lookback=10, high_vol_threshold=0.01)
        # 剧烈波动
        for i in range(15):
            d.update(3000.0 + 200 * ((-1) ** i))
        assert d._volatility_signal() == "BEAR"

    def test_insufficient_data(self):
        """数据不足 → volatility=0 → NEUTRAL"""
        d = MarketRegimeDetector(vol_lookback=20)
        d.update(3000.0)
        assert d._volatility_signal() == "NEUTRAL"


class TestMultiSignalVoting:
    """多信号投票机制测试"""

    def test_all_bull_signals(self):
        """三个信号都看涨 → BULL"""
        d = MarketRegimeDetector(
            bull_threshold=0.05,
            momentum_lookback=20,
            ma_short=5,
            ma_long=10,
            high_vol_threshold=0.10,
        )
        # 创建强上涨趋势
        for i in range(30):
            d.update(3000.0 + i * 30)
        assert d.detect() == "BULL"

    def test_two_bear_signals(self):
        """两个信号看空 → BEAR"""
        d = MarketRegimeDetector(
            bear_threshold=-0.03,
            momentum_lookback=20,
            ma_short=5,
            ma_long=10,
            high_vol_threshold=0.001,  # 极低阈值使 vol_signal 触发 BEAR
        )
        # 下跌趋势 + 波动
        for i in range(30):
            d.update(3000.0 - i * 20 + 5 * ((-1) ** i))
        result = d.detect()
        # 至少 momentum 和 vol 应该给出 BEAR
        assert result == "BEAR"

    def test_mixed_signals_neutral(self):
        """信号混合 → NEUTRAL"""
        d = MarketRegimeDetector(
            bull_threshold=100,   # 极高，不会触发
            bear_threshold=-100,  # 极低，不会触发
            momentum_lookback=20,
            ma_short=5,
            ma_long=20,
            high_vol_threshold=100,  # 极高，不会触发
        )
        for i in range(70):
            d.update(3000.0 + i)
        assert d.detect() == "NEUTRAL"


class TestSignalDetail:
    """signal_detail 审计方法测试"""

    def test_returns_all_keys(self):
        d = MarketRegimeDetector()
        for i in range(70):
            d.update(3000.0 + i)
        detail = d.signal_detail()
        assert "momentum" in detail
        assert "ma_crossover" in detail
        assert "volatility" in detail
        assert "composite" in detail

    def test_valid_values(self):
        d = MarketRegimeDetector()
        for i in range(70):
            d.update(3000.0 + i)
        detail = d.signal_detail()
        valid = {"BULL", "NEUTRAL", "BEAR"}
        for key, val in detail.items():
            assert val in valid, f"{key}={val} not in {valid}"


class TestPositionScaleEnhanced:
    """增强的仓位缩放测试"""

    def test_vol_scale_factor_custom(self):
        """自定义 vol_scale_factor"""
        d = MarketRegimeDetector(
            vol_scale_factor=0.6,
            high_vol_threshold=0.001,  # 很容易触发
            vol_lookback=5,
            momentum_lookback=5,
            bull_threshold=100,
            bear_threshold=-100,
        )
        for i in range(10):
            d.update(3000 + 100 * ((-1) ** i))

        # 高波动 + NEUTRAL → 0.8 * 0.6 = 0.48
        scale = d.position_scale()
        assert scale <= 0.8  # 应受到 vol 惩罚


class TestHistoryTrimming:
    """历史数据裁剪考虑 ma_long"""

    def test_trim_includes_ma_long(self):
        """历史长度应覆盖 ma_long"""
        d = MarketRegimeDetector(
            momentum_lookback=20, ma_long=100, vol_lookback=10
        )
        for i in range(200):
            d.update(3000.0 + i)
        # max(20, 10, 100) + 10 = 110
        assert len(d._index_history) <= 115
        assert len(d._index_history) >= 100


class TestRSRSIntegration:
    """RSRS 信号集成到 MarketRegimeDetector。"""

    def test_default_no_rsrs(self):
        """默认 use_rsrs=False，不创建 RSRS 因子。"""
        d = MarketRegimeDetector()
        assert d.use_rsrs is False
        assert d._rsrs_factor is None

    def test_rsrs_enabled(self):
        """use_rsrs=True 时创建 RSRSTimingFactor。"""
        d = MarketRegimeDetector(use_rsrs=True)
        assert d.use_rsrs is True
        assert d._rsrs_factor is not None

    def test_rsrs_signal_detail(self):
        """启用 RSRS 后 signal_detail 包含 rsrs 键。"""
        d = MarketRegimeDetector(use_rsrs=True, momentum_lookback=5, ma_long=5)
        for i in range(20):
            d.update(3000.0 + i, index_high=3010.0 + i, index_low=2990.0 + i)
        detail = d.signal_detail()
        assert "rsrs" in detail
        assert detail["rsrs"] in ("BULL", "BEAR", "NEUTRAL")

    def test_rsrs_update_with_high_low(self):
        """update 接受 index_high/index_low 参数。"""
        d = MarketRegimeDetector(use_rsrs=True, momentum_lookback=5)
        # 应不报错
        d.update(3000.0, index_high=3050.0, index_low=2950.0)
        assert len(d._index_history) == 1

    def test_rsrs_backward_compat(self):
        """不启用 RSRS 时，旧接口不变。"""
        d = MarketRegimeDetector(momentum_lookback=5, ma_long=5)
        for i in range(20):
            d.update(3000.0 + i)
        detail = d.signal_detail()
        assert "rsrs" not in detail
        assert "composite" in detail

    def test_4signal_voting(self):
        """4 信号投票需要 3/4 一致。"""
        d = MarketRegimeDetector(
            use_rsrs=True, momentum_lookback=5, ma_long=5, vol_lookback=5
        )
        # 持续上涨
        for i in range(30):
            p = 3000.0 + i * 10
            d.update(p, index_high=p + 5, index_low=p - 5)
        regime = d.detect()
        # 趋势、均线、RSRS 可能都看涨，波动率正常
        assert regime in ("BULL", "NEUTRAL")

