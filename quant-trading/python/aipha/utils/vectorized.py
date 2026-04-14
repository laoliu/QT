"""向量化计算工具 — 高性能批量计算函数"""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def compute_returns(
    prices: NDArray[np.float64],
    method: str = "simple",
) -> NDArray[np.float64]:
    """收益率序列。method: simple / log"""
    if len(prices) < 2:
        return np.array([])
    if method == "log":
        return np.diff(np.log(prices))
    return np.diff(prices) / prices[:-1]


def compute_volatility(
    returns: NDArray[np.float64],
    annualize: bool = True,
    trading_days: int = 252,
) -> float:
    if len(returns) < 2:
        return 0.0
    vol = float(np.std(returns, ddof=1))
    return vol * np.sqrt(trading_days) if annualize else vol


def compute_moving_average(
    data: NDArray[np.float64],
    window: int,
) -> NDArray[np.float64]:
    """移动平均（前 window-1 个为 NaN）"""
    if len(data) < window:
        return np.full(len(data), np.nan)
    cumsum = np.cumsum(np.insert(data, 0, 0))
    ma = (cumsum[window:] - cumsum[:-window]) / window
    result = np.empty(len(data))
    result[:window - 1] = np.nan
    result[window - 1:] = ma
    return result


def compute_exponential_ma(
    data: NDArray[np.float64],
    span: int,
) -> NDArray[np.float64]:
    """指数移动平均 (EMA)"""
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
    """滚动标准差"""
    n = len(data)
    if n < window:
        return np.full(n, np.nan)
    result = np.empty(n)
    result[:window - 1] = np.nan
    # 使用滑动窗口高效计算
    for i in range(window - 1, n):
        result[i] = np.std(data[i - window + 1:i + 1], ddof=1)
    return result


def compute_momentum(
    prices: NDArray[np.float64],
    period: int,
) -> NDArray[np.float64]:
    """动量 = 收盘价变化率"""
    n = len(prices)
    if n <= period:
        return np.full(n, np.nan)
    result = np.empty(n)
    result[:period] = np.nan
    result[period:] = prices[period:] / prices[:-period] - 1.0
    return result


def compute_rsi(
    prices: NDArray[np.float64],
    period: int = 14,
) -> NDArray[np.float64]:
    """RSI 指标"""
    n = len(prices)
    if n < period + 1:
        return np.full(n, np.nan)
    delta = np.diff(prices)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    result = np.full(n, np.nan)
    avg_gain = np.mean(gain[:period])
    avg_loss = np.mean(loss[:period])
    if avg_loss > 0:
        rs = avg_gain / avg_loss
        result[period] = 100.0 - 100.0 / (1.0 + rs)
    else:
        result[period] = 100.0
    for i in range(period, len(delta)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        if avg_loss > 0:
            rs = avg_gain / avg_loss
            result[i + 1] = 100.0 - 100.0 / (1.0 + rs)
        else:
            result[i + 1] = 100.0
    return result


def compute_bollinger_bands(
    data: NDArray[np.float64],
    window: int = 20,
    num_std: float = 2.0,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64]]:
    """布林带。返回 (upper, middle, lower)"""
    middle = compute_moving_average(data, window)
    rolling_std = compute_rolling_std(data, window)
    upper = middle + num_std * rolling_std
    lower = middle - num_std * rolling_std
    return upper, middle, lower


def compute_drawdown_series(nav: NDArray[np.float64]) -> NDArray[np.float64]:
    """回撤序列"""
    cummax = np.maximum.accumulate(nav)
    return (cummax - nav) / np.where(cummax > 0, cummax, 1.0)


def compute_rolling_sharpe(
    returns: NDArray[np.float64],
    window: int = 63,
    annualize: float = 252.0,
) -> NDArray[np.float64]:
    """滚动夏普比率"""
    n = len(returns)
    result = np.full(n, np.nan)
    for i in range(window - 1, n):
        w = returns[i - window + 1:i + 1]
        std = float(np.std(w, ddof=1))
        if std > 1e-12:
            result[i] = float(np.mean(w)) / std * np.sqrt(annualize)
    return result
