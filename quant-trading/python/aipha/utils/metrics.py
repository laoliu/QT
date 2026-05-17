"""§13.2 统一绩效指标计算模块

所有 Sharpe / Sortino / MaxDrawdown / Calmar / 年化收益计算的唯一权威实现。
其他模块必须 import 本模块的函数，禁止独立实现（§13.2 单一实现原则）。

参考文献:
- Sharpe (1994): The Sharpe Ratio
- Sortino & Price (1994): Performance Measurement in a Downside Risk Framework
- Bailey & López de Prado (2012): The Sharpe Ratio Efficient Frontier
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np
from numpy.typing import NDArray

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

# === 默认常量 ===
DEFAULT_RISK_FREE_RATE: float = 0.02  # 年化无风险利率 2%
DEFAULT_TRADING_DAYS: int = TRADING_DAYS_PER_YEAR  # §13.8: 244 (中国A股)


def compute_sharpe_ratio(
    returns: NDArray[np.float64] | Sequence[float],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> float:
    """计算年化夏普比率（算术收益，扣除无风险利率）

    公式: SR = mean(r - rf_daily) / std(r, ddof=1) * sqrt(T)

    Args:
        returns: 日收益率序列
        risk_free_rate: 年化无风险利率
        trading_days: 年交易日数
    Returns:
        年化 Sharpe Ratio
    """
    arr = np.asarray(returns, dtype=np.float64)
    if len(arr) < 2:
        return 0.0

    daily_rf = risk_free_rate / trading_days
    excess = arr - daily_rf
    mean_excess = float(np.mean(excess))
    std_r = float(np.std(arr, ddof=1))

    if std_r < 1e-12:
        return 0.0

    return mean_excess * math.sqrt(trading_days) / std_r


def compute_sharpe_ratio_log(
    returns: NDArray[np.float64] | Sequence[float],
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> float:
    """计算年化夏普比率（对数收益版本，用于置换检验等路径敏感场景）

    注意: 此版本不扣除无风险利率，因为置换检验中策略与基准共享同一 rf。
    仅在 advanced_validation / permutation_test 中使用。

    公式: SR_log = mean(log_r) / std(log_r, ddof=1) * sqrt(T)
    """
    arr = np.asarray(returns, dtype=np.float64)
    if len(arr) < 2 or not np.all(np.isfinite(arr)):
        return 0.0
    if np.any((1.0 + arr) <= 0.0):
        return 0.0
    # 构造 NAV → 对数收益
    nav = np.cumprod(np.concatenate(([1.0], 1 + arr)))
    if not np.all(np.isfinite(nav)) or np.any(nav <= 0.0):
        return 0.0
    with np.errstate(divide="ignore", invalid="ignore"):
        log_rets = np.diff(np.log(nav))
    if not np.all(np.isfinite(log_rets)):
        return 0.0
    mean_lr = float(np.mean(log_rets))
    std_lr = float(np.std(log_rets, ddof=1))
    if std_lr < 1e-15:
        return 0.0
    return (mean_lr / std_lr) * math.sqrt(trading_days)


def compute_sortino_ratio(
    returns: NDArray[np.float64] | Sequence[float],
    risk_free_rate: float = DEFAULT_RISK_FREE_RATE,
    trading_days: int = DEFAULT_TRADING_DAYS,
) -> float:
    """计算年化 Sortino 比率（Semi-Deviation 标准定义）

    采用 Sortino & Price (1994) 定义:
    分母 = sqrt(mean(min(r - rf_daily, 0)^2)) * sqrt(T)
    即对所有日期计算下行偏差（正收益日贡献 0），而非仅取负收益子集。

    Args:
        returns: 日收益率序列
        risk_free_rate: 年化无风险利率
        trading_days: 年交易日数
    Returns:
        年化 Sortino Ratio
    """
    arr = np.asarray(returns, dtype=np.float64)
    if len(arr) < 2:
        return 0.0

    daily_rf = risk_free_rate / trading_days
    excess = arr - daily_rf
    # Semi-Deviation: 正收益贡献 0, 负收益贡献其平方
    downside = np.minimum(excess, 0.0)
    downside_var = float(np.mean(downside ** 2))
    downside_dev = math.sqrt(downside_var)

    if downside_dev < 1e-12:
        return 0.0

    mean_excess = float(np.mean(excess))
    return (mean_excess / downside_dev) * math.sqrt(trading_days)


def compute_max_drawdown(
    nav: NDArray[np.float64] | Sequence[float],
) -> tuple[float, int, int]:
    """计算最大回撤

    Args:
        nav: 净值序列（绝对值，非收益率）
    Returns:
        (max_drawdown_ratio, peak_idx, trough_idx)
    """
    arr = np.asarray(nav, dtype=np.float64)
    if len(arr) < 2:
        return 0.0, 0, 0

    cummax = np.maximum.accumulate(arr)
    drawdown = (cummax - arr) / np.where(cummax > 0, cummax, 1.0)

    max_dd_idx = int(np.argmax(drawdown))
    max_dd = float(drawdown[max_dd_idx])
    peak_idx = int(np.argmax(arr[: max_dd_idx + 1]))

    return max_dd, peak_idx, max_dd_idx


def compute_max_drawdown_from_returns(
    returns: NDArray[np.float64] | Sequence[float],
) -> float:
    """从日收益率序列计算最大回撤比例

    Args:
        returns: 日收益率序列
    Returns:
        最大回撤比例 (0~1)
    """
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
    """计算年化收益率

    公式: AR = (nav[-1] / nav[0]) ^ (T / n_days) - 1

    Args:
        nav: 净值序列
        trading_days: 年交易日数
    Returns:
        年化收益率
    """
    arr = np.asarray(nav, dtype=np.float64)
    if len(arr) < 2 or arr[0] <= 0:
        return 0.0
    n_days = len(arr) - 1
    total_return = arr[-1] / arr[0]
    if total_return <= 0:
        return -1.0
    return float(total_return ** (trading_days / n_days) - 1)


def compute_calmar_ratio(
    annualized_return: float,
    max_drawdown: float,
) -> float:
    """计算 Calmar 比率

    Args:
        annualized_return: 年化收益率
        max_drawdown: 最大回撤比例 (0~1)
    Returns:
        Calmar Ratio
    """
    if max_drawdown < 1e-12:
        return float("inf") if annualized_return > 0 else 0.0
    return annualized_return / max_drawdown
