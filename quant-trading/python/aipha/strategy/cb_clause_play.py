"""可转债条款博弈策略（回售/到期/下修）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class ClausePlayCBConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5; max_price: float = 130.0; max_premium: float = 50.0
    min_age_days: int = 30; max_issue_size: float = float("inf")
    weight_price: float = 0.20; weight_premium: float = 0.15; weight_putback: float = 0.30
    weight_maturity: float = 0.25; weight_issue_size: float = 0.10
    putback_trigger_ratio: float = 0.70; min_catalyst_score: float = 0.0

class CBClausePlayStrategy(CBRotationStrategy):
    name = "cb_clause_play"
    def __init__(self, config: ClausePlayCBConfig | None = None, **kw: object):
        super().__init__(config=config or ClausePlayCBConfig(), **kw)
    @property
    def config(self) -> ClausePlayCBConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > cfg.max_premium: return False
        age = getattr(c, "listed_days", None)
        if age is not None and age < cfg.min_age_days: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        price_s = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem_s = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        # 回售价值: cb_value 接近 putback_price 越好
        putback_s: dict[str, float] = {}
        for c in candidates:
            cv = getattr(c, "cb_value", None) or getattr(c, "close", 100.0)
            pp = getattr(c, "putback_price", 100.0) or 100.0
            ratio = cv / pp if pp > 0 else 1.0
            putback_s[c.code] = -(abs(ratio - cfg.putback_trigger_ratio))
        mat_s = {c.code: -(getattr(c, "remain_years", 6.0) or 6.0) for c in candidates}
        size_s = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        rp, rprem, rput, rmat, rsz = _rank_scores(price_s), _rank_scores(prem_s), _rank_scores(putback_s), _rank_scores(mat_s), _rank_scores(size_s)
        composite = {c.code: cfg.weight_price * rp.get(c.code, 0) + cfg.weight_premium * rprem.get(c.code, 0) + cfg.weight_putback * rput.get(c.code, 0) + cfg.weight_maturity * rmat.get(c.code, 0) + cfg.weight_issue_size * rsz.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
