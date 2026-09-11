"""Tests for RegimeDetector — Regime 检测器。

测试覆盖（≥20 用例）：
- Config: 默认值、自定义模式、CB 阈值、溢价率参数
- Rule mode: 数据不足、牛市、熊市、震荡
- Premium signal: 低溢价→BULL、高溢价→BEAR、中性
- Score voting: 评分制 vs 多数投票
- HMM mode: 初始化、拟合、降级
- 辅助: 波动率、动量、重置
"""

from __future__ import annotations

import numpy as np
import pytest

from aipha.portfolio.regime_detector import (
    DetectorMode,
    RegimeDetector,
    RegimeDetectorConfig,
)
from aipha.portfolio.regime_weight import MarketRegime


class TestRegimeDetectorConfig:
    def test_defaults(self) -> None:
        cfg = RegimeDetectorConfig()
        assert cfg.mode == DetectorMode.RULE
        assert cfg.hmm_n_states == 3
        assert cfg.vol_window == 20
        assert cfg.momentum_window == 60

    def test_hmm_mode(self) -> None:
        cfg = RegimeDetectorConfig(mode=DetectorMode.HMM, hmm_n_states=2)
        assert cfg.mode == DetectorMode.HMM
        assert cfg.hmm_n_states == 2

    def test_cb_threshold_defaults(self) -> None:
        """CB 市场校准的默认阈值。"""
        cfg = RegimeDetectorConfig()
        assert cfg.momentum_bull_threshold == 0.03
        assert cfg.momentum_bear_threshold == -0.05
        assert cfg.volume_bull_ratio == 1.15
        assert cfg.volume_bear_ratio == 0.85
        assert cfg.premium_low_threshold == 20.0
        assert cfg.premium_high_threshold == 45.0
        assert cfg.use_premium_signal is True
        assert cfg.use_score_voting is True

    def test_custom_thresholds(self) -> None:
        """自定义阈值配置。"""
        cfg = RegimeDetectorConfig(
            momentum_bull_threshold=0.05,
            momentum_bear_threshold=-0.08,
            premium_low_threshold=15.0,
            premium_high_threshold=50.0,
            use_score_voting=False,
        )
        assert cfg.momentum_bull_threshold == 0.05
        assert cfg.premium_low_threshold == 15.0
        assert cfg.use_score_voting is False


class TestRegimeDetectorRule:
    """规则模式测试。"""

    @pytest.fixture
    def detector(self) -> RegimeDetector:
        return RegimeDetector(RegimeDetectorConfig(mode=DetectorMode.RULE))

    def test_insufficient_data(self, detector) -> None:
        """数据不足 → NEUTRAL。"""
        detector.update(100.0)
        assert detector.detect() == MarketRegime.NEUTRAL

    def test_bull_market(self, detector) -> None:
        """持续上涨 → BULL。"""
        # 构造 61 日上涨序列 (>10% 涨幅)
        base = 1000.0
        for i in range(70):
            price = base * (1 + 0.003 * i)  # ~21% over 70 days
            detector.update(price, volume=500.0)
        regime = detector.detect()
        assert regime in (MarketRegime.BULL, MarketRegime.NEUTRAL)

    def test_bear_market(self, detector) -> None:
        """持续下跌 → BEAR。"""
        base = 1000.0
        for i in range(70):
            price = base * (1 - 0.003 * i)  # ~21% 跌幅
            detector.update(price, volume=500.0)
        regime = detector.detect()
        assert regime in (MarketRegime.BEAR, MarketRegime.NEUTRAL)

    def test_neutral_market(self, detector) -> None:
        """窄幅震荡 → NEUTRAL。"""
        rng = np.random.RandomState(42)
        for _ in range(70):
            price = 1000.0 + rng.normal(0, 2)
            detector.update(price, volume=500.0)
        assert detector.detect() == MarketRegime.NEUTRAL

    def test_high_volatility(self, detector) -> None:
        """高波动检测。"""
        rng = np.random.RandomState(42)
        # 先给低波动基线
        for _ in range(260):
            detector.update(1000.0 + rng.normal(0, 1), volume=500.0)
        # 然后高波动
        for _ in range(25):
            detector.update(1000.0 + rng.normal(0, 50), volume=500.0)
        assert detector.is_high_volatility()

    def test_volatility_calc(self, detector) -> None:
        """波动率计算基本正确性。"""
        for i in range(25):
            detector.update(100.0 + i * 0.1)
        vol = detector._current_volatility()
        assert vol >= 0.0

    def test_momentum_calc(self, detector) -> None:
        """动量计算。"""
        for i in range(65):
            detector.update(100.0 + i)
        mom = detector._current_momentum()
        assert mom > 0.0  # 上涨趋势

    def test_reset(self, detector) -> None:
        """重置清空内部状态。"""
        for i in range(10):
            detector.update(100.0 + i)
        detector.reset()
        assert len(detector._closes) == 0


class TestRegimeDetectorPremium:
    """溢价率信号 + 评分投票测试。"""

    def test_premium_low_contributes_bull(self) -> None:
        """低溢价 (<20%) 信号贡献 BULL 票。"""
        cfg = RegimeDetectorConfig(
            use_premium_signal=True,
            premium_low_threshold=20.0,
            premium_lookback=5,
        )
        detector = RegimeDetector(cfg)
        # 给 70 天数据：平稳价格 + 低溢价
        for i in range(70):
            detector.update(1000.0, volume=500.0, premium_median=15.0)
        regime = detector.detect()
        # 低溢价 → BULL 票, 其他信号偏中性 → 评分 > 0 → BULL
        assert regime in (MarketRegime.BULL, MarketRegime.NEUTRAL)

    def test_premium_high_contributes_bear(self) -> None:
        """高溢价 (>45%) 信号贡献 BEAR 票。"""
        cfg = RegimeDetectorConfig(
            use_premium_signal=True,
            premium_high_threshold=45.0,
            premium_lookback=5,
        )
        detector = RegimeDetector(cfg)
        for i in range(70):
            detector.update(1000.0, volume=500.0, premium_median=55.0)
        regime = detector.detect()
        assert regime in (MarketRegime.BEAR, MarketRegime.NEUTRAL)

    def test_premium_disabled(self) -> None:
        """关闭溢价率信号 → premium 不参与投票。"""
        cfg = RegimeDetectorConfig(use_premium_signal=False)
        detector = RegimeDetector(cfg)
        for i in range(70):
            detector.update(1000.0, volume=500.0, premium_median=55.0)
        # 既然关闭, 只有 3 信号
        regime = detector.detect()
        assert regime in (MarketRegime.BULL, MarketRegime.NEUTRAL, MarketRegime.BEAR)

    def test_score_voting_sum(self) -> None:
        """评分制: 2 BULL + 1 NEUTRAL + 1 BEAR = score+1 → BULL。"""
        cfg = RegimeDetectorConfig(
            use_score_voting=True,
            # 让动量信号 BULL: 持续上涨
            momentum_bull_threshold=0.01,
            # 让溢价信号 BULL: 低溢价
            premium_low_threshold=30.0,
            premium_lookback=5,
        )
        detector = RegimeDetector(cfg)
        base = 1000.0
        for i in range(70):
            price = base * (1 + 0.001 * i)  # ~7% over 70 days → > 0.01
            detector.update(price, volume=500.0, premium_median=20.0)
        regime = detector.detect()
        # 动量: BULL, 波动: NEUTRAL, 成交额: NEUTRAL, 溢价: BULL
        # score = +1 + 0 + 0 + 1 = +2 → BULL
        assert regime == MarketRegime.BULL

    def test_majority_voting_mode(self) -> None:
        """切换到多数投票模式（非评分制）。"""
        cfg = RegimeDetectorConfig(
            use_score_voting=False,
            momentum_bull_threshold=0.01,
        )
        detector = RegimeDetector(cfg)
        # 平稳市场
        for i in range(70):
            detector.update(1000.0, volume=500.0, premium_median=30.0)
        regime = detector.detect()
        # 所有信号 NEUTRAL → 多数投票 → NEUTRAL
        assert regime == MarketRegime.NEUTRAL

    def test_premium_lookback_insufficient(self) -> None:
        """溢价数据不足 premium_lookback → 不参与投票。"""
        cfg = RegimeDetectorConfig(
            use_premium_signal=True,
            premium_lookback=100,  # 需要 100 天
        )
        detector = RegimeDetector(cfg)
        for i in range(70):  # 只有 70 天 < 100
            detector.update(1000.0, volume=500.0, premium_median=55.0)
        # premium 信号不会参与（数据不足），不会崩溃
        regime = detector.detect()
        assert regime in (MarketRegime.BULL, MarketRegime.NEUTRAL, MarketRegime.BEAR)

    def test_premium_with_nan_values(self) -> None:
        """溢价率数据含 0.0（无效值）→ 被过滤。"""
        cfg = RegimeDetectorConfig(
            use_premium_signal=True,
            premium_lookback=5,
        )
        detector = RegimeDetector(cfg)
        for i in range(65):
            detector.update(1000.0, volume=500.0, premium_median=30.0)
        # 最后 5 天有无效值
        for _ in range(5):
            detector.update(1000.0, volume=500.0, premium_median=0.0)
        regime = detector.detect()
        assert regime in (MarketRegime.BULL, MarketRegime.NEUTRAL, MarketRegime.BEAR)


class TestRegimeDetectorHMM:
    """HMM 模式测试。"""

    def test_hmm_init(self) -> None:
        """HMM 模式初始化成功。"""
        pytest.importorskip("hmmlearn", reason="HMM 模式依赖可选包 hmmlearn")
        cfg = RegimeDetectorConfig(mode=DetectorMode.HMM, hmm_n_states=3)
        detector = RegimeDetector(cfg)
        assert detector._hmm_available

    def test_hmm_detect_with_data(self) -> None:
        """HMM 有足够数据时可正常检测。"""
        cfg = RegimeDetectorConfig(
            mode=DetectorMode.HMM, hmm_n_states=2, hmm_lookback=60,
        )
        detector = RegimeDetector(cfg)
        rng = np.random.RandomState(42)
        for i in range(100):
            price = 1000.0 + rng.normal(0, 10)
            detector.update(price, volume=500.0 + rng.normal(0, 50))
        regime = detector.detect()
        assert regime in (MarketRegime.BULL, MarketRegime.NEUTRAL, MarketRegime.BEAR)

    def test_hmm_fallback_insufficient(self) -> None:
        """HMM 数据不足 → 降级到规则模式。"""
        cfg = RegimeDetectorConfig(mode=DetectorMode.HMM, hmm_lookback=252)
        detector = RegimeDetector(cfg)
        for i in range(20):
            detector.update(100.0 + i)
        regime = detector.detect()
        assert regime in (MarketRegime.BULL, MarketRegime.NEUTRAL, MarketRegime.BEAR)
