"""低溢价可转债"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class LowPremiumConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5; max_premium: float = 50.0; min_premium: float = -20.0
    weight_premium: float = 0.50; weight_price: float = 0.30; weight_volume: float = 0.20

class LowPremiumStrategy(CBRotationStrategy):
    name = "low_premium"
    def __init__(self, config: LowPremiumConfig | None = None, **kw: object):
        super().__init__(config=config or LowPremiumConfig(), **kw)
    @property
    def config(self) -> LowPremiumConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        p = getattr(c, "cb_over_rate", None)
        if p is None:
            p = getattr(c, "premium_rate", None)
            if p is not None: p = p * 100.0
        if p is None: return False
        if p > cfg.max_premium or p < cfg.min_premium: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        valid = []
        for c in candidates:
            p = getattr(c, "cb_over_rate", None)
            if p is None:
                p = getattr(c, "premium_rate", None)
                if p is not None: p = p * 100.0
            if p is None: continue
            valid.append(c)
        prem = {c.code: -(getattr(c, "cb_over_rate", None) or (getattr(c, "premium_rate", 0.5) or 0.5) * 100) for c in valid}
        price = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in valid}
        vol = {c.code: getattr(c, "volume", 0.0) or 0.0 for c in valid}
        rp, rprice, rv = _rank_scores(prem), _rank_scores(price), _rank_scores(vol)
        composite = {c.code: cfg.weight_premium * rp.get(c.code, 0) + cfg.weight_price * rprice.get(c.code, 0) + cfg.weight_volume * rv.get(c.code, 0) for c in valid}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
