"""参考五因子策略（EP/BP/ROE/股息率/低波 + 可选象限分层）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_ep_factor, compute_value_factor, compute_quality_factor,
    compute_dividend_factor, compute_volatility_factor, compute_size_factor, compute_momentum_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class QuadrantSpec:
    name: str; mv_threshold: float; style: str; top_k: int; weight_alloc: float

@dataclass
class ReferenceFiveFactorConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 21; min_mv: float = 100_000.0
    weight_ep: float = 0.20; weight_bp: float = 0.20; weight_roe: float = 0.20
    weight_dy: float = 0.20; weight_lv: float = 0.20; weight_size: float = 0.0; weight_momentum: float = 0.0
    roe_as_filter: bool = False; min_roe: float = 5.0
    quadrant_enabled: bool = True; mv_split_percentile: float = 50.0; style_split_method: str = "bp"
    min_industries: int = 6; max_industry_pct: float = 0.12

class ReferenceFiveFactorStrategy(EquityRotationStrategy):
    name = "reference_five_factor"
    def __init__(self, config: ReferenceFiveFactorConfig | None = None, **kw: object):
        super().__init__(config=config or ReferenceFiveFactorConfig(), **kw)
    @property
    def config(self) -> ReferenceFiveFactorConfig: return self._config  # type: ignore
    def _score_flat(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        ep = compute_ep_factor(candidates); bp = compute_value_factor(candidates)
        roe = compute_quality_factor(candidates); dy = compute_dividend_factor(candidates)
        lv = compute_volatility_factor(ph, candidates)
        composite = {c.code: cfg.weight_ep * ep.get(c.code, 0) + cfg.weight_bp * bp.get(c.code, 0) + cfg.weight_roe * roe.get(c.code, 0) + cfg.weight_dy * dy.get(c.code, 0) + cfg.weight_lv * lv.get(c.code, 0) for c in candidates}
        if cfg.weight_size:
            sz = compute_size_factor(candidates)
            for c in candidates: composite[c.code] = composite.get(c.code, 0) + cfg.weight_size * sz.get(c.code, 0)
        if cfg.weight_momentum:
            mom = compute_momentum_factor(ph, candidates)
            for c in candidates: composite[c.code] = composite.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0)
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config
        if cfg.roe_as_filter:
            candidates = [c for c in candidates if (getattr(c, "roe", None) or 0) >= cfg.min_roe]
        return self._score_flat(candidates, ctx)
