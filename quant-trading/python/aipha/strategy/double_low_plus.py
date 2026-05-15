"""双低增强策略 — 6因子 (价格/溢价/规模/年龄/均值回归/评级)"""
from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_since_listed,
)
from aipha.strategy.cb_ultra import _rank_scores

if TYPE_CHECKING:
    from aipha.domain.models import Bar
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)

_RATING_SCORE: dict[str, int] = {
    "AAA": 100, "AA+": 90, "AA": 80, "AA-": 70,
    "A+": 60, "A": 50, "A-": 40,
    "BBB+": 30, "BBB": 25, "BBB-": 20,
    "BB+": 15, "BB": 12, "BB-": 10,
    "B+": 8, "B": 5, "B-": 3,
    "CCC": 2, "CC": 1, "C": 0,
}


def _get_rating_score(rating: str) -> int:
    return _RATING_SCORE.get(rating.strip().upper(), -1) if rating else -1


@dataclass
class DoubleLowPlusConfig(CBRotationConfig):
    top_k: int = 10
    rebalance_days: int = 3
    max_price: float = 145.0
    max_issue_size: float = 30000.0
    min_issue_size: float = 1000.0
    min_turnover: float = 15.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.30
    lot_size: int = 10
    use_age_filter: bool = True
    min_age_days: int = 3
    max_age_days: int = 180
    fallback_max_age_days: int = 730
    max_premium: float = 70.0
    lookback: int = 20
    bb_k: float = 2.0
    weight_price: float = 0.20
    weight_premium: float = 0.20
    weight_size: float = 0.15
    weight_age: float = 0.10
    weight_mean_reversion: float = 0.20
    weight_rating: float = 0.15
    min_candidates: int = 5


class DoubleLowPlusStrategy(CBRotationStrategy):
    """双低增强 — 6因子加权: 价格 + 溢价率 + 规模 + 年龄 + 均值回归 + 评级"""
    name = "double_low_plus"

    def __init__(self, config: DoubleLowPlusConfig | None = None, **kwargs: object):
        super().__init__(config=config or DoubleLowPlusConfig(), **kwargs)
        self._price_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=200)
        )
        self._effective_max_age = self.config.max_age_days

    @property
    def config(self) -> DoubleLowPlusConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        self._price_history.clear()
        self._effective_max_age = self.config.max_age_days

    def _relax_filters_one_step(self) -> bool:
        cfg = self.config
        if cfg.fallback_max_age_days <= 0:
            return False
        if self._effective_max_age < cfg.fallback_max_age_days:
            self._effective_max_age = min(
                self._effective_max_age + 90, cfg.fallback_max_age_days,
            )
            return True
        return False

    def _maybe_tighten_filters(self) -> None:
        cfg = self.config
        if self._effective_max_age > cfg.max_age_days:
            self._effective_max_age = max(
                self._effective_max_age - 30, cfg.max_age_days,
            )

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            self._price_history[code].append(bar.close)

    def _should_rebalance(self) -> bool:
        return self._bar_count > 1 and (self._bar_count - 1) % self.config.rebalance_days == 0

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        issue_size = getattr(candidate, "issue_size", None) or float("inf")
        if issue_size < cfg.min_issue_size or issue_size > cfg.max_issue_size:
            return False
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        turnover = getattr(candidate, "amount", None) or 0.0
        if turnover < cfg.min_turnover:
            return False
        if cfg.use_age_filter:
            listed = getattr(candidate, "list_date", None)
            if listed:
                age = days_since_listed(listed, now=getattr(context, "current_time", None))
                if age < cfg.min_age_days or age > self._effective_max_age:
                    return False
        return True

    def _compute_bb_deviation(self, symbol: str, close: float) -> float | None:
        hist = self._price_history.get(symbol)
        lb = self.config.lookback
        if hist is None or len(hist) < lb:
            return None
        prices = list(hist)[-lb:]
        mean = sum(prices) / lb
        std = math.sqrt(sum((p - mean) ** 2 for p in prices) / lb)
        if std < 1e-9:
            return None
        return (mean - close) / std

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        codes = [c.code for c in candidates]
        prices = [c.price for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        ages = []
        now = getattr(context, "current_time", None)
        for c in candidates:
            listed = getattr(c, "list_date", None)
            ages.append(float(days_since_listed(listed, now=now)) if listed else 365.0)
        bb_devs = []
        for c in candidates:
            d = self._compute_bb_deviation(c.code, c.price)
            bb_devs.append(d if d is not None else 0.0)
        ratings = [float(_get_rating_score(getattr(c, "rating", "") or "")) for c in candidates]

        r_price = _rank_scores(prices, ascending=False)
        r_prem = _rank_scores(premiums, ascending=False)
        r_size = _rank_scores(sizes, ascending=False)
        r_age = _rank_scores(ages, ascending=False)
        r_bb = _rank_scores(bb_devs, ascending=False)  # 低偏离高分
        r_rating = _rank_scores(ratings, ascending=True)  # 高评级高分

        scored: list[tuple[str, float]] = []
        for i, code in enumerate(codes):
            s = (cfg.weight_price * r_price[i]
                 + cfg.weight_premium * r_prem[i]
                 + cfg.weight_size * r_size[i]
                 + cfg.weight_age * r_age[i]
                 + cfg.weight_mean_reversion * r_bb[i]
                 + cfg.weight_rating * r_rating[i])
            scored.append((code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
