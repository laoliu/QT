"""可转债增强因子工具模块 — 供多个 CB 策略共享的因子计算函数"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aipha.strategy.cb_rotation_base import CandidateInfo

_RATING_SCORES: dict[str, float] = {
    "AAA": 1.00, "AA+": 0.90, "AA": 0.80, "AA-": 0.70,
    "A+": 0.60, "A": 0.50, "A-": 0.40,
    "BBB+": 0.30, "BBB": 0.25, "BBB-": 0.20,
    "BB+": 0.15, "BB": 0.12, "BB-": 0.10,
    "B+": 0.08, "B": 0.05, "B-": 0.03,
    "CCC": 0.02, "CC": 0.01, "C": 0.00,
}


def rating_score(rating: str) -> float:
    return _RATING_SCORES.get(rating.strip().upper(), 0.5) if rating else 0.5


def delta_exposure(candidate_or_prem=None, close=None, stock_close=None) -> float:
    """简易 delta 暴露: 基于转换价值/面值.
    
    Accepts either:
      - delta_exposure(candidate: CandidateInfo)
      - delta_exposure(premium_rate, close, stock_close)
    """
    if close is not None:
        # Called with (prem, close, stock_close)
        prem = candidate_or_prem or 0.0
        sc = stock_close or close
        cb_value = sc * (1 + prem / 100.0) if prem else close
        return max(0.0, min(1.0, (cb_value - 85.0) / 100.0))
    candidate = candidate_or_prem
    cb_value = getattr(candidate, "cb_value", None)
    if cb_value is None or cb_value <= 0:
        return 0.5
    return max(0.0, min(1.0, (cb_value - 85.0) / 100.0))


def enhanced_ytm(close: float, maturity_date_or_years=None, now: datetime | None = None) -> float:
    """增强到期收益率 (简化).
    
    Accepts either:
      - enhanced_ytm(close, maturity_date_str, now)
      - enhanced_ytm(close, remain_years_float)
    """
    if close <= 0:
        return 0.0
    try:
        if isinstance(maturity_date_or_years, (int, float)):
            years = float(maturity_date_or_years)
        elif isinstance(maturity_date_or_years, str):
            s = maturity_date_or_years.strip()
            if "-" in s:
                mat = datetime.strptime(s[:10], "%Y-%m-%d")
            else:
                mat = datetime.strptime(s[:8], "%Y%m%d")
            if now is None:
                now = datetime.now()
            years = (mat - now).days / 365.25
        else:
            return 0.0
        if years <= 0:
            return 0.0
        return (100.0 / close) ** (1.0 / years) - 1.0
    except (ValueError, TypeError):
        return 0.0


def value_size_composite(
    candidate: "CandidateInfo",
    price_weight: float = 0.35,
    premium_weight: float = 0.30,
    size_weight: float = 0.20,
    delta_weight: float = 0.15,
    *,
    pool_stats: dict[str, tuple[float, float]] | None = None,
) -> float:
    """价值 + 规模复合因子"""
    score = 0.0
    # 价格因子 (越低越好)
    price = candidate.price
    score += price_weight * max(0, 1.0 - price / 200.0)
    # 溢价率因子 (越低越好)
    premium = getattr(candidate, "premium_rate", None) or 0.0
    score += premium_weight * max(0, 1.0 - premium)
    # 规模因子 (越小越好)
    issue_size = getattr(candidate, "issue_size", None) or 10000.0
    score += size_weight * max(0, 1.0 - issue_size / 50000.0)
    # delta 因子
    d = delta_exposure(candidate)
    score += delta_weight * (1.0 - abs(d - 0.5) * 2)  # balanced 区间最优
    return score


def compute_pool_stats(
    candidates: list["CandidateInfo"],
    percentile_low: float = 5.0,
    percentile_high: float = 95.0,
) -> dict[str, tuple[float, float]]:
    """计算候选池统计 — 返回各指标 (low, high) 百分位"""
    stats: dict[str, tuple[float, float]] = {}
    if not candidates:
        return stats
    prices = sorted(c.price for c in candidates)
    n = len(prices)
    lo_idx = max(0, int(n * percentile_low / 100))
    hi_idx = min(n - 1, int(n * percentile_high / 100))
    stats["price"] = (prices[lo_idx], prices[hi_idx])
    premiums = sorted(getattr(c, "premium_rate", 0.0) or 0.0 for c in candidates)
    stats["premium"] = (premiums[lo_idx], premiums[hi_idx])
    return stats


def turnover_activity(candidate: "CandidateInfo") -> float:
    """换手率活跃度因子 [0, 1]"""
    amount = getattr(candidate, "amount", None) or 0.0
    issue_size = getattr(candidate, "issue_size", None) or 1.0
    turnover = amount / issue_size if issue_size > 0 else 0.0
    # 归一化到 [0, 1] 范围
    return min(1.0, turnover / 0.5)


def rank_fusion(
    candidates: list["CandidateInfo"],
    factor_values: dict[str, dict[str, float]],
    factor_weights: dict[str, float],
    top_k: int = 20,
) -> list[str]:
    """多因子排序融合 — 各因子排名加权合成"""
    if not candidates or not factor_values:
        return []
    codes = [c.code for c in candidates]
    total_weight = sum(factor_weights.values()) or 1.0
    composite: dict[str, float] = {code: 0.0 for code in codes}

    for factor_name, weights in factor_weights.items():
        fv = factor_values.get(factor_name, {})
        if not fv:
            continue
        vals = [(code, fv.get(code, 0.0)) for code in codes]
        vals.sort(key=lambda x: x[1], reverse=True)
        for rank, (code, _) in enumerate(vals):
            composite[code] += weights * (1.0 - rank / max(len(vals), 1)) / total_weight

    sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
    return sorted_codes[:top_k]


def compute_factor_values(
    candidates: list["CandidateInfo"],
    now: datetime,
    premium_tracker: Any | None = None,
) -> dict[str, dict[str, float]]:
    """批量计算各因子值"""
    result: dict[str, dict[str, float]] = {}

    # 价格因子
    price_f: dict[str, float] = {}
    for c in candidates:
        price_f[c.code] = 1.0 - c.price / 200.0
    result["price"] = price_f

    # 溢价率因子
    premium_f: dict[str, float] = {}
    for c in candidates:
        p = getattr(c, "premium_rate", None) or 0.0
        premium_f[c.code] = 1.0 - p
    result["premium"] = premium_f

    # delta 因子
    delta_f: dict[str, float] = {}
    for c in candidates:
        delta_f[c.code] = delta_exposure(c)
    result["delta"] = delta_f

    # 评级因子
    rating_f: dict[str, float] = {}
    for c in candidates:
        r = getattr(c, "rating", None) or ""
        rating_f[c.code] = rating_score(r)
    result["rating"] = rating_f

    # YTM 因子
    ytm_f: dict[str, float] = {}
    for c in candidates:
        mat = getattr(c, "maturity_date", None)
        close = c.price
        ytm_f[c.code] = enhanced_ytm(close, mat, now) if mat else 0.0
    result["ytm"] = ytm_f

    # 活跃度因子
    activity_f: dict[str, float] = {}
    for c in candidates:
        activity_f[c.code] = turnover_activity(c)
    result["activity"] = activity_f

    return result


@dataclass
class FactorWeight:
    name: str
    weight: float
    ascending: bool = True

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError(f"Factor weight must be >= 0, got {self.weight}")


class PremiumMomentumTracker:
    """溢价率动量跟踪器"""

    def __init__(self, lookback: int = 5) -> None:
        self._lookback = lookback
        self._history: dict[str, list[float]] = {}

    def update(self, symbol: str, cb_over_rate: float | None) -> None:
        if cb_over_rate is None:
            return
        hist = self._history.setdefault(symbol, [])
        hist.append(cb_over_rate)
        if len(hist) > self._lookback + 5:
            self._history[symbol] = hist[-self._lookback - 5:]

    def momentum(self, symbol: str) -> float | None:
        hist = self._history.get(symbol)
        if hist is None or len(hist) < self._lookback:
            return None
        old = hist[-self._lookback]
        return hist[-1] - old
