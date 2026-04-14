"""
signal_filter — 信号后处理过滤器
在聚合完成后, 根据一系列规则过滤/调整最终入选标的
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Dict, List, Optional, Set

import pandas as pd


class SignalFilter:
    """
    链式过滤器:
        flt = SignalFilter()
        flt.exclude_symbols({"600000", "000001"})
        flt.max_sector_count(sector_map, n=5)
        flt.min_score(0.6)
        result = flt.apply(agg_df)
    """

    def __init__(self) -> None:
        self._filters: List[Callable[[pd.DataFrame], pd.DataFrame]] = []

    # ── 预设过滤规则 ──────────────────────────────────────
    def exclude_symbols(self, symbols: Set[str]) -> "SignalFilter":
        """排除特定标的 (ST / 停牌 / 黑名单)"""
        def _f(df: pd.DataFrame) -> pd.DataFrame:
            return df[~df["symbol"].isin(symbols)].reset_index(drop=True)
        self._filters.append(_f)
        return self

    def min_score(self, threshold: float) -> "SignalFilter":
        """聚合得分低于阈值的剔除"""
        def _f(df: pd.DataFrame) -> pd.DataFrame:
            return df[df["agg_score"] >= threshold].reset_index(drop=True)
        self._filters.append(_f)
        return self

    def max_sector_count(
        self,
        sector_map: Dict[str, str],
        n: int = 5,
    ) -> "SignalFilter":
        """
        每个行业最多选 n 只, 超出的按 agg_score 从低到高剔除.
        sector_map: symbol → sector_name
        """
        def _f(df: pd.DataFrame) -> pd.DataFrame:
            df = df.copy()
            df["_sector"] = df["symbol"].map(sector_map).fillna("unknown")
            df = df.sort_values("agg_score", ascending=False)
            df = df.groupby("_sector").head(n)
            return df.drop(columns=["_sector"]).sort_values("agg_score", ascending=False).reset_index(drop=True)
        self._filters.append(_f)
        return self

    def max_total(self, n: int) -> "SignalFilter":
        """最终持仓数上限"""
        def _f(df: pd.DataFrame) -> pd.DataFrame:
            return df.head(n).reset_index(drop=True)
        self._filters.append(_f)
        return self

    def custom(self, fn: Callable[[pd.DataFrame], pd.DataFrame]) -> "SignalFilter":
        """注册自定义过滤函数"""
        self._filters.append(fn)
        return self

    # ── 执行 ──────────────────────────────────────────────
    def apply(self, df: pd.DataFrame) -> pd.DataFrame:
        result = df.copy()
        for f in self._filters:
            result = f(result)
        return result
