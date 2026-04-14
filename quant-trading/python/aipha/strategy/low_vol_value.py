"""低波动价值策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_quality_factor, compute_turnover_factor, compute_value_factor, compute_volatility_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class LowVolValueConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 20; min_price: float = 3.0; max_price: float = 300.0
    min_turnover: float = 1000.0; min_listed_days: int = 120; stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12; buffer_k: int = 10; max_single_pct: float = 0.05
    min_mv: float = 500_000.0; max_mv: float = float("inf"); inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20; weight_volatility: float = 0.35; weight_value: float = 0.30
    weight_quality: float = 0.20; weight_turnover: float = 0.15
    min_roe: float = 5.0; max_pe: float = 50.0; max_pb: float = 10.0
    volatility_lookback: int = 60

class LowVolValueStrategy(EquityRotationStrategy):
    name = "low_vol_value"
    def __init__(self, config: LowVolValueConfig | None = None, **kw: object):
        super().__init__(config=config or LowVolValueConfig(), **kw)
    @property
    def config(self) -> LowVolValueConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        roe = getattr(c, "roe", None)
        if roe is not None and roe < cfg.min_roe: return False
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe: return False
        pb = getattr(c, "pb", None)
        if pb is not None and pb > cfg.max_pb: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        vol = compute_volatility_factor(ph, candidates, lookback=cfg.volatility_lookback)
        val = compute_value_factor(candidates); qual = compute_quality_factor(candidates)
        turn = compute_turnover_factor(candidates)
        composite = {c.code: cfg.weight_volatility * vol.get(c.code, 0.0) + cfg.weight_value * val.get(c.code, 0.0) + cfg.weight_quality * qual.get(c.code, 0.0) + cfg.weight_turnover * turn.get(c.code, 0.0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
