"""ST摘帽博弈策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_earnings_growth_factor, compute_piotroski_factor,
    compute_value_factor, compute_size_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class StRemovalPlayConfig(EquityRotationConfig):
    top_k: int = 10; rebalance_days: int = 15; max_single_pct: float = 0.12
    min_mv: float = 100_000.0; max_mv: float = 1_000_000.0; max_industry_pct: float = 0.30
    weight_earnings: float = 0.40; weight_fscore: float = 0.25; weight_value: float = 0.20
    weight_size: float = 0.15; min_fscore: float = 5.0; max_pe: float = 80.0

class StRemovalPlayStrategy(EquityRotationStrategy):
    name = "st_removal_play"
    def __init__(self, config: StRemovalPlayConfig | None = None, **kw: object):
        super().__init__(config=config or StRemovalPlayConfig(), **kw)
    @property
    def config(self) -> StRemovalPlayConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe: return False
        fs = getattr(c, "f_score", None)
        if fs is not None and fs < cfg.min_fscore: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config
        eg = compute_earnings_growth_factor(candidates); fs = compute_piotroski_factor(candidates)
        val = compute_value_factor(candidates); sz = compute_size_factor(candidates)
        composite = {c.code: cfg.weight_earnings * eg.get(c.code, 0) + cfg.weight_fscore * fs.get(c.code, 0) + cfg.weight_value * val.get(c.code, 0) + cfg.weight_size * sz.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
