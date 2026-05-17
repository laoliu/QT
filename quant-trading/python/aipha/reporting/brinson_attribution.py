"""
Brinson 收益归因模块

实现 Brinson-Hood-Beebower (BHB) 单期与多期收益归因，
将策略超额收益分解为：
- 资产配置效应 (Allocation)
- 个券选择效应 (Selection)
- 交互效应 (Interaction)

典型用法：
    result = brinson_single_period(
        portfolio_weights={"tech": 0.4, "finance": 0.3, "consumer": 0.3},
        benchmark_weights={"tech": 0.3, "finance": 0.4, "consumer": 0.3},
        portfolio_returns={"tech": 0.05, "finance": 0.02, "consumer": 0.03},
        benchmark_returns={"tech": 0.04, "finance": 0.01, "consumer": 0.02},
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BrinsonSingleResult:
    """Brinson 单期归因结果

    Attributes:
        allocation: 配置效应 (各行业/类别)
        selection: 选择效应
        interaction: 交互效应
        total_allocation: 总配置效应
        total_selection: 总选择效应
        total_interaction: 总交互效应
        total_active: 总超额收益 = allocation + selection + interaction
        portfolio_return: 组合收益
        benchmark_return: 基准收益
    """

    allocation: dict[str, float]
    selection: dict[str, float]
    interaction: dict[str, float]
    total_allocation: float
    total_selection: float
    total_interaction: float
    total_active: float
    portfolio_return: float
    benchmark_return: float

    def to_dict(self) -> dict:
        """将单期归因结果转换为字典。"""
        return {
            "allocation": {k: round(v, 8) for k, v in self.allocation.items()},
            "selection": {k: round(v, 8) for k, v in self.selection.items()},
            "interaction": {k: round(v, 8) for k, v in self.interaction.items()},
            "total_allocation": round(self.total_allocation, 8),
            "total_selection": round(self.total_selection, 8),
            "total_interaction": round(self.total_interaction, 8),
            "total_active": round(self.total_active, 8),
            "portfolio_return": round(self.portfolio_return, 8),
            "benchmark_return": round(self.benchmark_return, 8),
        }


@dataclass
class BrinsonMultiPeriodResult:
    """Brinson 多期归因结果

    Attributes:
        periods: 各期归因结果 {日期: BrinsonSingleResult}
        cumulative_allocation: 累计配置效应
        cumulative_selection: 累计选择效应
        cumulative_interaction: 累计交互效应
        cumulative_active: 累计超额收益
    """

    periods: dict[date, BrinsonSingleResult] = field(default_factory=dict)
    cumulative_allocation: float = 0.0
    cumulative_selection: float = 0.0
    cumulative_interaction: float = 0.0
    cumulative_active: float = 0.0

    def to_dict(self) -> dict:
        """将多期累计归因结果转换为字典。"""
        return {
            "num_periods": len(self.periods),
            "cumulative_allocation": round(self.cumulative_allocation, 8),
            "cumulative_selection": round(self.cumulative_selection, 8),
            "cumulative_interaction": round(self.cumulative_interaction, 8),
            "cumulative_active": round(self.cumulative_active, 8),
        }


def brinson_single_period(
    portfolio_weights: dict[str, float],
    benchmark_weights: dict[str, float],
    portfolio_returns: dict[str, float],
    benchmark_returns: dict[str, float],
) -> BrinsonSingleResult:
    """Brinson-Hood-Beebower 单期收益归因

    将组合超额收益分解为配置、选择、交互三大效应。

    公式（以行业 i 为例）：
        Allocation_i  = (w_p_i - w_b_i) * R_b_i
        Selection_i   = w_b_i * (R_p_i - R_b_i)
        Interaction_i = (w_p_i - w_b_i) * (R_p_i - R_b_i)

    超额收益 = Σ(Allocation_i + Selection_i + Interaction_i)

    Args:
        portfolio_weights: 组合中各行业/类别权重 {sector: weight}
        benchmark_weights: 基准中各行业/类别权重
        portfolio_returns: 组合中各行业/类别收益率
        benchmark_returns: 基准中各行业/类别收益率

    Returns:
        BrinsonSingleResult: 归因结果
    """
    # 合并所有行业/类别
    all_sectors = set(portfolio_weights) | set(benchmark_weights)

    allocation: dict[str, float] = {}
    selection: dict[str, float] = {}
    interaction: dict[str, float] = {}

    for sector in all_sectors:
        w_p = portfolio_weights.get(sector, 0.0)
        w_b = benchmark_weights.get(sector, 0.0)
        r_p = portfolio_returns.get(sector, 0.0)
        r_b = benchmark_returns.get(sector, 0.0)

        allocation[sector] = (w_p - w_b) * r_b
        selection[sector] = w_b * (r_p - r_b)
        interaction[sector] = (w_p - w_b) * (r_p - r_b)

    total_alloc = sum(allocation.values())
    total_sel = sum(selection.values())
    total_inter = sum(interaction.values())

    # 组合与基准总收益
    port_ret = sum(
        portfolio_weights.get(s, 0.0) * portfolio_returns.get(s, 0.0)
        for s in all_sectors
    )
    bench_ret = sum(
        benchmark_weights.get(s, 0.0) * benchmark_returns.get(s, 0.0)
        for s in all_sectors
    )

    return BrinsonSingleResult(
        allocation=allocation,
        selection=selection,
        interaction=interaction,
        total_allocation=total_alloc,
        total_selection=total_sel,
        total_interaction=total_inter,
        total_active=total_alloc + total_sel + total_inter,
        portfolio_return=port_ret,
        benchmark_return=bench_ret,
    )


def brinson_multi_period(
    period_data: dict[
        date,
        tuple[
            dict[str, float],  # portfolio_weights
            dict[str, float],  # benchmark_weights
            dict[str, float],  # portfolio_returns
            dict[str, float],  # benchmark_returns
        ],
    ],
) -> BrinsonMultiPeriodResult:
    """Brinson 多期归因（简单累加，适用于短周期）

    对每一期独立做 Brinson 归因，然后累计各效应。

    注意：简单累加在长周期下存在复利偏差，但对日频/周频分析
    已足够准确。如需更精确可扩展 Carino/Menchero 方法。

    Args:
        period_data: {日期: (组合权重, 基准权重, 组合收益, 基准收益)}

    Returns:
        BrinsonMultiPeriodResult
    """
    result = BrinsonMultiPeriodResult()

    for d in sorted(period_data):
        pw, bw, pr, br = period_data[d]
        single = brinson_single_period(pw, bw, pr, br)
        result.periods[d] = single
        result.cumulative_allocation += single.total_allocation
        result.cumulative_selection += single.total_selection
        result.cumulative_interaction += single.total_interaction
        result.cumulative_active += single.total_active

    return result
