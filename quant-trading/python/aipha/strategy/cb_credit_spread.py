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
    ytm_mode: str = "enhanced"; face_value: float = 100.0

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
        from aipha.strategy.cb_rotation_base import days_to_maturity
        cfg = self.config
        now = __import__("datetime").datetime.now()
        ytm_v: list[float] = []
        rat_v: list[float] = []
        bf_v: list[float] = []
        price_v: list[float] = []
        prem_v: list[float] = []
        size_v: list[float] = []
        for c in candidates:
            price = c.close or 100.0
            mat = c.maturity_date
            ry = days_to_maturity(mat, now) / 365.25 if mat else 1.0
            if ry <= 0:
                ry = 1.0
            ytm_v.append((cfg.face_value / price) ** (1.0 / ry) - 1.0 if price > 0 else 0.0)
            rat_v.append(rating_to_score(c.rating))
            bf_v.append(0.0)  # bond_floor not available
            price_v.append(-price)
            prem_v.append(-(c.cb_over_rate or 50.0))
            size_v.append(-(c.issue_size or 1e9))
        r_ytm = _rank_scores(ytm_v, ascending=False)
        r_rat = _rank_scores(rat_v, ascending=False)
        r_bf = _rank_scores(bf_v, ascending=False)
        r_price = _rank_scores(price_v, ascending=False)
        r_prem = _rank_scores(prem_v, ascending=False)
        r_size = _rank_scores(size_v, ascending=False)
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = (cfg.weight_ytm * r_ytm[i] + cfg.weight_rating * r_rat[i]
                 + cfg.weight_bond_floor * r_bf[i] + cfg.weight_price * r_price[i]
                 + cfg.weight_premium * r_prem[i] + cfg.weight_issue_size * r_size[i])
            scored.append((c.symbol, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
