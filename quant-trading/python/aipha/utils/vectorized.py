"""
向量化计算工具模块

提供高性能的批量计算函数，用于：
- 批量计算技术指标
- 批量计算收益率和波动率
- 批量计算因子值

使用 NumPy 进行向量化运算，比循环快10-100倍。

使用示例：
    import numpy as np
    prices = np.array([100, 101, 102, 103, 104])
    returns = compute_returns(prices)
    ma = compute_moving_average(prices, window=3)
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


def compute_returns(
    prices: NDArray[np.float64],
    method: str = "simple",
) -> NDArray[np.float64]:
    """计算收益率序列

    Args:
        prices: 价格序列
        method: "simple" 简单收益率 或 "log" 对数收益率

    Returns:
        收益率序列（长度比 prices 少1）
    """
    if len(prices) < 2:
        return np.array([])

    if method == "log":
        return np.diff(np.log(prices))
    else:  # simple
        return np.diff(prices) / prices[:-1]


def compute_volatility(
    returns: NDArray[np.float64],
    annualize: bool = True,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """计算波动率

    Args:
        returns: 收益率序列
        annualize: 是否年化
        trading_days: 年交易日数

    Returns:
        波动率（标准差）
    """
    if len(returns) < 2:
        return 0.0

    vol = float(np.std(returns, ddof=1))
    if annualize:
        vol *= np.sqrt(trading_days)

    return vol


def compute_moving_average(
    data: NDArray[np.float64],
    window: int,
) -> NDArray[np.float64]:
    """计算移动平均

    Args:
        data: 数据序列
        window: 窗口大小

    Returns:
        移动平均序列（前 window-1 个为 NaN）
    """
    if len(data) < window:
        return np.full(len(data), np.nan)

    # 使用 cumsum 技巧实现高效移动平均
    cumsum = np.cumsum(np.insert(data, 0, 0))
    ma = (cumsum[window:] - cumsum[:-window]) / window

    # 前面填充 NaN
    result = np.empty(len(data))
    result[:window - 1] = np.nan
    result[window - 1:] = ma

    return result


def compute_exponential_ma(
    data: NDArray[np.float64],
    span: int,
) -> NDArray[np.float64]:
    """计算指数移动平均 (EMA)

    Args:
        data: 数据序列
        span: 衰减周期

    Returns:
        EMA 序列
    """
    if len(data) == 0:
        return np.array([])

    alpha = 2.0 / (span + 1)
    ema = np.empty(len(data))
    ema[0] = data[0]

    for i in range(1, len(data)):
        ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]

    return ema


def compute_rolling_std(
    data: NDArray[np.float64],
    window: int,
) -> NDArray[np.float64]:
    """计算滚动标准差

    Args:
        data: 数据序列
        window: 窗口大小

    Returns:
        滚动标准差序列
    """
    if len(data) < window:
        return np.full(len(data), np.nan)

    result = np.empty(len(data))
    result[:window - 1] = np.nan

    for i in range(window - 1, len(data)):
        result[i] = np.std(data[i - window + 1:i + 1], ddof=1)

    return result


def compute_momentum(
    prices: NDArray[np.float64],
    period: int,
) -> NDArray[np.float64]:
    """计算动量（价格变化率）

    Args:
        prices: 价格序列
        period: 回望期

    Returns:
        动量序列
    """
    if len(prices) <= period:
        return np.full(len(prices), np.nan)

    result = np.empty(len(prices))
    result[:period] = np.nan
    result[period:] = (prices[period:] - prices[:-period]) / prices[:-period]

    return result


def compute_rsi(
    prices: NDArray[np.float64],
    period: int = 14,
) -> NDArray[np.float64]:
    """计算相对强弱指数 (RSI)

    Args:
        prices: 价格序列
        period: 计算周期

    Returns:
        RSI 序列 (0-100)
    """
    if len(prices) < period + 1:
        return np.full(len(prices), np.nan)

    # 计算价格变化
    delta = np.diff(prices)

    # 分离涨跌
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    # 计算平均涨跌
    avg_gain = compute_exponential_ma(gains, period)
    avg_loss = compute_exponential_ma(losses, period)

    # 计算 RSI
    rsi = np.zeros_like(avg_gain)
    valid_loss = avg_loss > 0
    flat_mask = (~valid_loss) & (avg_gain <= 0)
    only_gain = (~valid_loss) & (avg_gain > 0)
    if np.any(valid_loss):
        rs = avg_gain[valid_loss] / avg_loss[valid_loss]
        rsi[valid_loss] = 100 - (100 / (1 + rs))
    rsi[flat_mask] = 50.0
    rsi[only_gain] = 100.0

    # 添加 NaN 前缀
    result = np.empty(len(prices))
    result[0] = np.nan
    result[1:] = rsi

    return result


def compute_macd(
    prices: NDArray[np.float64],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """计算 MACD 指标

    Args:
        prices: 价格序列
        fast_period: 快线周期
        slow_period: 慢线周期
        signal_period: 信号线周期

    Returns:
        (macd, signal, histogram) 三元组
    """
    ema_fast = compute_exponential_ma(prices, fast_period)
    ema_slow = compute_exponential_ma(prices, slow_period)

    macd = ema_fast - ema_slow
    signal = compute_exponential_ma(macd, signal_period)
    histogram = macd - signal

    return macd, signal, histogram


def compute_bollinger_bands(
    prices: NDArray[np.float64],
    window: int = 20,
    num_std: float = 2.0,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """计算布林带

    Args:
        prices: 价格序列
        window: 移动平均窗口
        num_std: 标准差倍数

    Returns:
        (upper, middle, lower) 三元组
    """
    middle = compute_moving_average(prices, window)
    rolling_std = compute_rolling_std(prices, window)

    upper = middle + num_std * rolling_std
    lower = middle - num_std * rolling_std

    return upper, middle, lower


def compute_sharpe_ratio(
    returns: NDArray[np.float64],
    risk_free_rate: float = 0.02,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """计算夏普比率 — §13.2 薄包装，委托 metrics.py 唯一实现

    Args:
        returns: 收益率序列
        risk_free_rate: 年化无风险利率
        trading_days: 年交易日数

    Returns:
        夏普比率
    """
    from aipha.utils.metrics import compute_sharpe_ratio as _sharpe
    return _sharpe(returns, risk_free_rate=risk_free_rate, trading_days=trading_days)


def compute_max_drawdown(
    nav: NDArray[np.float64],
) -> tuple[float, int, int]:
    """计算最大回撤 — §13.2 薄包装，委托 metrics.py 唯一实现

    Args:
        nav: 净值序列

    Returns:
        (max_drawdown, peak_idx, trough_idx)
    """
    from aipha.utils.metrics import compute_max_drawdown as _mdd
    return _mdd(nav)


def batch_compute_returns(
    price_matrix: NDArray[np.float64],
) -> NDArray[np.float64]:
    """批量计算多个标的的收益率

    Args:
        price_matrix: 价格矩阵 (days x symbols)

    Returns:
        收益率矩阵 (days-1 x symbols)
    """
    if price_matrix.shape[0] < 2:
        return np.array([])

    prev_prices = price_matrix[:-1, :]
    price_diff = np.diff(price_matrix, axis=0)
    returns = np.full_like(price_diff, np.nan, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        np.divide(price_diff, prev_prices, out=returns, where=prev_prices != 0)
    return returns


def batch_compute_volatility(
    return_matrix: NDArray[np.float64],
    annualize: bool = True,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> NDArray[np.float64]:
    """批量计算多个标的的波动率

    Args:
        return_matrix: 收益率矩阵 (days x symbols)
        annualize: 是否年化
        trading_days: 年交易日数

    Returns:
        波动率数组 (symbols,)
    """
    if return_matrix.shape[0] < 2:
        return np.zeros(return_matrix.shape[1])

    vol = np.std(return_matrix, axis=0, ddof=1)
    if annualize:
        vol *= np.sqrt(trading_days)

    return vol


def batch_compute_correlation(
    return_matrix: NDArray[np.float64],
) -> NDArray[np.float64]:
    """计算收益率相关系数矩阵

    Args:
        return_matrix: 收益率矩阵 (days x symbols)

    Returns:
        相关系数矩阵 (symbols x symbols)
    """
    if return_matrix.shape[0] < 2:
        n = return_matrix.shape[1]
        return np.eye(n)

    return np.corrcoef(return_matrix.T)


def batch_compute_covariance(
    return_matrix: NDArray[np.float64],
    annualize: bool = True,
    trading_days: int = TRADING_DAYS_PER_YEAR,
) -> NDArray[np.float64]:
    """计算收益率协方差矩阵

    Args:
        return_matrix: 收益率矩阵 (days x symbols)
        annualize: 是否年化
        trading_days: 年交易日数

    Returns:
        协方差矩阵 (symbols x symbols)
    """
    if return_matrix.shape[0] < 2:
        n = return_matrix.shape[1]
        return np.zeros((n, n))

    cov = np.cov(return_matrix.T, ddof=1)
    if annualize:
        cov *= trading_days

    return cov
