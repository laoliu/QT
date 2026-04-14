"""行业IC倾斜策略"""
from __future__ import annotations
import logging
from collections import defaultdict
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_value_factor, compute_quality_factor,
    compute_momentum_factor, compute_earnings_growth_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class IndustryIcTiltConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 20; max_pe: float = 80.0; momentum_lookback: int = 60
    max_industry_pct: float = 0.25; weight_quality: float = 0.25; weight_value: float = 0.25
    weight_momentum: float = 0.25; weight_growth: float = 0.25
    ic_lookback: int = 60; tilt_strength: float = 0.30

class IndustryIcTiltStrategy(EquityRotationStrategy):
    name = "industry_ic_tilt"
    def __init__(self, config: IndustryIcTiltConfig | None = None, **kw: object):
        super().__init__(config=config or IndustryIcTiltConfig(), **kw)
    @property
    def config(self) -> IndustryIcTiltConfig: return self._config  # type: ignore
    def _compute_industry_tilt(self, candidates: list[EquityCandidateInfo]) -> dict[str, float]:
        by_ind: dict[str, list[float]] = defaultdict(list)
        for c in candidates:
            ind = getattr(c, "industry", "") or ""
            roe = getattr(c, "roe", 0.0) or 0.0
            by_ind[ind].append(roe)
        avg_roe = {ind: sum(vals) / len(vals) for ind, vals in by_ind.items() if vals}
        if not avg_roe: return {}
        lo, hi = min(avg_roe.values()), max(avg_roe.values())
        rng = hi - lo if hi > lo else 1.0
        return {ind: (v - lo) / rng for ind, v in avg_roe.items()}
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > self.config.max_pe: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        val = compute_value_factor(candidates); qual = compute_quality_factor(candidates)
        mom = compute_momentum_factor(ph, candidates); grw = compute_earnings_growth_factor(candidates)
        ind_tilt = self._compute_industry_tilt(candidates)
        composite: dict[str, float] = {}
        for c in candidates:
            base = cfg.weight_value * val.get(c.code, 0) + cfg.weight_quality * qual.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) + cfg.weight_growth * grw.get(c.code, 0)
            ind = getattr(c, "industry", "") or ""
            tilt = ind_tilt.get(ind, 0.5)
            composite[c.code] = base * (1 + cfg.tilt_strength * (tilt - 0.5))
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
