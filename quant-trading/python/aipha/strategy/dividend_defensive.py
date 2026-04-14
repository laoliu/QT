"""股息防御策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_dividend_factor, compute_momentum_factor, compute_quality_factor, compute_value_factor, compute_volatility_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class DividendDefensiveConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 20; min_price: float = 3.0; max_price: float = 200.0
    min_turnover: float = 500.0; min_listed_days: int = 120; stop_loss_pct: float = 0.15
    buffer_k: int = 8; max_single_pct: float = 0.05; min_mv: float = 200_000.0
    weight_dividend: float = 0.35; weight_quality: float = 0.25; weight_volatility: float = 0.20
    weight_value: float = 0.10; weight_momentum: float = 0.10
    min_dividend_yield: float = 0.5; min_roe: float = 5.0; max_pe: float = 60.0; max_debt_to_assets: float = 75.0
    momentum_lookback: int = 20; volatility_lookback: int = 60

class DividendDefensiveStrategy(EquityRotationStrategy):
    name = "dividend_defensive"
    def __init__(self, config: DividendDefensiveConfig | None = None, **kw: object):
        super().__init__(config=config or DividendDefensiveConfig(), **kw)
    @property
    def config(self) -> DividendDefensiveConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        dv = getattr(c, "dv_ratio", None)
        if dv is not None and dv < cfg.min_dividend_yield: return False
        roe = getattr(c, "roe", None)
        if roe is not None and roe < cfg.min_roe: return False
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        div = compute_dividend_factor(candidates); qual = compute_quality_factor(candidates)
        vol = compute_volatility_factor(ph, candidates, lookback=cfg.volatility_lookback)
        val = compute_value_factor(candidates)
        mom = compute_momentum_factor(ph, candidates, lookback=cfg.momentum_lookback)
        composite = {c.code: cfg.weight_dividend * div.get(c.code, 0.0) + cfg.weight_quality * qual.get(c.code, 0.0) + cfg.weight_volatility * vol.get(c.code, 0.0) + cfg.weight_value * val.get(c.code, 0.0) + cfg.weight_momentum * mom.get(c.code, 0.0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
