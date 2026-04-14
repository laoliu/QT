"""可转债极致小规模低价策略 (cb_ultra) — 规模 + 溢价率 + 价格 + 年龄权重"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)

if TYPE_CHECKING:
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


def _rank_scores(values: list[float], ascending: bool = True) -> list[float]:
    """将 values 排名归一化到 [0, 1]"""
    n = len(values)
    if n == 0:
        return []
    indexed = sorted(enumerate(values), key=lambda x: x[1], reverse=not ascending)
    ranks = [0.0] * n
    for rank, (idx, _) in enumerate(indexed):
        ranks[idx] = rank / max(n - 1, 1)
    return ranks


# ── CBUltra 策略 (含年龄因子) ──────────────────────────

@dataclass
class CBUltraConfig(CBRotationConfig):
    top_k: int = 5
    rebalance_days: int = 1
    max_price: float = 150.0
    max_issue_size: float = 20000.0
    min_issue_size: float = 2000.0
    min_turnover: float = 10.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.50
    lot_size: int = 10
    use_age_filter: bool = True
    min_age_days: int = 3
    max_age_days: int = 90
    fallback_max_age_days: int = 365
    max_premium: float = 80.0
    weight_size: float = 0.40
    weight_premium: float = 0.20
    weight_price: float = 0.20
    weight_age: float = 0.20
    prefer_low_premium: bool = True
    min_candidates: int = 3


class CBUltraStrategy(CBRotationStrategy):
    """极致小规模 — 低规模 + 低溢价率 + 低价格 + 年龄因子"""
    name = "cb_ultra"

    def __init__(self, config: CBUltraConfig | None = None, **kwargs: object):
        super().__init__(config=config or CBUltraConfig(), **kwargs)

    @property
    def config(self) -> CBUltraConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        pass

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        issue_size = getattr(candidate, "issue_size", None) or float("inf")
        if issue_size < cfg.min_issue_size or issue_size > cfg.max_issue_size:
            return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        turnover = getattr(candidate, "turnover", None) or 0.0
        if turnover < cfg.min_turnover:
            return False
        if cfg.use_age_filter:
            listed = getattr(candidate, "list_date", None)
            if listed:
                age = days_since_listed(listed)
                if age < cfg.min_age_days:
                    return False
                if age > cfg.max_age_days:
                    return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        if len(candidates) < cfg.min_candidates:
            # 放宽年龄限制后重试
            pass
        codes = [c.code for c in candidates]
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        prices = [c.price for c in candidates]
        ages = []
        for c in candidates:
            listed = getattr(c, "list_date", None)
            ages.append(float(days_since_listed(listed)) if listed else 100.0)

        r_size = _rank_scores(sizes, ascending=True)   # 小规模高分
        r_prem = _rank_scores(premiums, ascending=True) # 低溢价高分
        r_price = _rank_scores(prices, ascending=True)  # 低价高分
        r_age = _rank_scores(ages, ascending=True)      # 年龄小高分

        scored: list[tuple[str, float]] = []
        for i, code in enumerate(codes):
            s = (cfg.weight_size * r_size[i]
                 + cfg.weight_premium * r_prem[i]
                 + cfg.weight_price * r_price[i]
                 + cfg.weight_age * r_age[i])
            scored.append((code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]


# ── CBUltra 无年龄限制变体 ────────────────────────────

@dataclass
class CBUltraNoAgeConfig(CBRotationConfig):
    top_k: int = 5
    rebalance_days: int = 1
    max_price: float = 200.0
    max_issue_size: float = 20000.0
    min_issue_size: float = 2000.0
    min_turnover: float = 5.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.50
    lot_size: int = 10
    max_premium: float = 100.0
    weight_size: float = 0.60
    weight_premium: float = 0.20
    weight_price: float = 0.20


class CBUltraNoAgeStrategy(CBRotationStrategy):
    """极致小规模 (无年龄过滤)"""
    name = "cb_ultra_noage"

    def __init__(self, config: CBUltraNoAgeConfig | None = None, **kwargs: object):
        super().__init__(config=config or CBUltraNoAgeConfig(), **kwargs)

    @property
    def config(self) -> CBUltraNoAgeConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        issue_size = getattr(candidate, "issue_size", None) or float("inf")
        if issue_size < cfg.min_issue_size or issue_size > cfg.max_issue_size:
            return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        turnover = getattr(candidate, "turnover", None) or 0.0
        return turnover >= cfg.min_turnover

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        codes = [c.code for c in candidates]
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        prices = [c.price for c in candidates]

        r_size = _rank_scores(sizes, ascending=True)
        r_prem = _rank_scores(premiums, ascending=True)
        r_price = _rank_scores(prices, ascending=True)

        scored: list[tuple[str, float]] = []
        for i, code in enumerate(codes):
            s = (cfg.weight_size * r_size[i]
                 + cfg.weight_premium * r_prem[i]
                 + cfg.weight_price * r_price[i])
            scored.append((code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
