"""逐年业绩归因分析

将回测结果按自然年拆解，产出每年的：
- 年化收益率、最大回撤、Sharpe、波动率
- 费用归因（如有 cost_breakdown）
- 市场状态标注（牛/熊/震荡）

Args:
    nav_series: 日 NAV 序列 [(date, nav), ...]
    cost_series: 可选，日费用序列 [(date, cost_amount), ...]

对齐 copilot-instructions.md：
- 所有可替换都必须接口化
- 不在代码中硬编码市场、费率假设
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import numpy as np

from aipha.utils.metrics import (
    compute_sharpe_ratio as _compute_sharpe,
    compute_max_drawdown as _compute_mdd,
)
from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


@dataclass(frozen=True)
class YearlyMetrics:
    """单年度业绩指标"""

    year: int
    start_nav: float
    end_nav: float
    total_return: float       # 年内总收益率
    annual_return: float      # 年化收益率（与 total_return 相同，因为正好是 1 年）
    max_drawdown: float       # 年内最大回撤
    sharpe_ratio: float       # 年内 Sharpe
    volatility: float         # 年化波动率
    trading_days: int         # 该年交易日数
    regime: str               # "BULL" / "BEAR" / "NEUTRAL"

    # 费用归因（可选）
    total_cost: float = 0.0
    return_before_cost: Optional[float] = None

    def to_dict(self) -> dict:
        """序列化为字典。"""
        result = {
            "year": self.year,
            "start_nav": round(self.start_nav, 4),
            "end_nav": round(self.end_nav, 4),
            "total_return": round(self.total_return, 6),
            "annual_return": round(self.annual_return, 6),
            "max_drawdown": round(self.max_drawdown, 6),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "volatility": round(self.volatility, 6),
            "trading_days": self.trading_days,
            "regime": self.regime,
            "total_cost": round(self.total_cost, 4),
        }
        if self.return_before_cost is not None:
            result["return_before_cost"] = round(self.return_before_cost, 6)
        return result


def yearly_breakdown(
    nav_series: list[tuple[date, float]],
    cost_series: list[tuple[date, float]] | None = None,
    risk_free_rate: float = 0.02,
    bull_threshold: float = 0.10,
    bear_threshold: float = -0.10,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> list[YearlyMetrics]:
    """将 NAV 序列按自然年拆解，返回逐年指标。

    Args:
        nav_series: 日 NAV 序列 [(date, nav), ...]
        cost_series: 可选，日费用序列 [(date, cost), ...]
        risk_free_rate: 年化无风险利率
        bull_threshold: 年收益率 > 此值 → BULL
        bear_threshold: 年收益率 < 此值 → BEAR
        trading_days_per_year: 年交易日数（用于 Sharpe/波动率年化）

    Returns:
        按年份排序的 YearlyMetrics 列表
    """
    if not nav_series:
        return []

    # 按日期排序
    sorted_navs = sorted(nav_series, key=lambda x: x[0])

    # 按年分组
    year_groups: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for d, nav in sorted_navs:
        year_groups[d.year].append((d, nav))

    # 按年分组成本
    cost_by_year: dict[int, float] = defaultdict(float)
    if cost_series:
        for d, cost in cost_series:
            cost_by_year[d.year] += cost

    results: list[YearlyMetrics] = []
    for year in sorted(year_groups.keys()):
        navs = year_groups[year]
        nav_values = [n for _, n in navs]
        start_nav = nav_values[0]
        end_nav = nav_values[-1]

        total_return = (end_nav - start_nav) / start_nav if start_nav != 0 else 0.0

        # 日收益率
        daily_returns: list[float] = []
        for i in range(1, len(nav_values)):
            if nav_values[i - 1] != 0:
                daily_returns.append(
                    (nav_values[i] - nav_values[i - 1]) / nav_values[i - 1]
                )

        # 波动率 & Sharpe — §13.2 委托共享模块
        volatility = 0.0
        sharpe_ratio = 0.0
        if len(daily_returns) >= 2:
            rets_arr = np.array(daily_returns, dtype=np.float64)
            daily_vol = float(np.std(rets_arr, ddof=1))
            volatility = daily_vol * math.sqrt(trading_days_per_year)
            sharpe_ratio = _compute_sharpe(
                rets_arr,
                risk_free_rate=risk_free_rate,
                trading_days=trading_days_per_year,
            )

        # 最大回撤 — §13.2 委托共享模块
        navs_arr = np.array(nav_values, dtype=np.float64)
        max_drawdown, _, _ = _compute_mdd(navs_arr)

        # 市场状态标注
        if total_return > bull_threshold:
            regime = "BULL"
        elif total_return < bear_threshold:
            regime = "BEAR"
        else:
            regime = "NEUTRAL"

        total_cost = cost_by_year.get(year, 0.0)
        return_before_cost = None
        if total_cost > 0 and start_nav > 0:
            return_before_cost = total_return + total_cost / start_nav

        results.append(
            YearlyMetrics(
                year=year,
                start_nav=start_nav,
                end_nav=end_nav,
                total_return=total_return,
                annual_return=total_return,  # 年度 = 年化
                max_drawdown=max_drawdown,
                sharpe_ratio=sharpe_ratio,
                volatility=volatility,
                trading_days=len(navs),
                regime=regime,
                total_cost=total_cost,
                return_before_cost=return_before_cost,
            )
        )

    return results


def _calc_max_drawdown(nav_values: list[float]) -> float:
    """计算 NAV 序列的最大回撤"""
    if len(nav_values) < 2:
        return 0.0
    peak = nav_values[0]
    max_dd = 0.0
    for nav in nav_values:
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def format_yearly_report(
    yearly: list[YearlyMetrics],
    strategy_name: str = "",
) -> str:
    """将逐年指标格式化为文本报告"""
    lines = []
    if strategy_name:
        lines.append(f"=== {strategy_name} 逐年业绩归因 ===")
    lines.append(
        f"{'年份':>6}  {'收益率':>8}  {'最大回撤':>8}  {'Sharpe':>7}  "
        f"{'波动率':>8}  {'交易日':>5}  {'状态':>6}"
    )
    lines.append("-" * 65)

    for y in yearly:
        lines.append(
            f"{y.year:>6}  {y.total_return:>8.2%}  {y.max_drawdown:>8.2%}  "
            f"{y.sharpe_ratio:>7.3f}  {y.volatility:>8.2%}  "
            f"{y.trading_days:>5}  {y.regime:>6}"
        )

    return "\n".join(lines)
