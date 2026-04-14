"""可转债信用利差策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

RATING_SCORE_MAP: dict[str, float] = {"AAA": 8.0, "AA+": 7.0, "AA": 6.0, "AA-": 5.0, "A+": 4.0, "A": 3.0, "A-": 2.5, "BBB+": 2.0, "BBB": 1.5, "BBB-": 1.0, "BB+": 0.5, "BB": 0.0}
DEFAULT_RATING_SCORE: float = 2.0
def rating_to_score(rating: str) -> float:
    return RATING_SCORE_MAP.get(rating.strip().upper(), DEFAULT_RATING_SCORE) if rating else DEFAULT_RATING_SCORE

@dataclass
class CreditSpreadCBConfig(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 5; max_price: float = 150.0
    max_issue_size: float = float("inf"); min_turnover: float = 20.0; min_days_to_maturity: int = 60
    stop_loss_pct: float = 0.20; lot_size: int = 10; use_age_filter: bool = True; min_age_days: int = 3
    max_premium: float = 100.0; min_rating: str = ""
    weight_ytm: float = 0.25; weight_rating: float = 0.15; weight_bond_floor: float = 0.15
    weight_price: float = 0.20; weight_premium: float = 0.15; weight_issue_size: float = 0.10
    ytm_mode: str = "enhanced"

class CBCreditSpreadStrategy(CBRotationStrategy):
    name = "cb_credit_spread"
    def __init__(self, config: CreditSpreadCBConfig | None = None, **kw: object):
        super().__init__(config=config or CreditSpreadCBConfig(), **kw)
    @property
    def config(self) -> CreditSpreadCBConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > cfg.max_premium: return False
        if cfg.min_rating:
            r = getattr(c, "rating", "")
            if rating_to_score(r) < rating_to_score(cfg.min_rating): return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        ytm_s = {c.code: getattr(c, "ytm_to_maturity", 0.0) or 0.0 for c in candidates}
        rat_s = {c.code: rating_to_score(getattr(c, "rating", "")) for c in candidates}
        bf_s = {c.code: getattr(c, "bond_floor", 0.0) or 0.0 for c in candidates}
        price_s = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem_s = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        size_s = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        ry, rr, rb, rp, rpm, rs = _rank_scores(ytm_s), _rank_scores(rat_s), _rank_scores(bf_s), _rank_scores(price_s), _rank_scores(prem_s), _rank_scores(size_s)
        composite = {c.code: cfg.weight_ytm * ry.get(c.code, 0) + cfg.weight_rating * rr.get(c.code, 0) + cfg.weight_bond_floor * rb.get(c.code, 0) + cfg.weight_price * rp.get(c.code, 0) + cfg.weight_premium * rpm.get(c.code, 0) + cfg.weight_issue_size * rs.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
