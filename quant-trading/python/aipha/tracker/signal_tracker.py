"""
signal_tracker — 记录策略每期产生的买卖信号及后续表现
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pandas as pd


@dataclass
class SignalRecord:
    """一条信号记录"""
    date: dt.date
    symbol: str
    direction: str          # "long" / "short" / "close"
    strategy: str
    score: float = 0.0
    meta: Dict = field(default_factory=dict)


class SignalTracker:
    """
    信号追踪器 —— 收集每期策略产生的 signal, 落地为 DataFrame
    后续可用于:
      - 信号衰减分析 (IC decay)
      - 信号重叠度 (crowding) 检测
      - 策略间信号相关性
    """

    def __init__(self) -> None:
        self._records: List[SignalRecord] = []

    # ── 写入 ──────────────────────────────────────────────
    def add(
        self,
        date: dt.date,
        symbol: str,
        direction: str,
        strategy: str,
        score: float = 0.0,
        **meta,
    ) -> None:
        self._records.append(
            SignalRecord(
                date=date,
                symbol=symbol,
                direction=direction,
                strategy=strategy,
                score=score,
                meta=meta,
            )
        )

    def add_batch(self, records: List[Dict]) -> None:
        """批量写入, 每条 dict 至少含 date/symbol/direction/strategy"""
        for r in records:
            self.add(**r)

    # ── 读取 ──────────────────────────────────────────────
    def to_dataframe(self) -> pd.DataFrame:
        if not self._records:
            return pd.DataFrame(
                columns=["date", "symbol", "direction", "strategy", "score"]
            )
        rows = [
            {
                "date": r.date,
                "symbol": r.symbol,
                "direction": r.direction,
                "strategy": r.strategy,
                "score": r.score,
                **r.meta,
            }
            for r in self._records
        ]
        return pd.DataFrame(rows)

    def get_by_strategy(self, strategy: str) -> pd.DataFrame:
        df = self.to_dataframe()
        if df.empty:
            return df
        return df[df["strategy"] == strategy].reset_index(drop=True)

    def get_by_date(
        self,
        start: Optional[dt.date] = None,
        end: Optional[dt.date] = None,
    ) -> pd.DataFrame:
        df = self.to_dataframe()
        if df.empty:
            return df
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        return df.reset_index(drop=True)

    # ── 分析 ──────────────────────────────────────────────
    def overlap_matrix(self) -> pd.DataFrame:
        """
        计算策略间信号重叠度 (Jaccard similarity).
        返回 strategy × strategy 矩阵.
        """
        df = self.to_dataframe()
        if df.empty:
            return pd.DataFrame()
        groups = df.groupby("strategy").apply(
            lambda g: set(zip(g["date"], g["symbol"]))
        )
        strategies = groups.index.tolist()
        n = len(strategies)
        mat = pd.DataFrame(1.0, index=strategies, columns=strategies)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = groups.iloc[i], groups.iloc[j]
                union = len(a | b)
                jaccard = len(a & b) / union if union else 0.0
                mat.iloc[i, j] = jaccard
                mat.iloc[j, i] = jaccard
        return mat

    def signal_count_summary(self) -> pd.DataFrame:
        """按 strategy × direction 统计信号数"""
        df = self.to_dataframe()
        if df.empty:
            return pd.DataFrame()
        return df.groupby(["strategy", "direction"]).size().unstack(fill_value=0)

    # ── 持久化 ────────────────────────────────────────────
    def save_csv(self, path: str) -> None:
        self.to_dataframe().to_csv(path, index=False)

    def clear(self) -> None:
        self._records.clear()
