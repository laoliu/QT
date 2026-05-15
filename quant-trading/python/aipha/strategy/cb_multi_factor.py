"""可转债多因子策略 — 价格/溢价/转债价值/规模/delta/评级/YTM 加权"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from aipha.strategy.cb_enhanced_factors import delta_exposure, enhanced_ytm, rating_score
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.cb_ultra import _rank_scores

if TYPE_CHECKING:
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class MultiFactorConfig(CBRotationConfig):
    top_k: int = 20
    rebalance_days: int = 3
    price_weight: float = 0.25
    premium_weight: float = 0.20
    cb_value_weight: float = 0.00
    size_weight: float = 0.25
    delta_weight: float = 0.10
    rating_weight: float = 0.20
    ytm_weight: float = 0.00
    max_price: float = 130.0
    max_premium: float = 40.0
    min_turnover: float = 50.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.15
    buffer_k: int = 0
    lot_size: int = 10


class MultiFactorStrategy(CBRotationStrategy):
    """可转债多因子 — 7 因子 rank_fusion 合成"""
    name = "multi_factor"

    def __init__(self, config: MultiFactorConfig | None = None, **kwargs: object):
        super().__init__(config=config or MultiFactorConfig(), **kwargs)

    @property
    def config(self) -> MultiFactorConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        p = getattr(candidate, "cb_over_rate", None)
        if p is None:
            p = getattr(candidate, "premium_rate", None)
            if p is not None: p = p * 100.0
        if p is None: return False
        if p > cfg.max_premium: return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        now = datetime.now()

        prices = [c.price for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        deltas = [delta_exposure(c) for c in candidates]
        ratings = [rating_score(getattr(c, "rating", "") or "") for c in candidates]
        ytms = []
        for c in candidates:
            mat = getattr(c, "maturity_date", None)
            ytms.append(enhanced_ytm(c.price, mat, now) if mat else 0.0)

        r_price = _rank_scores(prices, ascending=True)     # 低价高分
        r_prem = _rank_scores(premiums, ascending=True)     # 低溢价高分
        r_size = _rank_scores(sizes, ascending=True)        # 小规模高分
        r_delta = _rank_scores(deltas, ascending=False)     # 高 delta 高分
        r_rating = _rank_scores(ratings, ascending=False)   # 高评级高分
        r_ytm = _rank_scores(ytms, ascending=False)         # 高 YTM 高分

        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = (cfg.price_weight * r_price[i]
                 + cfg.premium_weight * r_prem[i]
                 + cfg.size_weight * r_size[i]
                 + cfg.delta_weight * r_delta[i]
                 + cfg.rating_weight * r_rating[i]
                 + cfg.ytm_weight * r_ytm[i])
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
