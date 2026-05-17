from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


DEFAULT_WEIGHTS_V1: dict[str, float] = {
    "price": 0.25,
    "premium": 0.20,
    "roe": 0.20,
    "ep": 0.15,
    "vol": 0.10,
    "issue_size": 0.10,
    "ytm": 0.0,
    "bond_floor": 0.0,
}


@dataclass(slots=True)
class CBQualityFilters:
    max_price: float = 150.0
    max_premium: float = 80.0
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30


@dataclass(slots=True)
class ScoreDispersionInfo:
    """评分分散度指标 — 衡量当日信号区分度/alpha 质量。

    核心思想：score_spread 大 → top_k 与次优标的差距明显 → 信号强；
    score_spread 小 → 排名边界模糊 → 集中持仓风险高。
    """

    score_spread: float
    """score[rank_2k] - score[rank_k]：组合内外边界得分差（越大信号越强）。"""

    score_cv: float
    """top_k 得分的变异系数 std/mean（越大内部分化越强）。"""

    candidate_density: float
    """通过筛选的候选数 / top_k — 供需比（越高可选标的越充裕）。"""

    n_candidates: int
    """通过筛选的候选总数。"""

    score_at_k: float
    """第 k 名（组合内最差）的得分。"""

    score_at_2k: float
    """第 2k 名（组合外第一名）的得分，不足时取最后一名。"""

    top_k_scores: list[float] = field(default_factory=list)
    """top_k 得分列表（从优到劣排序）。"""


@dataclass(slots=True)
class RankResult:
    """rank_cb_quality_extended 的返回值。"""

    symbols: list[str]
    dispersion: ScoreDispersionInfo | None


_WEIGHT_ALIASES: dict[str, str] = {
    "stock_roe": "roe",
    "stock_ep": "ep",
    "stock_vol": "vol",
}


def _merge_weights(weights: dict[str, float] | None) -> dict[str, float]:
    merged = dict(DEFAULT_WEIGHTS_V1)
    if not weights:
        return merged
    for key, value in weights.items():
        merged[_WEIGHT_ALIASES.get(key, key)] = value
    return merged


def _rank_series(values: pd.Series, ascending: bool) -> pd.Series:
    return values.fillna(0.0).rank(method="average", pct=True, ascending=ascending)


def rank_cb_quality(
    df: pd.DataFrame,
    weights: dict[str, float] | None,
    top_k: int,
    filters: CBQualityFilters,
    score_multipliers: dict[str, float] | None = None,
) -> list[str]:
    """Rank convertible bonds using prepared factor columns.

    Expected columns:
    - ts_code, close, cb_over_rate
    - optional: roe, ep, vol, issue_size, amount, maturity_date

    Args:
        score_multipliers: {ts_code: weight} 乘法权重，用于规模软约束等。
            权重 < 1.0 会惩罚该标的的综合得分（不是消除）。
    """
    ranked = _compute_ranked_df(df, weights, top_k, filters, score_multipliers)
    if ranked is None:
        return []
    return ranked.head(top_k)["ts_code"].tolist()


def rank_cb_quality_extended(
    df: pd.DataFrame,
    weights: dict[str, float] | None,
    top_k: int,
    filters: CBQualityFilters,
    score_multipliers: dict[str, float] | None = None,
) -> RankResult:
    """与 rank_cb_quality 相同的排名逻辑，额外返回 ScoreDispersionInfo。"""
    ranked = _compute_ranked_df(df, weights, top_k, filters, score_multipliers)
    if ranked is None:
        return RankResult(symbols=[], dispersion=None)
    symbols = ranked.head(top_k)["ts_code"].tolist()
    dispersion = _compute_dispersion(ranked, top_k)
    return RankResult(symbols=symbols, dispersion=dispersion)


def _compute_ranked_df(
    df: pd.DataFrame,
    weights: dict[str, float] | None,
    top_k: int,
    filters: CBQualityFilters,
    score_multipliers: dict[str, float] | None = None,
) -> pd.DataFrame | None:
    """核心排名逻辑：过滤 + 评分 + 排序，返回完整排序后的 DataFrame。"""
    if df.empty or top_k <= 0:
        return None

    prepared = df.copy()
    prepared = prepared.dropna(subset=["ts_code", "close", "cb_over_rate"])
    prepared = prepared[prepared["close"] <= filters.max_price]
    prepared = prepared[prepared["cb_over_rate"] <= filters.max_premium]
    if "amount" in prepared.columns:
        prepared = prepared[prepared["amount"].fillna(0.0) >= filters.min_turnover]

    if prepared.empty:
        return None

    for column in ("roe", "ep", "vol", "issue_size", "ytm", "bond_floor"):
        if column not in prepared.columns:
            prepared[column] = 0.0

    merged_weights = _merge_weights(weights)
    total_weight = sum(max(0.0, merged_weights.get(key, 0.0)) for key in DEFAULT_WEIGHTS_V1)
    if total_weight <= 0:
        total_weight = 1.0

    prepared["r_price"] = _rank_series(prepared["close"], ascending=True)
    prepared["r_premium"] = _rank_series(prepared["cb_over_rate"], ascending=True)
    prepared["r_roe"] = _rank_series(prepared["roe"], ascending=False)
    prepared["r_ep"] = _rank_series(prepared["ep"], ascending=False)
    prepared["r_vol"] = _rank_series(prepared["vol"], ascending=False)
    prepared["r_issue_size"] = _rank_series(prepared["issue_size"], ascending=True)
    prepared["r_ytm"] = _rank_series(prepared["ytm"], ascending=False)
    prepared["r_bond_floor"] = _rank_series(prepared["bond_floor"], ascending=False)

    prepared["score"] = (
        prepared["r_price"] * (merged_weights["price"] / total_weight)
        + prepared["r_premium"] * (merged_weights["premium"] / total_weight)
        + prepared["r_roe"] * (merged_weights["roe"] / total_weight)
        + prepared["r_ep"] * (merged_weights["ep"] / total_weight)
        + prepared["r_vol"] * (merged_weights["vol"] / total_weight)
        + prepared["r_issue_size"] * (merged_weights["issue_size"] / total_weight)
        + prepared["r_ytm"] * (merged_weights["ytm"] / total_weight)
        + prepared["r_bond_floor"] * (merged_weights["bond_floor"] / total_weight)
    )

    # 规模软约束：对大盘标的施加加性惩罚（lower score = better）
    if score_multipliers:
        penalty = prepared["ts_code"].map(
            lambda s: 1.0 - score_multipliers.get(s, 1.0)
        )
        prepared["score"] = prepared["score"] + penalty

    ranked = prepared.sort_values(["score", "ts_code"], ascending=[True, True])
    return ranked


__all__ = [
    "CBQualityFilters",
    "DEFAULT_WEIGHTS_V1",
    "RankResult",
    "ScoreDispersionInfo",
    "rank_cb_quality",
    "rank_cb_quality_extended",
]


def _compute_dispersion(ranked_df: pd.DataFrame, top_k: int) -> ScoreDispersionInfo:
    """从完整排序后的 DataFrame 计算评分分散度指标。"""
    scores = ranked_df["score"].values
    n = len(scores)

    # top_k scores (已按升序排列，lower = better)
    k = min(top_k, n)
    top_scores = scores[:k].tolist()

    score_at_k = float(scores[k - 1]) if k > 0 else 0.0

    # 第 2k 名得分（不足时取最后一名）
    idx_2k = min(2 * top_k - 1, n - 1)
    score_at_2k = float(scores[idx_2k])

    # spread: 组合外第一名与组合内最差名的差距
    score_spread = score_at_2k - score_at_k

    # CV of top_k scores
    if k >= 2:
        arr = np.array(top_scores)
        mean_s = arr.mean()
        std_s = arr.std(ddof=1)
        score_cv = float(std_s / mean_s) if mean_s > 1e-9 else 0.0
    else:
        score_cv = 0.0

    candidate_density = float(n / top_k) if top_k > 0 else 0.0

    return ScoreDispersionInfo(
        score_spread=score_spread,
        score_cv=score_cv,
        candidate_density=candidate_density,
        n_candidates=n,
        score_at_k=score_at_k,
        score_at_2k=score_at_2k,
        top_k_scores=top_scores,
    )