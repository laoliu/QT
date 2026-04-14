"""伪到期收益率策略"""
from __future__ import annotations
import logging, math
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class PseudoYTMConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5; max_price: float = 100.0; face_value: float = 100.0
    weight_ytm: float = 0.60; weight_premium: float = 0.25; weight_volume: float = 0.15
    min_remain_years: float = 0.5; max_remain_years: float = 6.0

def _compute_pseudo_ytm(price: float, face: float, remain_years: float) -> float:
    if remain_years <= 0 or price <= 0: return -99.0
    return (face / price) ** (1.0 / remain_years) - 1.0

class PseudoYTMStrategy(CBRotationStrategy):
    name = "pseudo_ytm"
    def __init__(self, config: PseudoYTMConfig | None = None, **kw: object):
        super().__init__(config=config or PseudoYTMConfig(), **kw)
    @property
    def config(self) -> PseudoYTMConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        ry = getattr(c, "remain_years", None)
        if ry is not None and (ry < cfg.min_remain_years or ry > cfg.max_remain_years): return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        ytm = {}
        for c in candidates:
            price = getattr(c, "close", 100.0) or 100.0
            ry = getattr(c, "remain_years", 1.0) or 1.0
            ytm[c.code] = _compute_pseudo_ytm(price, cfg.face_value, ry)
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        vol = {c.code: getattr(c, "volume", 0.0) or 0.0 for c in candidates}
        ry, rp, rv = _rank_scores(ytm), _rank_scores(prem), _rank_scores(vol)
        composite = {c.code: cfg.weight_ytm * ry.get(c.code, 0) + cfg.weight_premium * rp.get(c.code, 0) + cfg.weight_volume * rv.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
