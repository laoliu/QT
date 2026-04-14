"""盈余惊喜阿尔法策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_earnings_growth_factor, compute_quality_factor,
    compute_size_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class EarningsSurpriseConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 10; min_mv: float = 20e9; max_mv: float = 300e9
    weight_earnings: float = 0.35; weight_reversal: float = 0.25; weight_quality: float = 0.20
    weight_size: float = 0.20; reversal_lookback: int = 20

def _compute_reversal_factor(candidates: list[EquityCandidateInfo]) -> dict[str, float]:
    raw = {c.code: -(getattr(c, f"return_{20}d", 0.0) or getattr(c, "return_20d", 0.0) or 0.0) for c in candidates}
    if not raw: return {}
    lo, hi = min(raw.values()), max(raw.values()); rng = hi - lo if hi > lo else 1.0
    return {k: (v - lo) / rng for k, v in raw.items()}

class EarningsSurpriseAlphaStrategy(EquityRotationStrategy):
    name = "earnings_surprise_alpha"
    def __init__(self, config: EarningsSurpriseConfig | None = None, **kw: object):
        super().__init__(config=config or EarningsSurpriseConfig(), **kw)
    @property
    def config(self) -> EarningsSurpriseConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config; mv = getattr(c, "circ_mv", None)
        if mv is not None and (mv < cfg.min_mv or mv > cfg.max_mv): return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config
        eg = compute_earnings_growth_factor(candidates); rev = _compute_reversal_factor(candidates)
        qual = compute_quality_factor(candidates); sz = compute_size_factor(candidates)
        composite = {c.code: cfg.weight_earnings * eg.get(c.code, 0) + cfg.weight_reversal * rev.get(c.code, 0) + cfg.weight_quality * qual.get(c.code, 0) + cfg.weight_size * sz.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
