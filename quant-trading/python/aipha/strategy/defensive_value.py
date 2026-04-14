"""防御价值策略 (可转债) — 到期收益率 + 低溢价 + 短到期优先"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_enhanced_factors import enhanced_ytm
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
    days_to_maturity,
)
from aipha.strategy.cb_ultra import _rank_scores

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class DefensiveValueConfig(CBRotationConfig):
    top_k: int = 25
    rebalance_days: int = 10
    buffer_k: int = 5
    min_issue_size: float = 30000.0
    max_issue_size: float = float("inf")
    max_price: float = 115.0
    max_premium: float = 40.0
    max_years_to_maturity: float = 5.0
    min_years_to_maturity_val: float = 0.5
    ytm_weight: float = 0.40
    premium_weight: float = 0.35
    maturity_weight: float = 0.25
    min_turnover: float = 30.0
    min_days_to_maturity: int = 180
    stop_loss_pct: float = 0.12
    min_positions: int = 10
    face_value: float = 100.0


class DefensiveValueStrategy(CBRotationStrategy):
    """防御价值 — 高 YTM + 低溢价 + 短到期 可转债"""
    name = "defensive_value"

    def __init__(self, config: DefensiveValueConfig | None = None, **kwargs: object):
        super().__init__(config=config or DefensiveValueConfig(), **kwargs)

    @property
    def config(self) -> DefensiveValueConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
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
        # 到期年限
        mat_date = getattr(candidate, "maturity_date", None)
        if mat_date:
            dtm = days_to_maturity(mat_date)
            years = dtm / 365.25
            if years > cfg.max_years_to_maturity or years < cfg.min_years_to_maturity_val:
                return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        now = datetime.now()

        ytms: list[float] = []
        premiums: list[float] = []
        maturities: list[float] = []

        for c in candidates:
            mat = getattr(c, "maturity_date", None)
            ytm = enhanced_ytm(c.price, mat, now) if mat else 0.0
            ytms.append(ytm)
            premiums.append((getattr(c, "premium_rate", 0.0) or 0.0) * 100)
            dtm = days_to_maturity(mat) if mat else 1000
            maturities.append(float(dtm))

        r_ytm = _rank_scores(ytms, ascending=False)     # 高 YTM 高分
        r_prem = _rank_scores(premiums, ascending=True)  # 低溢价高分
        r_mat = _rank_scores(maturities, ascending=True) # 短到期高分

        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = (cfg.ytm_weight * r_ytm[i]
                 + cfg.premium_weight * r_prem[i]
                 + cfg.maturity_weight * r_mat[i])
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
