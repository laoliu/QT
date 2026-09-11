"""向量化计算工具模块测试"""

import numpy as np
import pytest

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
from aipha.utils.vectorized import (
    batch_compute_correlation,
    batch_compute_covariance,
    batch_compute_returns,
    batch_compute_volatility,
    compute_bollinger_bands,
    compute_exponential_ma,
    compute_macd,
    compute_max_drawdown,
    compute_momentum,
    compute_moving_average,
    compute_returns,
    compute_rolling_std,
    compute_rsi,
    compute_sharpe_ratio,
    compute_volatility,
)


class TestComputeReturns:
    """收益率计算测试"""

    def test_simple_returns(self):
        """测试简单收益率"""
        prices = np.array([100.0, 110.0, 99.0, 115.0])
        returns = compute_returns(prices)

        expected = np.array([0.1, -0.1, 115.0 / 99.0 - 1])
        np.testing.assert_array_almost_equal(returns, expected)

    def test_log_returns(self):
        """测试对数收益率"""
        prices = np.array([100.0, 110.0, 121.0])
        returns = compute_returns(prices, method="log")

        expected = np.array([np.log(1.1), np.log(1.1)])
        np.testing.assert_array_almost_equal(returns, expected)

    def test_empty_prices(self):
        """测试空价格序列"""
        prices = np.array([100.0])
        returns = compute_returns(prices)
        assert len(returns) == 0


class TestComputeVolatility:
    """波动率计算测试"""

    def test_annualized_volatility(self):
        """测试年化波动率"""
        returns = np.array([0.01, -0.02, 0.015, -0.005, 0.02])
        vol = compute_volatility(returns, annualize=True)

        daily_std = np.std(returns, ddof=1)
        # §13.8：年化因子只能来自 trading_calendar_constants，写死 252 会在
        # A 股口径（244）下产生 1.6% 的系统性偏差
        expected = daily_std * np.sqrt(TRADING_DAYS_PER_YEAR)
        assert abs(vol - expected) < 1e-10

    def test_non_annualized_volatility(self):
        """测试非年化波动率"""
        returns = np.array([0.01, -0.02, 0.015])
        vol = compute_volatility(returns, annualize=False)

        expected = np.std(returns, ddof=1)
        assert abs(vol - expected) < 1e-10

    def test_empty_returns(self):
        """测试空收益率序列"""
        returns = np.array([0.01])
        vol = compute_volatility(returns)
        assert vol == 0.0


class TestMovingAverage:
    """移动平均测试"""

    def test_moving_average_basic(self):
        """测试基本移动平均"""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        ma = compute_moving_average(data, window=3)

        assert np.isnan(ma[0])
        assert np.isnan(ma[1])
        assert ma[2] == 2.0  # (1+2+3)/3
        assert ma[3] == 3.0  # (2+3+4)/3
        assert ma[4] == 4.0  # (3+4+5)/3

    def test_window_larger_than_data(self):
        """测试窗口大于数据长度"""
        data = np.array([1.0, 2.0])
        ma = compute_moving_average(data, window=5)

        assert all(np.isnan(ma))


class TestExponentialMA:
    """指数移动平均测试"""

    def test_ema_basic(self):
        """测试基本 EMA"""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        ema = compute_exponential_ma(data, span=3)

        # EMA 应该跟随数据趋势
        assert ema[0] == 1.0
        assert ema[-1] > ema[0]

    def test_ema_empty(self):
        """测试空数据"""
        data = np.array([])
        ema = compute_exponential_ma(data, span=3)
        assert len(ema) == 0


class TestRollingStd:
    """滚动标准差测试"""

    def test_rolling_std_basic(self):
        """测试基本滚动标准差"""
        data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        std = compute_rolling_std(data, window=3)

        assert np.isnan(std[0])
        assert np.isnan(std[1])
        # [1,2,3] 的标准差
        expected_std = np.std([1, 2, 3], ddof=1)
        assert abs(std[2] - expected_std) < 1e-10


class TestMomentum:
    """动量计算测试"""

    def test_momentum_basic(self):
        """测试基本动量"""
        prices = np.array([100.0, 105.0, 110.0, 120.0, 125.0])
        mom = compute_momentum(prices, period=2)

        assert np.isnan(mom[0])
        assert np.isnan(mom[1])
        assert mom[2] == 0.1  # (110-100)/100
        assert mom[3] == (120 - 105) / 105


class TestRSI:
    """RSI 计算测试"""

    def test_rsi_range(self):
        """测试 RSI 在 0-100 范围内"""
        prices = np.array([44.0, 44.25, 44.5, 43.75, 44.5,
                          44.25, 44.0, 43.5, 44.0, 44.25,
                          44.25, 44.5, 45.0, 44.5, 45.25])
        rsi = compute_rsi(prices, period=14)

        # 跳过 NaN 值
        valid_rsi = rsi[~np.isnan(rsi)]
        assert all(0 <= r <= 100 for r in valid_rsi)


class TestMACD:
    """MACD 计算测试"""

    def test_macd_components(self):
        """测试 MACD 组件"""
        prices = np.linspace(100, 120, 50)  # 上涨趋势
        macd, signal, histogram = compute_macd(prices)

        assert len(macd) == len(prices)
        assert len(signal) == len(prices)
        assert len(histogram) == len(prices)
        np.testing.assert_array_almost_equal(histogram, macd - signal)


class TestBollingerBands:
    """布林带计算测试"""

    def test_bollinger_bands_order(self):
        """测试布林带上中下顺序"""
        prices = np.random.randn(50) * 5 + 100
        upper, middle, lower = compute_bollinger_bands(prices, window=20)

        # 跳过 NaN 值
        valid_idx = ~np.isnan(middle)
        assert all(upper[valid_idx] >= middle[valid_idx])
        assert all(middle[valid_idx] >= lower[valid_idx])


class TestSharpeRatio:
    """夏普比率测试"""

    def test_sharpe_positive(self):
        """测试正收益夏普比率"""
        # 稳定正收益（带微小波动以产生非零 std）
        rng = np.random.default_rng(42)
        returns = rng.normal(0.001, 0.005, 252)
        sharpe = compute_sharpe_ratio(returns)
        assert sharpe > 0

    def test_sharpe_zero_volatility(self):
        """测试零波动率"""
        returns = np.zeros(100)
        sharpe = compute_sharpe_ratio(returns)
        assert sharpe == 0.0


class TestMaxDrawdown:
    """最大回撤测试"""

    def test_max_drawdown_basic(self):
        """测试基本最大回撤"""
        nav = np.array([1.0, 1.1, 1.2, 1.0, 0.9, 1.1, 1.2])
        max_dd, peak_idx, trough_idx = compute_max_drawdown(nav)

        # 最大回撤从 1.2 跌到 0.9
        expected_dd = (1.2 - 0.9) / 1.2
        assert abs(max_dd - expected_dd) < 1e-10
        assert peak_idx == 2  # 1.2 的位置
        assert trough_idx == 4  # 0.9 的位置

    def test_no_drawdown(self):
        """测试无回撤情况"""
        nav = np.array([1.0, 1.1, 1.2, 1.3])
        max_dd, _, _ = compute_max_drawdown(nav)
        assert max_dd == 0.0


class TestBatchOperations:
    """批量计算测试"""

    def test_batch_returns(self):
        """测试批量收益率计算"""
        prices = np.array([
            [100.0, 200.0],
            [110.0, 190.0],
            [121.0, 209.0],
        ])
        returns = batch_compute_returns(prices)

        assert returns.shape == (2, 2)
        np.testing.assert_almost_equal(returns[0, 0], 0.1)
        np.testing.assert_almost_equal(returns[0, 1], -0.05)

    def test_batch_volatility(self):
        """测试批量波动率计算"""
        returns = np.array([
            [0.01, 0.02],
            [-0.02, -0.01],
            [0.015, 0.015],
            [-0.005, -0.005],
        ])
        vol = batch_compute_volatility(returns, annualize=False)

        assert vol.shape == (2,)
        assert vol[0] > 0
        assert vol[1] > 0

    def test_batch_correlation(self):
        """测试批量相关系数计算"""
        returns = np.array([
            [0.01, 0.02],
            [-0.02, -0.01],
            [0.015, 0.01],
        ])
        corr = batch_compute_correlation(returns)

        assert corr.shape == (2, 2)
        # 对角线为 1
        np.testing.assert_almost_equal(corr[0, 0], 1.0)
        np.testing.assert_almost_equal(corr[1, 1], 1.0)
        # 对称
        np.testing.assert_almost_equal(corr[0, 1], corr[1, 0])

    def test_batch_covariance(self):
        """测试批量协方差计算"""
        returns = np.array([
            [0.01, 0.02],
            [-0.02, -0.01],
            [0.015, 0.01],
        ])
        cov = batch_compute_covariance(returns, annualize=False)

        assert cov.shape == (2, 2)
        # 对角线为方差
        assert cov[0, 0] > 0
        assert cov[1, 1] > 0
        # 对称
        np.testing.assert_almost_equal(cov[0, 1], cov[1, 0])


class TestEdgeCases:
    """边界情况测试"""

    def test_single_element(self):
        """测试单元素数组"""
        data = np.array([100.0])

        assert len(compute_returns(data)) == 0
        assert compute_volatility(np.array([0.01])) == 0.0
        np.testing.assert_array_equal(compute_moving_average(data, 3), [np.nan])

    def test_large_array_performance(self):
        """测试大数组性能（确保不超时）"""
        prices = np.random.randn(10000) * 10 + 100
        prices = np.abs(prices)  # 确保正数

        # 这些操作应该在合理时间内完成
        _ = compute_returns(prices)
        _ = compute_moving_average(prices, 20)
        _ = compute_bollinger_bands(prices)
        _ = compute_macd(prices)
