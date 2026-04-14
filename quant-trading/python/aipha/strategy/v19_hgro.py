"""V19 高成长小盘策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_ep_factor, compute_value_factor,
    compute_momentum_factor, compute_earnings_growth_factor, compute_size_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class V19HgroConfig(EquityRotationConfig):
    top_k: int = 15; rebalance_days: int = 20; min_mv: float = 2e9; max_mv: float = 30e9
    weight_small_size: float = 0.40; weight_momentum: float = 0.25; weight_growth: float = 0.20
    weight_ep: float = 0.10; weight_bp: float = 0.05
    momentum_lookback: int = 60; vol_scaling: bool = True; vol_lookback: int = 20
    mdd_gradient: bool = True; mdd_window: int = 60

class V19HgroStrategy(EquityRotationStrategy):
    name = "v19_hgro"
    def __init__(self, config: V19HgroConfig | None = None, **kw: object):
        super().__init__(config=config or V19HgroConfig(), **kw)
    @property
    def config(self) -> V19HgroConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        mv = getattr(c, "circ_mv", None)
        if mv is not None and (mv < cfg.min_mv or mv > cfg.max_mv): return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        sz = compute_size_factor(candidates); mom = compute_momentum_factor(ph, candidates)
        grw = compute_earnings_growth_factor(candidates)
        ep = compute_ep_factor(candidates); bp = compute_value_factor(candidates)
        composite = {c.code: cfg.weight_small_size * sz.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) + cfg.weight_growth * grw.get(c.code, 0) + cfg.weight_ep * ep.get(c.code, 0) + cfg.weight_bp * bp.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
