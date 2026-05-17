"""Alpha 衰减分析与市场状态条件分析

- AlphaDecayAnalyzer: 通过滚动窗口计算超额收益趋势（OLS 线性回归斜率）
- RegimeConditionalAnalyzer: 按 MarketRegimeDetector 状态分组统计策略表现

对齐 copilot-instructions.md：
- 一切可替换都必须接口化
- 基准必须显式传入
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


@dataclass(frozen=True)
class AlphaDecayResult:
    """Alpha 衰减分析结果"""

    slope: float                    # OLS 回归斜率（年化超额收益随时间变化速率）
    r_squared: float                # 拟合优度
    avg_alpha: float                # 平均滚动 Alpha
    n_windows: int                  # 有效窗口数
    rolling_alphas: list[float]     # 滚动 Alpha 序列（年化）
    is_decaying: bool               # 斜率 < 0 且统计显著

    def to_dict(self) -> dict:
        """将 Alpha 衰减分析结果转换为字典。"""
        return {
            "slope": round(self.slope, 6),
            "r_squared": round(self.r_squared, 4),
            "avg_alpha": round(self.avg_alpha, 6),
            "n_windows": self.n_windows,
            "is_decaying": self.is_decaying,
        }


@dataclass(frozen=True)
class RegimeMetrics:
    """单一市场状态下的策略指标"""

    regime: str
    trading_days: int
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    volatility: float

    def to_dict(self) -> dict:
        """将单一市场状态指标转换为字典。"""
        return {
            "regime": self.regime,
            "trading_days": self.trading_days,
            "total_return": round(self.total_return, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 6),
            "volatility": round(self.volatility, 6),
        }


def analyze_alpha_decay(
    strategy_navs: list[tuple[date, float]],
    benchmark_navs: list[tuple[date, float]],
    window_days: int = 60,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> Optional[AlphaDecayResult]:
    """分析 Alpha 的时间衰减。

    通过滚动窗口计算超额收益（策略 - 基准），然后对
    滚动 Alpha 序列做线性回归判断趋势。

    Args:
        strategy_navs: 策略 NAV 序列 [(date, nav), ...]
        benchmark_navs: 基准 NAV 序列 [(date, nav), ...]
        window_days: 滚动窗口天数
        trading_days_per_year: 年化因子

    Returns:
        AlphaDecayResult 或 None（数据不足时）
    """
    if len(strategy_navs) < window_days + 1 or len(benchmark_navs) < window_days + 1:
        return None

    # 对齐日期
    strat_map = dict(strategy_navs)
    bench_map = dict(benchmark_navs)
    common_dates = sorted(set(strat_map.keys()) & set(bench_map.keys()))

    if len(common_dates) < window_days + 1:
        return None

    # 计算日超额收益
    excess_returns: list[float] = []
    dates_sorted = common_dates
    for i in range(1, len(dates_sorted)):
        d_prev = dates_sorted[i - 1]
        d_curr = dates_sorted[i]
        s_ret = (strat_map[d_curr] - strat_map[d_prev]) / strat_map[d_prev]
        b_ret = (bench_map[d_curr] - bench_map[d_prev]) / bench_map[d_prev]
        excess_returns.append(s_ret - b_ret)

    # 滚动窗口 Alpha
    rolling_alphas: list[float] = []
    for start in range(0, len(excess_returns) - window_days + 1, window_days):
        window = excess_returns[start:start + window_days]
        if len(window) < window_days:
            continue
        mean_excess = sum(window) / len(window)
        annual_alpha = mean_excess * trading_days_per_year
        rolling_alphas.append(annual_alpha)

    if len(rolling_alphas) < 2:
        return None

    # OLS: y = α + β·x
    n = len(rolling_alphas)
    x = list(range(n))
    slope, intercept, r_squared = _simple_ols(x, rolling_alphas)
    avg_alpha = sum(rolling_alphas) / n

    # 判断衰减：斜率 < 0 且 R² > 0.1
    is_decaying = slope < 0 and r_squared > 0.1

    return AlphaDecayResult(
        slope=slope,
        r_squared=r_squared,
        avg_alpha=avg_alpha,
        n_windows=n,
        rolling_alphas=rolling_alphas,
        is_decaying=is_decaying,
    )


def analyze_by_regime(
    strategy_navs: list[tuple[date, float]],
    regime_labels: list[tuple[date, str]],
    risk_free_rate: float = 0.0,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> list[RegimeMetrics]:
    """按市场状态分组计算策略指标。

    Args:
        strategy_navs: 策略 NAV 序列
        regime_labels: 每日市场状态 [(date, "BULL"/"BEAR"/"NEUTRAL"), ...]
        risk_free_rate: 年化无风险利率
        trading_days_per_year: 年化因子

    Returns:
        按状态分组的 RegimeMetrics 列表
    """
    if not strategy_navs or not regime_labels:
        return []

    nav_map = dict(strategy_navs)
    regime_map = dict(regime_labels)

    # 按时间排序的公共日期
    common = sorted(set(nav_map.keys()) & set(regime_map.keys()))
    if len(common) < 2:
        return []

    # 将连续同状态 period 聚合
    groups: dict[str, list[float]] = defaultdict(list)
    for i in range(1, len(common)):
        d_prev = common[i - 1]
        d_curr = common[i]
        regime = regime_map.get(d_curr, "NEUTRAL")
        ret = (nav_map[d_curr] - nav_map[d_prev]) / nav_map[d_prev]
        groups[regime].append(ret)

    results: list[RegimeMetrics] = []
    for regime, returns in sorted(groups.items()):
        n = len(returns)
        if n == 0:
            continue

        total_return = 1.0
        for r in returns:
            total_return *= (1 + r)
        total_return -= 1

        mean_ret = sum(returns) / n
        volatility = 0.0
        sharpe = 0.0
        if n >= 2:
            var = sum((r - mean_ret) ** 2 for r in returns) / (n - 1)
            daily_vol = math.sqrt(var)
            volatility = daily_vol * math.sqrt(trading_days_per_year)
            daily_rf = risk_free_rate / trading_days_per_year
            if daily_vol > 0:
                sharpe = (mean_ret - daily_rf) / daily_vol * math.sqrt(
                    trading_days_per_year
                )

        # 最大回撤
        cum = [1.0]
        for r in returns:
            cum.append(cum[-1] * (1 + r))
        peak = cum[0]
        max_dd = 0.0
        for v in cum:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        results.append(
            RegimeMetrics(
                regime=regime,
                trading_days=n,
                total_return=total_return,
                sharpe_ratio=sharpe,
                max_drawdown=max_dd,
                volatility=volatility,
            )
        )

    return results


def _simple_ols(
    x: list[float], y: list[float]
) -> tuple[float, float, float]:
    """简单线性回归 y = intercept + slope * x

    Returns: (slope, intercept, r_squared)
    """
    n = len(x)
    if n < 2:
        return 0.0, 0.0, 0.0

    sum_x = sum(x)
    sum_y = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_x2 = sum(xi ** 2 for xi in x)

    denom = n * sum_x2 - sum_x ** 2
    if abs(denom) < 1e-15:
        return 0.0, sum_y / n, 0.0

    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n

    # R²
    mean_y = sum_y / n
    ss_total = sum((yi - mean_y) ** 2 for yi in y)
    ss_residual = sum((yi - (intercept + slope * xi)) ** 2 for xi, yi in zip(x, y))

    r_squared = 1 - ss_residual / ss_total if ss_total > 0 else 0.0

    return slope, intercept, r_squared
