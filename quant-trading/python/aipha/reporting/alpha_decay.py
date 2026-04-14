"""Alpha 衰减分析 & 市场环境分析"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional


def _simple_ols(x: list[float], y: list[float]) -> tuple[float, float, float]:
    """简单 OLS → (slope, intercept, r_squared)"""
    n = len(x)
    if n < 2:
        return 0.0, 0.0, 0.0
    mx = sum(x) / n
    my = sum(y) / n
    sxy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    sxx = sum((xi - mx) ** 2 for xi in x)
    syy = sum((yi - my) ** 2 for yi in y)
    slope = sxy / sxx if sxx > 1e-15 else 0.0
    intercept = my - slope * mx
    r2 = (sxy ** 2) / (sxx * syy) if sxx > 1e-15 and syy > 1e-15 else 0.0
    return slope, intercept, r2


@dataclass(frozen=True)
class AlphaDecayResult:
    slope: float
    r_squared: float
    avg_alpha: float
    n_windows: int
    rolling_alphas: list[float]
    is_decaying: bool

    def to_dict(self) -> dict:
        return {
            "slope": round(self.slope, 6),
            "r_squared": round(self.r_squared, 4),
            "avg_alpha": round(self.avg_alpha, 6),
            "n_windows": self.n_windows,
            "is_decaying": self.is_decaying,
        }


@dataclass(frozen=True)
class RegimeMetrics:
    regime: str
    trading_days: int
    total_return: float
    sharpe_ratio: float
    max_drawdown: float
    volatility: float

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "trading_days": self.trading_days,
            "total_return": round(self.total_return, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "max_drawdown": round(self.max_drawdown, 6),
            "volatility": round(self.volatility, 6),
        }


def analyze_alpha_decay(
    strategy_navs: dict[date, float],
    benchmark_navs: dict[date, float],
    window_days: int = 60,
    trading_days_per_year: int = 252,
) -> Optional[AlphaDecayResult]:
    """分析策略 alpha 随时间的衰减趋势

    Parameters
    ----------
    strategy_navs : {date -> nav}
    benchmark_navs : {date -> nav}
    window_days : 滚动窗口大小
    """
    dates = sorted(set(strategy_navs.keys()) & set(benchmark_navs.keys()))
    if len(dates) < window_days * 2:
        return None

    # 计算滚动超额收益
    rolling_alphas: list[float] = []
    for i in range(window_days, len(dates)):
        d_start = dates[i - window_days]
        d_end = dates[i]
        s_ret = strategy_navs[d_end] / strategy_navs[d_start] - 1
        b_ret = benchmark_navs[d_end] / benchmark_navs[d_start] - 1
        alpha = s_ret - b_ret
        ann_factor = trading_days_per_year / window_days
        rolling_alphas.append(alpha * ann_factor)

    if len(rolling_alphas) < 3:
        return None

    x = list(range(len(rolling_alphas)))
    slope, _intercept, r2 = _simple_ols(x, rolling_alphas)
    avg_alpha = sum(rolling_alphas) / len(rolling_alphas)

    return AlphaDecayResult(
        slope=slope, r_squared=r2, avg_alpha=avg_alpha,
        n_windows=len(rolling_alphas), rolling_alphas=rolling_alphas,
        is_decaying=slope < 0 and r2 > 0.1,
    )


def analyze_by_regime(
    strategy_navs: dict[date, float],
    regime_labels: dict[date, str],
    risk_free_rate: float = 0.0,
    trading_days_per_year: int = 252,
) -> list[RegimeMetrics]:
    """按市场环境分析策略表现"""
    dates = sorted(set(strategy_navs.keys()) & set(regime_labels.keys()))
    if len(dates) < 2:
        return []

    # 按 regime 分组
    groups: dict[str, list[tuple[date, float]]] = defaultdict(list)
    prev_nav = strategy_navs[dates[0]]
    for d in dates[1:]:
        nav = strategy_navs[d]
        regime = regime_labels.get(d, "unknown")
        ret = nav / prev_nav - 1 if prev_nav > 0 else 0.0
        groups[regime].append((d, ret))
        prev_nav = nav

    results: list[RegimeMetrics] = []
    for regime, rets_list in groups.items():
        rets = [r for _, r in rets_list]
        n = len(rets)
        if n < 1:
            continue
        total = math.prod(1 + r for r in rets) - 1
        mu = sum(rets) / n
        var = sum((r - mu) ** 2 for r in rets) / max(n - 1, 1)
        std = math.sqrt(var)
        ann_vol = std * math.sqrt(trading_days_per_year)
        ann_ret = mu * trading_days_per_year
        sharpe = (ann_ret - risk_free_rate) / ann_vol if ann_vol > 0 else 0.0

        # Max drawdown
        cum = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in rets:
            cum *= (1 + r)
            peak = max(peak, cum)
            dd = (peak - cum) / peak
            max_dd = max(max_dd, dd)

        results.append(RegimeMetrics(
            regime=regime, trading_days=n, total_return=total,
            sharpe_ratio=sharpe, max_drawdown=max_dd, volatility=ann_vol,
        ))
    return results
