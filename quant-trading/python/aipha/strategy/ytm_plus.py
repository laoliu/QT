"""YTM增强策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo, days_to_maturity
logger = logging.getLogger(__name__)

@dataclass
class YTMPlusConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 10; max_price: float = 115.0
    face_value: float = 100.0; min_years_to_maturity: float = 0.5; max_premium: float = 50.0
    ytm_weight: float = 0.5; premium_weight: float = 0.5

class YTMPlusStrategy(CBRotationStrategy):
    name = "ytm_plus"
    def __init__(self, config: YTMPlusConfig | None = None, **kw: object):
        super().__init__(config=config or YTMPlusConfig(), **kw)
    @property
    def config(self) -> YTMPlusConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > cfg.max_premium: return False
        ry = getattr(c, "remain_years", None)
        if ry is not None and ry < cfg.min_years_to_maturity: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        ytm = {}
        for c in candidates:
            price = getattr(c, "close", 100.0) or 100.0
            ry = getattr(c, "remain_years", 1.0) or 1.0
            ytm[c.code] = (cfg.face_value / price) ** (1.0 / ry) - 1.0 if price > 0 and ry > 0 else 0.0
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        ry, rp = _rank_scores(ytm), _rank_scores(prem)
        composite = {c.code: cfg.ytm_weight * ry.get(c.code, 0) + cfg.premium_weight * rp.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
