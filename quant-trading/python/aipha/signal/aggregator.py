"""
aggregator — 多策略信号聚合器
支持投票 / 加权 / 优先级 / Rank-Avg 四种模式
"""
from __future__ import annotations

import datetime as dt
from enum import Enum
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


class AggregationMethod(str, Enum):
    VOTING = "voting"
    WEIGHTED = "weighted"
    PRIORITY = "priority"
    RANK_AVG = "rank_avg"


class SignalAggregator:
    """
    将多个策略的打分 signal 合并为统一排名或买卖信号.

    用法:
        agg = SignalAggregator(method="weighted")
        agg.set_weights({"momentum": 0.3, "value": 0.5, "quality": 0.2})
        agg.add_signal("momentum", df_momentum)   # df: symbol, score
        agg.add_signal("value", df_value)
        result = agg.aggregate()                   # → DataFrame[symbol, agg_score]
    """

    def __init__(
        self,
        method: str | AggregationMethod = AggregationMethod.WEIGHTED,
        top_k: int = 30,
    ) -> None:
        self.method = AggregationMethod(method)
        self.top_k = top_k
        self._signals: Dict[str, pd.DataFrame] = {}
        self._weights: Dict[str, float] = {}
        self._priority: List[str] = []

    # ── 配置 ──────────────────────────────────────────────
    def set_weights(self, weights: Dict[str, float]) -> None:
        """设置策略权重 (只在 weighted 模式下使用)"""
        total = sum(weights.values())
        self._weights = {k: v / total for k, v in weights.items()} if total > 0 else weights

    def set_priority(self, order: List[str]) -> None:
        """设置策略优先级 (只在 priority 模式下使用). 先添加的优先."""
        self._priority = list(order)

    # ── 输入 ──────────────────────────────────────────────
    def add_signal(self, strategy: str, df: pd.DataFrame) -> None:
        """
        df 必须含 'symbol' 和 'score' 两列.
        score 越高表示越看好.
        """
        if "symbol" not in df.columns or "score" not in df.columns:
            raise ValueError(f"signal df for '{strategy}' must have 'symbol' and 'score' columns")
        self._signals[strategy] = df[["symbol", "score"]].copy()

    # ── 聚合 ──────────────────────────────────────────────
    def aggregate(self) -> pd.DataFrame:
        if not self._signals:
            return pd.DataFrame(columns=["symbol", "agg_score"])

        if self.method == AggregationMethod.VOTING:
            return self._aggregate_voting()
        elif self.method == AggregationMethod.WEIGHTED:
            return self._aggregate_weighted()
        elif self.method == AggregationMethod.PRIORITY:
            return self._aggregate_priority()
        elif self.method == AggregationMethod.RANK_AVG:
            return self._aggregate_rank_avg()
        else:
            raise ValueError(f"unknown method: {self.method}")

    # ── Voting ────────────────────────────────────────────
    def _aggregate_voting(self) -> pd.DataFrame:
        """
        每个策略取 top_k 标的投票, 票数高优先.
        同票数按平均 score 排序.
        """
        votes: Dict[str, float] = {}
        scores: Dict[str, List[float]] = {}
        for name, df in self._signals.items():
            topk = df.nlargest(self.top_k, "score")
            for _, row in topk.iterrows():
                sym = row["symbol"]
                votes[sym] = votes.get(sym, 0) + 1
                scores.setdefault(sym, []).append(row["score"])
        rows = [
            {"symbol": sym, "votes": v, "avg_score": np.mean(scores[sym])}
            for sym, v in votes.items()
        ]
        result = pd.DataFrame(rows)
        if result.empty:
            return pd.DataFrame(columns=["symbol", "agg_score"])
        result = result.sort_values(["votes", "avg_score"], ascending=False)
        result["agg_score"] = result["votes"] + result["avg_score"] / (result["avg_score"].max() + 1e-9)
        return result[["symbol", "agg_score"]].head(self.top_k).reset_index(drop=True)

    # ── Weighted ──────────────────────────────────────────
    def _aggregate_weighted(self) -> pd.DataFrame:
        """加权线性组合: agg_score = Σ w_i * score_i"""
        merged: Optional[pd.DataFrame] = None
        for name, df in self._signals.items():
            w = self._weights.get(name, 1.0 / len(self._signals))
            tmp = df.rename(columns={"score": f"s_{name}"}).copy()
            tmp[f"s_{name}"] *= w
            if merged is None:
                merged = tmp
            else:
                merged = merged.merge(tmp, on="symbol", how="outer")
        if merged is None or merged.empty:
            return pd.DataFrame(columns=["symbol", "agg_score"])
        score_cols = [c for c in merged.columns if c.startswith("s_")]
        merged[score_cols] = merged[score_cols].fillna(0.0)
        merged["agg_score"] = merged[score_cols].sum(axis=1)
        return (
            merged[["symbol", "agg_score"]]
            .sort_values("agg_score", ascending=False)
            .head(self.top_k)
            .reset_index(drop=True)
        )

    # ── Priority ──────────────────────────────────────────
    def _aggregate_priority(self) -> pd.DataFrame:
        """
        按优先级依次取标的, 高优先级策略 top 先入选,
        低优先级填满剩余名额.
        """
        selected: List[Tuple[str, float]] = []
        seen = set()
        order = self._priority or list(self._signals.keys())
        quota_per = max(1, self.top_k // len(order))
        for name in order:
            df = self._signals.get(name)
            if df is None:
                continue
            topk = df.nlargest(self.top_k, "score")
            count = 0
            for _, row in topk.iterrows():
                if row["symbol"] not in seen and count < quota_per:
                    selected.append((row["symbol"], row["score"]))
                    seen.add(row["symbol"])
                    count += 1
        # 如果还没满, 再从所有策略补
        if len(selected) < self.top_k:
            for name in order:
                df = self._signals.get(name)
                if df is None:
                    continue
                for _, row in df.nlargest(self.top_k, "score").iterrows():
                    if row["symbol"] not in seen:
                        selected.append((row["symbol"], row["score"]))
                        seen.add(row["symbol"])
                    if len(selected) >= self.top_k:
                        break
                if len(selected) >= self.top_k:
                    break
        result = pd.DataFrame(selected, columns=["symbol", "agg_score"])
        return result.head(self.top_k).reset_index(drop=True)

    # ── Rank Average ──────────────────────────────────────
    def _aggregate_rank_avg(self) -> pd.DataFrame:
        """
        每个策略内部做 percentile-rank, 然后取平均 rank.
        """
        merged: Optional[pd.DataFrame] = None
        for name, df in self._signals.items():
            tmp = df.copy()
            tmp[f"rank_{name}"] = tmp["score"].rank(pct=True, ascending=True)
            tmp = tmp[["symbol", f"rank_{name}"]]
            if merged is None:
                merged = tmp
            else:
                merged = merged.merge(tmp, on="symbol", how="outer")
        if merged is None or merged.empty:
            return pd.DataFrame(columns=["symbol", "agg_score"])
        rank_cols = [c for c in merged.columns if c.startswith("rank_")]
        merged[rank_cols] = merged[rank_cols].fillna(0.0)
        merged["agg_score"] = merged[rank_cols].mean(axis=1)
        return (
            merged[["symbol", "agg_score"]]
            .sort_values("agg_score", ascending=False)
            .head(self.top_k)
            .reset_index(drop=True)
        )

    # ── 清理 ──────────────────────────────────────────────
    def clear(self) -> None:
        self._signals.clear()
