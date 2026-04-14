"""V22 均衡多因子策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_ep_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor, compute_size_factor, compute_earnings_growth_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class V22BmfConfig(EquityRotationConfig):
    top_k: int = 20; rebalance_days: int = 20; min_mv: float = 5e9; max_mv: float = 150e9
    weight_size: float = 0.25; weight_momentum: float = 0.25; weight_ep: float = 0.15
    weight_profit_growth: float = 0.15; weight_roe: float = 0.10; weight_low_vol: float = 0.10
    momentum_lookback: int = 60; vol_lookback: int = 60

class V22BmfStrategy(EquityRotationStrategy):
    name = "v22_bmf"
    def __init__(self, config: V22BmfConfig | None = None, **kw: object):
        super().__init__(config=config or V22BmfConfig(), **kw)
    @property
    def config(self) -> V22BmfConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config; mv = getattr(c, "circ_mv", None)
        if mv is not None and (mv < cfg.min_mv or mv > cfg.max_mv): return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        sz = compute_size_factor(candidates); mom = compute_momentum_factor(ph, candidates)
        ep = compute_ep_factor(candidates); grw = compute_earnings_growth_factor(candidates)
        roe = compute_quality_factor(candidates)
        vol = compute_volatility_factor(ph, candidates, lookback=cfg.vol_lookback)
        composite = {c.code: cfg.weight_size * sz.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) + cfg.weight_ep * ep.get(c.code, 0) + cfg.weight_profit_growth * grw.get(c.code, 0) + cfg.weight_roe * roe.get(c.code, 0) + cfg.weight_low_vol * vol.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
