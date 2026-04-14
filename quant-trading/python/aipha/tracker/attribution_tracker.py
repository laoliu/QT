"""
attribution_tracker — 逐日收益归因时序记录
支持两种模式:
  1. 基于 Brinson 的资产配置 / 选股归因
  2. 基于因子的 risk-factor 归因
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class AttributionRecord:
    date: dt.date
    total_return: float = 0.0
    benchmark_return: float = 0.0
    active_return: float = 0.0
    # Brinson
    allocation_effect: float = 0.0
    selection_effect: float = 0.0
    interaction_effect: float = 0.0
    # Factor
    factor_returns: Dict[str, float] = field(default_factory=dict)
    residual_return: float = 0.0


class AttributionTracker:
    """
    逐日归因追踪, 用于:
      - 累计归因分解
      - 归因稳定性评估
      - 因子暴露漂移检测
    """

    def __init__(self) -> None:
        self._records: List[AttributionRecord] = []

    # ── 写入 ──────────────────────────────────────────────
    def add(self, record: AttributionRecord) -> None:
        self._records.append(record)

    def add_brinson(
        self,
        date: dt.date,
        total_return: float,
        benchmark_return: float,
        allocation: float,
        selection: float,
        interaction: float = 0.0,
    ) -> None:
        self._records.append(
            AttributionRecord(
                date=date,
                total_return=total_return,
                benchmark_return=benchmark_return,
                active_return=total_return - benchmark_return,
                allocation_effect=allocation,
                selection_effect=selection,
                interaction_effect=interaction,
            )
        )

    def add_factor(
        self,
        date: dt.date,
        total_return: float,
        factor_returns: Dict[str, float],
        residual: float = 0.0,
    ) -> None:
        self._records.append(
            AttributionRecord(
                date=date,
                total_return=total_return,
                factor_returns=dict(factor_returns),
                residual_return=residual,
            )
        )

    # ── 读取 ──────────────────────────────────────────────
    def brinson_dataframe(self) -> pd.DataFrame:
        if not self._records:
            return pd.DataFrame()
        rows = [
            {
                "date": r.date,
                "total_return": r.total_return,
                "benchmark_return": r.benchmark_return,
                "active_return": r.active_return,
                "allocation": r.allocation_effect,
                "selection": r.selection_effect,
                "interaction": r.interaction_effect,
            }
            for r in self._records
        ]
        return pd.DataFrame(rows).set_index("date")

    def factor_dataframe(self) -> pd.DataFrame:
        if not self._records:
            return pd.DataFrame()
        rows = []
        for r in self._records:
            row = {"date": r.date, "total_return": r.total_return, "residual": r.residual_return}
            row.update(r.factor_returns)
            rows.append(row)
        return pd.DataFrame(rows).set_index("date").fillna(0.0)

    # ── 累计分析 ──────────────────────────────────────────
    def cumulative_brinson(self) -> pd.DataFrame:
        df = self.brinson_dataframe()
        if df.empty:
            return df
        return df[["allocation", "selection", "interaction", "active_return"]].cumsum()

    def cumulative_factor(self) -> pd.DataFrame:
        df = self.factor_dataframe()
        if df.empty:
            return df
        return df.cumsum()

    # ── 持久化 ────────────────────────────────────────────
    def save_csv(self, path: str, mode: str = "brinson") -> None:
        if mode == "brinson":
            self.brinson_dataframe().to_csv(path)
        else:
            self.factor_dataframe().to_csv(path)

    def clear(self) -> None:
        self._records.clear()
