"""PEAD盈余漂移策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_earnings_growth_factor, compute_momentum_factor,
    compute_value_factor, compute_quality_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class PeadDriftConfig(EquityRotationConfig):
    top_k: int = 20; rebalance_days: int = 10; min_mv: float = 30e9; max_mv: float = 500e9
    weight_earnings: float = 0.40; weight_momentum: float = 0.25; weight_value: float = 0.20
    weight_quality: float = 0.15; momentum_lookback: int = 20

class PeadDriftStrategy(EquityRotationStrategy):
    name = "pead_drift"
    def __init__(self, config: PeadDriftConfig | None = None, **kw: object):
        super().__init__(config=config or PeadDriftConfig(), **kw)
    @property
    def config(self) -> PeadDriftConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config; mv = getattr(c, "circ_mv", None)
        if mv is not None and (mv < cfg.min_mv or mv > cfg.max_mv): return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        eg = compute_earnings_growth_factor(candidates); mom = compute_momentum_factor(ph, candidates)
        val = compute_value_factor(candidates); qual = compute_quality_factor(candidates)
        composite = {c.code: cfg.weight_earnings * eg.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) + cfg.weight_value * val.get(c.code, 0) + cfg.weight_quality * qual.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
