"""等权复合排名策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_value_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor, compute_size_factor, compute_turnover_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class CompositeRankEqualConfig(EquityRotationConfig):
    top_k: int = 40; rebalance_days: int = 20; max_pe: float = 100.0
    momentum_lookback: int = 60; momentum_skip_recent: int = 5; volatility_lookback: int = 60
    max_single_pct: float = 0.04; min_mv: float = 300_000.0; max_industry_pct: float = 0.20

class CompositeRankEqualStrategy(EquityRotationStrategy):
    name = "composite_rank_equal"
    def __init__(self, config: CompositeRankEqualConfig | None = None, **kw: object):
        super().__init__(config=config or CompositeRankEqualConfig(), **kw)
    @property
    def config(self) -> CompositeRankEqualConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > self.config.max_pe: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        factors = {"value": compute_value_factor(candidates), "quality": compute_quality_factor(candidates),
                   "momentum": compute_momentum_factor(ph, candidates), "volatility": compute_volatility_factor(ph, candidates, lookback=cfg.volatility_lookback),
                   "size": compute_size_factor(candidates), "turnover": compute_turnover_factor(candidates)}
        w = 1.0 / len(factors)
        composite = {c.code: sum(w * factors[n].get(c.code, 0) for n in factors) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
