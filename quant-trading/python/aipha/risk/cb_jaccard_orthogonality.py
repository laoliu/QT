"""CB 子策略持仓级 Jaccard 正交性分析

从回测产出的 daily_snapshots.csv 提取每日持仓集合，
计算 CB 子策略两两之间的持仓 Jaccard 时序，
验证原则 E [CN] 的 Jaccard ≤ 0.30 硬约束。

依赖: pandas, 回测产出 (daily_snapshots.csv)
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class JaccardPairResult:
    """两策略之间的 Jaccard 分析结果。"""
    strategy_a: str
    strategy_b: str
    mean_jaccard: float
    median_jaccard: float
    max_jaccard: float
    pct_above_threshold: float  # 超过阈值的交易日占比
    total_days: int
    passed: bool  # mean < threshold


@dataclass
class CBJaccardReport:
    """CB 子策略组正交性报告。"""
    pair_results: list[JaccardPairResult] = field(default_factory=list)
    overall_passed: bool = True
    threshold: float = 0.30
    avg_jaccard: float = 0.0
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """序列化为字典。"""
        return {
            "threshold": self.threshold,
            "overall_passed": self.overall_passed,
            "avg_jaccard": round(self.avg_jaccard, 4),
            "pair_results": [
                {
                    "pair": f"{p.strategy_a} vs {p.strategy_b}",
                    "mean_jaccard": round(p.mean_jaccard, 4),
                    "median_jaccard": round(p.median_jaccard, 4),
                    "max_jaccard": round(p.max_jaccard, 4),
                    "pct_above_threshold": round(p.pct_above_threshold, 4),
                    "total_days": p.total_days,
                    "passed": p.passed,
                }
                for p in self.pair_results
            ],
            "recommendations": self.recommendations,
        }


# ---------------------------------------------------------------------------
# 持仓提取
# ---------------------------------------------------------------------------

def extract_daily_holdings(
    snapshot_path: Path,
) -> dict[str, set[str]]:
    """从 daily_snapshots.csv 提取每日持仓集合。

    Returns: {date_str: {symbol1, symbol2, ...}}
    """
    df = pd.read_csv(snapshot_path)
    holdings: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        date_str = str(row["date"])
        pos_raw = row.get("positions", "{}")
        if pd.isna(pos_raw) or pos_raw in ("", "{}"):
            holdings[date_str] = set()
            continue
        try:
            pos_dict = ast.literal_eval(str(pos_raw))
            holdings[date_str] = set(pos_dict.keys())
        except (ValueError, SyntaxError):
            holdings[date_str] = set()
    return holdings


def _jaccard(set_a: set[str], set_b: set[str]) -> float:
    """计算两集合 Jaccard 系数。"""
    if not set_a and not set_b:
        return 0.0
    union = set_a | set_b
    if not union:
        return 0.0
    return len(set_a & set_b) / len(union)


def compute_pairwise_jaccard(
    holdings_a: dict[str, set[str]],
    holdings_b: dict[str, set[str]],
    threshold: float = 0.30,
    name_a: str = "A",
    name_b: str = "B",
) -> JaccardPairResult:
    """计算两策略每日 Jaccard 时序并汇总统计。"""
    common_dates = sorted(set(holdings_a) & set(holdings_b))
    if not common_dates:
        return JaccardPairResult(
            strategy_a=name_a, strategy_b=name_b,
            mean_jaccard=0.0, median_jaccard=0.0, max_jaccard=0.0,
            pct_above_threshold=0.0, total_days=0, passed=True,
        )

    jaccards: list[float] = []
    for d in common_dates:
        a_set = holdings_a[d]
        b_set = holdings_b[d]
        # 跳过两者都为空的日期（初始期）
        if not a_set and not b_set:
            continue
        jaccards.append(_jaccard(a_set, b_set))

    if not jaccards:
        return JaccardPairResult(
            strategy_a=name_a, strategy_b=name_b,
            mean_jaccard=0.0, median_jaccard=0.0, max_jaccard=0.0,
            pct_above_threshold=0.0, total_days=0, passed=True,
        )

    s = pd.Series(jaccards)
    above = (s > threshold).sum() / len(s)
    return JaccardPairResult(
        strategy_a=name_a,
        strategy_b=name_b,
        mean_jaccard=float(s.mean()),
        median_jaccard=float(s.median()),
        max_jaccard=float(s.max()),
        pct_above_threshold=float(above),
        total_days=len(jaccards),
        passed=float(s.mean()) <= threshold,
    )


def analyze_cb_orthogonality(
    strategy_snapshots: dict[str, Path],
    threshold: float = 0.30,
) -> CBJaccardReport:
    """CB 子策略组正交性全面分析。

    Args:
        strategy_snapshots: {策略名: daily_snapshots.csv 路径}
        threshold: Jaccard 阈值 (原则 E [CN] 默认 0.30)

    Returns:
        CBJaccardReport
    """
    # 1. 提取各策略每日持仓
    all_holdings: dict[str, dict[str, set[str]]] = {}
    for name, path in strategy_snapshots.items():
        if not path.exists():
            logger.warning(f"Snapshot not found: {path}")
            continue
        all_holdings[name] = extract_daily_holdings(path)

    names = sorted(all_holdings.keys())
    if len(names) < 2:
        return CBJaccardReport(
            threshold=threshold,
            recommendations=["需要至少 2 个策略的回测产出"],
        )

    # 2. 两两 Jaccard
    pairs: list[JaccardPairResult] = []
    for i, na in enumerate(names):
        for nb in names[i + 1 :]:
            pr = compute_pairwise_jaccard(
                all_holdings[na], all_holdings[nb],
                threshold=threshold, name_a=na, name_b=nb,
            )
            pairs.append(pr)

    # 3. 汇总
    all_means = [p.mean_jaccard for p in pairs]
    avg = sum(all_means) / len(all_means) if all_means else 0.0
    overall = all(p.passed for p in pairs)

    # 4. 建议
    recs: list[str] = []
    for p in pairs:
        if not p.passed:
            recs.append(
                f"{p.strategy_a} vs {p.strategy_b}: "
                f"mean Jaccard={p.mean_jaccard:.3f} > {threshold} — "
                f"建议在次优策略中剔除重叠标的或降权"
            )
    if overall:
        recs.append("所有策略对 Jaccard 均 ≤ 阈值，正交性达标")

    return CBJaccardReport(
        pair_results=pairs,
        overall_passed=overall,
        threshold=threshold,
        avg_jaccard=avg,
        recommendations=recs,
    )
