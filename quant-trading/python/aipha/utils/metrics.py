"""统一绩效指标计算模块

所有 Sharpe / Sortino / MaxDrawdown / Calmar / 年化收益的唯一实现。

参考:
- Sharpe (1994): The Sharpe Ratio
- Sortino & Price (1994): Performance Measurement in a Downside Risk Framework
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from numpy.typing import NDArray

DEFAULT_RISK_FREE_RATE: float = 0.02
DEFAULT_TRADING_DAYS: int = 252


def compute_sharpe_ratio(
    returns: NDArray[np.float64] | Sequence[float],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> float:
    """年化夏普比率 SR = mean(r - rf) / std(r) * sqrt(T)"""
    arr = np.asarray(returns, dtype=np.float64)
    if len(arr) < 2:
        return 0.0
    daily_rf = risk_free_rate / trading_days
    excess = arr - daily_rf
    std_r = float(np.std(arr, ddof=1))
    if std_r < 1e-12:
        return 0.0
    return float(np.mean(excess)) * math.sqrt(trading_days) / std_r


def compute_sortino_ratio(
    returns: NDArray[np.float64] | Sequence[float],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> float:
    """年化 Sortino 比率（下行偏差标准定义）"""
    arr = np.asarray(returns, dtype=np.float64)
    if len(arr) < 2:
        return 0.0
    daily_rf = risk_free_rate / trading_days
    excess = arr - daily_rf
    downside = np.minimum(excess, 0.0)
    downside_dev = math.sqrt(float(np.mean(downside ** 2)))
    if downside_dev < 1e-12:
        return 0.0
    return float(np.mean(excess)) * math.sqrt(trading_days) / downside_dev


def compute_max_drawdown(
    nav: NDArray[np.float64] | Sequence[float],
) -> tuple[float, int, int]:
    """最大回撤。返回 (max_dd, peak_idx, trough_idx)"""
    arr = np.asarray(nav, dtype=np.float64)
    if len(arr) < 2:
        return 0.0, 0, 0
    cummax = np.maximum.accumulate(arr)
    drawdown = (cummax - arr) / np.where(cummax > 0, cummax, 1.0)
    trough = int(np.argmax(drawdown))
    peak = int(np.argmax(arr[:trough + 1])) if trough > 0 else 0
    return float(drawdown[trough]), peak, trough


def compute_max_drawdown_from_returns(
    returns: NDArray[np.float64] | Sequence[float],
) -> float:
    """从收益率序列计算最大回撤"""
    arr = np.asarray(returns, dtype=np.float64)
    if len(arr) < 1:
        return 0.0
    nav = np.cumprod(1 + arr)
    mdd, _, _ = compute_max_drawdown(nav)
    return mdd


def compute_annualized_return(
    nav: NDArray[np.float64] | Sequence[float],
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> float:
    """年化收益率"""
    arr = np.asarray(nav, dtype=np.float64)
    if len(arr) < 2 or arr[0] <= 0:
        return 0.0
    total_return = arr[-1] / arr[0]
    if total_return <= 0:
        return -1.0
    n_years = len(arr) / trading_days
    if n_years < 1e-6:
        return 0.0
    return total_return ** (1.0 / n_years) - 1.0


def compute_calmar_ratio(
    nav: NDArray[np.float64] | Sequence[float],
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> float:
    """Calmar 比率 = 年化收益 / 最大回撤"""
    ann_ret = compute_annualized_return(nav, trading_days)
    mdd, _, _ = compute_max_drawdown(nav)
    if mdd < 1e-12:
        return 0.0
    return ann_ret / mdd


def compute_win_rate(returns: NDArray[np.float64] | Sequence[float]) -> float:
    """胜率"""
    arr = np.asarray(returns, dtype=np.float64)
    if len(arr) == 0:
        return 0.0
    return float(np.mean(arr > 0))


def compute_profit_loss_ratio(returns: NDArray[np.float64] | Sequence[float]) -> float:
    """盈亏比 = 平均盈利 / 平均亏损"""
    arr = np.asarray(returns, dtype=np.float64)
    gains = arr[arr > 0]
    losses = arr[arr < 0]
    if len(gains) == 0 or len(losses) == 0:
        return 0.0
    return abs(float(np.mean(gains)) / float(np.mean(losses)))


def compute_information_ratio(
    returns: NDArray[np.float64] | Sequence[float],
    benchmark: NDArray[np.float64] | Sequence[float],
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> float:
    """信息比率 IR = mean(active) / std(active) * sqrt(T)"""
    ret = np.asarray(returns, dtype=np.float64)
    bench = np.asarray(benchmark, dtype=np.float64)
    if len(ret) < 2 or len(ret) != len(bench):
        return 0.0
    active = ret - bench
    std_a = float(np.std(active, ddof=1))
    if std_a < 1e-12:
        return 0.0
    return float(np.mean(active)) * math.sqrt(trading_days) / std_a


def compute_all_metrics(
    nav: NDArray[np.float64] | Sequence[float],
    trading_days: int = DEFAULT_TRADING_DAYS,
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
) -> dict[str, float]:
    """一次性计算全部绩效指标"""
    arr = np.asarray(nav, dtype=np.float64)
    if len(arr) < 2:
        return {}
    returns = np.diff(arr) / arr[:-1]
    mdd, _, _ = compute_max_drawdown(arr)
    ann_ret = compute_annualized_return(arr, trading_days)
    return {
        "annualized_return": round(ann_ret, 6),
        "sharpe_ratio": round(compute_sharpe_ratio(returns, risk_free_rate, trading_days), 4),
        "sortino_ratio": round(compute_sortino_ratio(returns, risk_free_rate, trading_days), 4),
        "max_drawdown": round(mdd, 6),
        "calmar_ratio": round(ann_ret / mdd if mdd > 1e-12 else 0.0, 4),
        "win_rate": round(compute_win_rate(returns), 4),
        "profit_loss_ratio": round(compute_profit_loss_ratio(returns), 4),
        "total_return": round(float(arr[-1] / arr[0] - 1), 6),
        "n_days": len(arr),
    }
