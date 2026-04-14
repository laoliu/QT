"""V23 风格均衡策略（2×2 Fama-French象限）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_ep_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor, compute_earnings_growth_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

QUADRANT_NAMES = ["large_value", "large_growth", "small_value", "small_growth"]

@dataclass
class V23SbalConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 20; min_mv: float = 5e9; max_mv: float = 1000e9
    quadrant_stocks: int = 8; weight_ep: float = 0.25; weight_growth: float = 0.25
    weight_roe: float = 0.20; weight_low_vol: float = 0.15; weight_momentum: float = 0.15
    mv_split_percentile: float = 50.0; mdd_gradient: bool = True

class V23SbalStrategy(EquityRotationStrategy):
    name = "v23_sbal"
    def __init__(self, config: V23SbalConfig | None = None, **kw: object):
        super().__init__(config=config or V23SbalConfig(), **kw)
    @property
    def config(self) -> V23SbalConfig: return self._config  # type: ignore
    def _classify_quadrants(self, candidates: list[EquityCandidateInfo]) -> dict[str, list[EquityCandidateInfo]]:
        mvs = sorted(getattr(c, "circ_mv", 0) or 0 for c in candidates)
        median_mv = mvs[len(mvs) // 2] if mvs else 0
        bps = {c.code: getattr(c, "pb", 1) or 1 for c in candidates}
        median_bp = sorted(bps.values())[len(bps) // 2] if bps else 1
        quads: dict[str, list[EquityCandidateInfo]] = {q: [] for q in QUADRANT_NAMES}
        for c in candidates:
            mv = getattr(c, "circ_mv", 0) or 0; bp = bps.get(c.code, 1)
            is_large = mv >= median_mv; is_value = bp <= median_bp
            if is_large and is_value: quads["large_value"].append(c)
            elif is_large: quads["large_growth"].append(c)
            elif is_value: quads["small_value"].append(c)
            else: quads["small_growth"].append(c)
        return quads
    def _score_within_quadrant(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        ep = compute_ep_factor(candidates); grw = compute_earnings_growth_factor(candidates)
        roe = compute_quality_factor(candidates); vol = compute_volatility_factor(ph, candidates)
        mom = compute_momentum_factor(ph, candidates)
        composite = {c.code: cfg.weight_ep * ep.get(c.code, 0) + cfg.weight_growth * grw.get(c.code, 0) + cfg.weight_roe * roe.get(c.code, 0) + cfg.weight_low_vol * vol.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; quads = self._classify_quadrants(candidates)
        result: list[str] = []
        for qname in QUADRANT_NAMES:
            group = quads[qname]
            if not group: continue
            ranked = self._score_within_quadrant(group, ctx)
            result.extend(ranked[:cfg.quadrant_stocks])
        return result[:cfg.top_k]
