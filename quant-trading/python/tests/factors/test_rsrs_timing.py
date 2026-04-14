"""RSRS 择时因子单元测试。

覆盖 RSRSTimingFactor / compute_rsrs_series / rsrs_to_position_scale。
"""

from __future__ import annotations

import numpy as np
import pytest

from aipha.factors.rsrs_timing import (
    DEFAULT_BEAR_THRESHOLD,
    DEFAULT_BULL_THRESHOLD,
    DEFAULT_REGRESSION_WINDOW,
    DEFAULT_ZSCORE_WINDOW,
    MarketSignal,
    RSRSConfig,
    RSRSResult,
    RSRSTimingFactor,
    compute_rsrs_series,
    rsrs_to_position_scale,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_trending_data(n: int, slope: float = 0.5, noise: float = 0.1):
    """生成带趋势的 high/low 序列。"""
    rng = np.random.default_rng(42)
    base = 10.0 + np.arange(n) * slope
    low = base + rng.normal(0, noise, n)
    high = low + 1.0 + rng.normal(0, noise, n)
    return high, low


# ============================================================================
# RSRSConfig
# ============================================================================

class TestRSRSConfig:
    """RSRSConfig 数据类测试。"""

    def test_default_values(self):
        cfg = RSRSConfig()
        assert cfg.regression_window == DEFAULT_REGRESSION_WINDOW
        assert cfg.zscore_window == DEFAULT_ZSCORE_WINDOW
        assert cfg.bull_threshold == DEFAULT_BULL_THRESHOLD
        assert cfg.bear_threshold == DEFAULT_BEAR_THRESHOLD
        assert cfg.use_r2_correction is True

    def test_custom_values(self):
        cfg = RSRSConfig(
            regression_window=10,
            zscore_window=200,
            bull_threshold=1.0,
            bear_threshold=-1.0,
            use_r2_correction=False,
        )
        assert cfg.regression_window == 10
        assert cfg.use_r2_correction is False


# ============================================================================
# compute_beta_r2 (static)
# ============================================================================

class TestComputeBetaR2:
    """OLS 回归 β/R² 测试。"""

    def test_perfect_linear(self):
        """完美线性: high = 2 * low + 1 → β=2, R²=1。"""
        low = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        high = 2.0 * low + 1.0
        beta, r2 = RSRSTimingFactor.compute_beta_r2(high, low)
        assert abs(beta - 2.0) < 1e-6
        assert abs(r2 - 1.0) < 1e-6

    def test_insufficient_data(self):
        """数据不足(<2) 返回 (0,0)。"""
        beta, r2 = RSRSTimingFactor.compute_beta_r2(
            np.array([1.0]), np.array([1.0]),
        )
        assert beta == 0.0
        assert r2 == 0.0

    def test_constant_x(self):
        """x 全相同 → ss_xx ≈ 0 → (0,0)。"""
        low = np.array([5.0, 5.0, 5.0, 5.0])
        high = np.array([6.0, 7.0, 8.0, 9.0])
        beta, r2 = RSRSTimingFactor.compute_beta_r2(high, low)
        assert beta == 0.0

    def test_noisy_data(self):
        """带噪声数据 β>0, 0<R²<1。"""
        rng = np.random.default_rng(123)
        low = np.arange(20, dtype=float)
        high = 1.5 * low + 3.0 + rng.normal(0, 1, 20)
        beta, r2 = RSRSTimingFactor.compute_beta_r2(high, low)
        assert 1.0 < beta < 2.0
        assert 0.5 < r2 < 1.0


# ============================================================================
# compute_zscore (static)
# ============================================================================

class TestComputeZscore:
    """Z-Score 计算测试。"""

    def test_insufficient_data(self):
        assert RSRSTimingFactor.compute_zscore(np.array([1.0]), 600) is None

    def test_constant_values(self):
        """全量相同 → std≈0 → 返回 0。"""
        arr = np.array([5.0] * 10)
        assert RSRSTimingFactor.compute_zscore(arr, 600) == 0.0

    def test_known_zscore(self):
        """手动验证 Z-Score。"""
        arr = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        z = RSRSTimingFactor.compute_zscore(arr, 600)
        assert z is not None
        mean = np.mean(arr)
        std = np.std(arr, ddof=1)
        expected = (5.0 - mean) / std
        assert abs(z - expected) < 1e-6


# ============================================================================
# RSRSTimingFactor (instance)
# ============================================================================

class TestRSRSTimingFactor:
    """核心计算器测试。"""

    def test_warmup_period(self):
        """窗口不满时返回 β=0, R²=0, signal=NEUTRAL。"""
        factor = RSRSTimingFactor(RSRSConfig(regression_window=5))
        for i in range(4):
            result = factor.update(10.0 + i, 9.0 + i)
            assert result.beta == 0.0
            assert result.signal == MarketSignal.NEUTRAL

    def test_first_valid_result(self):
        """第 N 天产生有效 β 和 R²。"""
        cfg = RSRSConfig(regression_window=5)
        factor = RSRSTimingFactor(cfg)
        high, low = _make_trending_data(10)
        for i in range(5):
            result = factor.update(high[i], low[i])
        # 第 5 天应有有效 beta
        assert result.beta != 0.0
        assert 0.0 <= result.r_squared <= 1.0

    def test_zscore_appears_after_enough_betas(self):
        """β 序列长度 ≥2 后 zscore 不为 None。"""
        cfg = RSRSConfig(regression_window=5, zscore_window=600)
        factor = RSRSTimingFactor(cfg)
        high, low = _make_trending_data(20)
        last_result = None
        for i in range(20):
            last_result = factor.update(high[i], low[i])
        # 到第 20 天，β 序列长度 = 20-5+1 = 16, zscore 应有值
        assert last_result is not None
        assert last_result.rsrs_zscore is not None

    def test_reset_clears_state(self):
        """reset 后所有内部状态清空。"""
        factor = RSRSTimingFactor()
        high, low = _make_trending_data(30)
        for i in range(30):
            factor.update(high[i], low[i])
        factor.reset()
        assert len(factor._highs) == 0
        assert len(factor._betas) == 0
        assert factor._latest_result is None
        assert factor.get_signal() == MarketSignal.NEUTRAL

    def test_get_signal_default(self):
        """未 update 时 get_signal 返回 NEUTRAL。"""
        factor = RSRSTimingFactor()
        assert factor.get_signal() == MarketSignal.NEUTRAL

    def test_r2_correction(self):
        """use_r2_correction=True 时 rsrs_std = zscore * R²。"""
        cfg = RSRSConfig(regression_window=5, zscore_window=50, use_r2_correction=True)
        factor = RSRSTimingFactor(cfg)
        high, low = _make_trending_data(80)
        result = None
        for i in range(80):
            result = factor.update(high[i], low[i])
        assert result is not None
        if result.rsrs_zscore is not None and result.rsrs_std is not None:
            expected = result.rsrs_zscore * result.r_squared
            assert abs(result.rsrs_std - expected) < 1e-10

    def test_no_r2_correction(self):
        """use_r2_correction=False 时 rsrs_std = zscore。"""
        cfg = RSRSConfig(regression_window=5, zscore_window=50, use_r2_correction=False)
        factor = RSRSTimingFactor(cfg)
        high, low = _make_trending_data(80)
        result = None
        for i in range(80):
            result = factor.update(high[i], low[i])
        assert result is not None
        if result.rsrs_zscore is not None and result.rsrs_std is not None:
            assert abs(result.rsrs_std - result.rsrs_zscore) < 1e-10


# ============================================================================
# compute_rsrs_series
# ============================================================================

class TestComputeRsrsSeries:
    """批量计算测试。"""

    def test_length_matches_input(self):
        high, low = _make_trending_data(50)
        results = compute_rsrs_series(high, low)
        assert len(results) == 50

    def test_warmup_results(self):
        """前 N-1 个结果 β=0。"""
        cfg = RSRSConfig(regression_window=5)
        high, low = _make_trending_data(20)
        results = compute_rsrs_series(high, low, cfg)
        for i in range(4):
            assert results[i].beta == 0.0

    def test_config_passthrough(self):
        """自定义 config 传递有效。"""
        cfg = RSRSConfig(regression_window=3, zscore_window=10)
        high, low = _make_trending_data(30)
        results = compute_rsrs_series(high, low, cfg)
        # 第 3 天开始有 β
        assert results[2].beta != 0.0


# ============================================================================
# rsrs_to_position_scale
# ============================================================================

class TestRsrsToPositionScale:
    """信号→仓位缩放测试。"""

    def test_bull(self):
        assert rsrs_to_position_scale(MarketSignal.BULL) == 1.0

    def test_bear(self):
        assert rsrs_to_position_scale(MarketSignal.BEAR) == 0.0

    def test_neutral(self):
        assert rsrs_to_position_scale(MarketSignal.NEUTRAL) == 0.5

    def test_custom_scales(self):
        val = rsrs_to_position_scale(
            MarketSignal.BULL,
            bull_scale=0.8,
            bear_scale=0.2,
            neutral_scale=0.5,
        )
        assert val == 0.8
