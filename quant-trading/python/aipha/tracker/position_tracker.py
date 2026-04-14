"""
position_tracker — 逐日持仓快照追踪
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


@dataclass
class PositionSnapshot:
    date: dt.date
    positions: Dict[str, float]   # symbol → weight (0~1)
    nav: float = 0.0
    cash_weight: float = 0.0


class PositionTracker:
    """
    逐日记录投资组合持仓, 提供:
      - 换手率计算
      - 集中度 (HHI / top-N) 分析
      - 持仓热力图数据
    """

    def __init__(self) -> None:
        self._snapshots: List[PositionSnapshot] = []

    # ── 写入 ──────────────────────────────────────────────
    def snapshot(
        self,
        date: dt.date,
        positions: Dict[str, float],
        nav: float = 0.0,
        cash_weight: float = 0.0,
    ) -> None:
        self._snapshots.append(
            PositionSnapshot(
                date=date,
                positions=dict(positions),
                nav=nav,
                cash_weight=cash_weight,
            )
        )

    # ── 读取 ──────────────────────────────────────────────
    def to_weight_dataframe(self) -> pd.DataFrame:
        """返回 date × symbol 权重矩阵"""
        if not self._snapshots:
            return pd.DataFrame()
        rows = []
        for s in self._snapshots:
            row = {"date": s.date, **s.positions}
            rows.append(row)
        df = pd.DataFrame(rows).set_index("date").fillna(0.0)
        return df

    def nav_series(self) -> pd.Series:
        if not self._snapshots:
            return pd.Series(dtype=float)
        return pd.Series(
            {s.date: s.nav for s in self._snapshots}, name="nav"
        )

    # ── 分析 ──────────────────────────────────────────────
    def turnover(self) -> pd.Series:
        """
        逐日单边换手率 = Σ|w_t - w_{t-1}| / 2
        """
        wdf = self.to_weight_dataframe()
        if wdf.empty or len(wdf) < 2:
            return pd.Series(dtype=float, name="turnover")
        diff = wdf.diff().iloc[1:]
        return diff.abs().sum(axis=1) / 2

    def concentration_hhi(self) -> pd.Series:
        """HHI = Σ w_i^2"""
        wdf = self.to_weight_dataframe()
        if wdf.empty:
            return pd.Series(dtype=float, name="hhi")
        return (wdf ** 2).sum(axis=1).rename("hhi")

    def top_n_weight(self, n: int = 5) -> pd.Series:
        """前 N 大持仓合计权重"""
        wdf = self.to_weight_dataframe()
        if wdf.empty:
            return pd.Series(dtype=float, name=f"top{n}_weight")

        def _top(row):
            return np.sort(row.values)[-n:].sum() if len(row) >= n else row.sum()

        return wdf.apply(_top, axis=1).rename(f"top{n}_weight")

    def holding_count(self) -> pd.Series:
        """每日持有标的数"""
        wdf = self.to_weight_dataframe()
        if wdf.empty:
            return pd.Series(dtype=int, name="count")
        return (wdf > 0).sum(axis=1).rename("count")

    # ── 持久化 ────────────────────────────────────────────
    def save_csv(self, path: str) -> None:
        self.to_weight_dataframe().to_csv(path)

    def clear(self) -> None:
        self._snapshots.clear()
