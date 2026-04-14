"""VaR / CVaR 风险度量"""
from __future__ import annotations

import math
from typing import Optional


def historical_var(returns: list[float], confidence: float = 0.95) -> float:
    """历史模拟法 VaR。

    Args:
        returns: 收益率序列
        confidence: 置信度 (如 0.95)

    Returns:
        VaR（正数表示损失）
    """
    if not returns:
        return 0.0
    sorted_returns = sorted(returns)
    idx = int(len(sorted_returns) * (1 - confidence))
    idx = max(0, min(idx, len(sorted_returns) - 1))
    return -sorted_returns[idx]


def historical_cvar(returns: list[float], confidence: float = 0.95) -> float:
    """历史模拟法 CVaR (Expected Shortfall)。"""
    if not returns:
        return 0.0
    sorted_returns = sorted(returns)
    cutoff = int(len(sorted_returns) * (1 - confidence))
    cutoff = max(1, cutoff)
    tail = sorted_returns[:cutoff]
    return -sum(tail) / len(tail)


def parametric_var(
    mean: float,
    std: float,
    confidence: float = 0.95,
    holding_period: int = 1,
) -> float:
    """参数法 VaR（假设正态分布）。"""
    # 正态分布分位数近似
    z_map = {0.90: 1.282, 0.95: 1.645, 0.99: 2.326}
    z = z_map.get(confidence, 1.645)
    return -(mean * holding_period - z * std * math.sqrt(holding_period))


def max_drawdown(nav_series: list[float]) -> tuple[float, int, int]:
    """计算最大回撤。

    Returns:
        (max_dd_pct, peak_idx, trough_idx)
    """
    if not nav_series or len(nav_series) < 2:
        return 0.0, 0, 0

    peak = nav_series[0]
    peak_idx = 0
    max_dd = 0.0
    max_dd_peak = 0
    max_dd_trough = 0

    for i, nav in enumerate(nav_series):
        if nav > peak:
            peak = nav
            peak_idx = i
        dd = (peak - nav) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
            max_dd_peak = peak_idx
            max_dd_trough = i

    return max_dd, max_dd_peak, max_dd_trough
