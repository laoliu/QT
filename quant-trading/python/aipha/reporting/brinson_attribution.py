"""Brinson 归因模型 — BHB / BF 版本

将组合超额收益分解为:
  - 资产配置效应 (Allocation)
  - 个股选择效应 (Selection)
  - 交互效应 (Interaction)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BrinsonResult:
    allocation: pd.Series       # 各期配置效应
    selection: pd.Series        # 各期选择效应
    interaction: pd.Series      # 各期交互效应
    total_excess: pd.Series     # 各期总超额
    by_sector: pd.DataFrame     # 分行业明细 (columns: alloc, select, interact)
    summary: dict               # 汇总


class BrinsonAttribution:
    """BHB (Brinson-Hood-Beebower) 基本归因。

    Parameters
    ----------
    portfolio_weights : DataFrame
        index=datetime, columns=sector, values=组合权重
    benchmark_weights : DataFrame
        同上，基准权重
    portfolio_returns : DataFrame
        各行业组合收益
    benchmark_returns : DataFrame
        各行业基准收益
    """

    def __init__(
        self,
        portfolio_weights: pd.DataFrame,
        benchmark_weights: pd.DataFrame,
        portfolio_returns: pd.DataFrame,
        benchmark_returns: pd.DataFrame,
    ) -> None:
        self.wp = portfolio_weights
        self.wb = benchmark_weights
        self.rp = portfolio_returns
        self.rb = benchmark_returns

    def run(self) -> BrinsonResult:
        """计算 BHB 归因。"""
        dw = self.wp - self.wb  # 权重差
        dr = self.rp - self.rb  # 收益差

        # 基准组合收益 (各期总基准收益)
        rb_total = (self.wb * self.rb).sum(axis=1)

        # 资产配置: (wp_i - wb_i) * rb_i
        alloc_by_sector = dw * self.rb
        # 个股选择: wb_i * (rp_i - rb_i)
        select_by_sector = self.wb * dr
        # 交互: (wp_i - wb_i) * (rp_i - rb_i)
        interact_by_sector = dw * dr

        alloc = alloc_by_sector.sum(axis=1)
        selection = select_by_sector.sum(axis=1)
        interaction = interact_by_sector.sum(axis=1)
        total_excess = alloc + selection + interaction

        by_sector = pd.DataFrame({
            "allocation": alloc_by_sector.sum(),
            "selection": select_by_sector.sum(),
            "interaction": interact_by_sector.sum(),
        })
        by_sector["total"] = by_sector.sum(axis=1)

        summary = {
            "total_excess_return": float(total_excess.sum()),
            "allocation_effect": float(alloc.sum()),
            "selection_effect": float(selection.sum()),
            "interaction_effect": float(interaction.sum()),
            "periods": len(alloc),
        }

        return BrinsonResult(
            allocation=alloc,
            selection=selection,
            interaction=interaction,
            total_excess=total_excess,
            by_sector=by_sector,
            summary=summary,
        )

    def run_bf(self) -> BrinsonResult:
        """BF (Brinson-Fachler) 变体: 将交互效应归入配置。"""
        result = self.run()
        merged_alloc = result.allocation + result.interaction
        result.allocation = merged_alloc
        result.interaction = pd.Series(0.0, index=result.interaction.index)
        result.summary["allocation_effect"] += result.summary["interaction_effect"]
        result.summary["interaction_effect"] = 0.0
        return result
