"""伪到期收益率策略"""
from __future__ import annotations
import logging, math
from datetime import datetime
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class PseudoYTMConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5; max_price: float = 100.0; face_value: float = 100.0
    weight_ytm: float = 0.60; weight_premium: float = 0.25; weight_volume: float = 0.15
    min_remain_years: float = 0.5; max_remain_years: float = 6.0

    @property
    def min_years_to_maturity(self) -> float:
        return self.min_remain_years

def _compute_pseudo_ytm(price: float, face: float, remain_years: float) -> float:
    if remain_years <= 0 or price <= 0: return -99.0
    return (face / price) ** (1.0 / remain_years) - 1.0

def _years_to_maturity(maturity_date: str, now: datetime) -> float:
    if not maturity_date:
        return 0.0
    try:
        if "-" in maturity_date:
            mat = datetime.strptime(maturity_date[:10], "%Y-%m-%d")
        else:
            mat = datetime.strptime(maturity_date[:8], "%Y%m%d")
        return (mat - now).days / 365.25
    except (ValueError, TypeError):
        return 0.0

class PseudoYTMStrategy(CBRotationStrategy):
    name = "pseudo_ytm"
    def __init__(self, config: PseudoYTMConfig | None = None, **kw: object):
        super().__init__(config=config or PseudoYTMConfig(), **kw)
    @property
    def config(self) -> PseudoYTMConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        price = getattr(c, "close", None)
        if price is not None and price > cfg.face_value: return False
        maturity_date = getattr(c, "maturity_date", "") or ""
        if not maturity_date:
            return False
        now = getattr(ctx, "current_time", None) or datetime.now()
        years = _years_to_maturity(maturity_date, now)
        if years < cfg.min_remain_years:
            return False
        if years > cfg.max_remain_years:
            return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if not candidates:
            return []
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        now = getattr(ctx, "current_time", None) or datetime.now()
        ytm = {}
        valid_candidates = []
        for c in candidates:
            price = getattr(c, "close", 100.0) or 100.0
            maturity_date = getattr(c, "maturity_date", "") or ""
            ry = _years_to_maturity(maturity_date, now) if maturity_date else 0.0
            if ry <= 0:
                continue
            ytm[c.code] = _compute_pseudo_ytm(price, cfg.face_value, ry)
            valid_candidates.append(c)
        if not valid_candidates:
            return []
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in valid_candidates}
        vol = {c.code: getattr(c, "volume", 0.0) or 0.0 for c in valid_candidates}
        ry, rp, rv = _rank_scores(ytm), _rank_scores(prem), _rank_scores(vol)
        composite = {c.code: cfg.weight_ytm * ry.get(c.code, 0) + cfg.weight_premium * rp.get(c.code, 0) + cfg.weight_volume * rv.get(c.code, 0) for c in valid_candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
