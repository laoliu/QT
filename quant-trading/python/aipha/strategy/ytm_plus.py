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
        p = getattr(c, "cb_over_rate", None)
        if p is None:
            p = getattr(c, "premium_rate", None)
            if p is not None: p = p * 100.0
        if p is None: return False
        if p > cfg.max_premium: return False
        ry = getattr(c, "remain_years", None)
        if ry is not None and ry < cfg.min_years_to_maturity: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if len(candidates) < 2:
            return []
        from aipha.strategy.cb_ultra import _rank_scores
        from aipha.strategy.cb_rotation_base import days_to_maturity
        cfg = self.config
        ytm_vals: list[float] = []
        prem_vals: list[float] = []
        now = __import__("datetime").datetime.now()
        for c in candidates:
            price = c.close or 100.0
            mat = c.maturity_date
            ry = days_to_maturity(mat, now) / 365.25 if mat else 1.0
            if ry <= 0:
                ry = 1.0
            ytm_vals.append((cfg.face_value / price) ** (1.0 / ry) - 1.0 if price > 0 else 0.0)
            prem_vals.append(-(c.cb_over_rate or 50.0))
        r_ytm = _rank_scores(ytm_vals, ascending=False)
        r_prem = _rank_scores(prem_vals, ascending=False)
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = cfg.ytm_weight * r_ytm[i] + cfg.premium_weight * r_prem[i]
            scored.append((c.symbol, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
