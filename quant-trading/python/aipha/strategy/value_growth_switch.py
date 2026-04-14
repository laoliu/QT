"""价值成长风格切换"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from aipha.factors.equity_factors import (compute_earnings_growth_factor, compute_momentum_factor,
    compute_quality_factor, compute_value_factor, compute_volatility_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class ValueGrowthSwitchConfig(EquityRotationConfig):
    top_k: int = 25; rebalance_days: int = 20; min_price: float = 5.0; max_price: float = 500.0
    min_turnover: float = 2000.0; min_listed_days: int = 250; stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10; buffer_k: int = 8; max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0; max_mv: float = float("inf"); inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20
    pe_median_high: float = 35.0; pe_median_low: float = 20.0; pe_lookback: int = 60
    value_weights: dict[str, float] = field(default_factory=lambda: {"ep": 0.40, "quality": 0.30, "vol": 0.20, "momentum": 0.10})
    growth_weights: dict[str, float] = field(default_factory=lambda: {"growth": 0.35, "momentum": 0.30, "quality": 0.20, "ep": 0.15})

class ValueGrowthSwitchStrategy(EquityRotationStrategy):
    name = "value_growth_switch"
    def __init__(self, config: ValueGrowthSwitchConfig | None = None, **kw: object):
        super().__init__(config=config or ValueGrowthSwitchConfig(), **kw)
        self._style_mode: str = "VALUE"
        self._pe_history: list[float] = []
    @property
    def config(self) -> ValueGrowthSwitchConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        cfg = self.config
        market_pe = getattr(ctx, "market_pe", None)
        if market_pe is not None:
            self._pe_history.append(market_pe)
            if len(self._pe_history) > cfg.pe_lookback:
                self._pe_history = self._pe_history[-cfg.pe_lookback:]
        if len(self._pe_history) >= 10:
            median_pe = sorted(self._pe_history)[len(self._pe_history) // 2]
            if median_pe > cfg.pe_median_high: self._style_mode = "GROWTH"
            elif median_pe < cfg.pe_median_low: self._style_mode = "VALUE"
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        ep = compute_value_factor(candidates); roe = compute_quality_factor(candidates)
        vol = compute_volatility_factor(ph, candidates); mom = compute_momentum_factor(ph, candidates)
        grw = compute_earnings_growth_factor(candidates)
        w = cfg.value_weights if self._style_mode == "VALUE" else cfg.growth_weights
        composite: dict[str, float] = {}
        for c in candidates:
            s = (w.get("ep", 0) * ep.get(c.code, 0) + w.get("quality", 0) * roe.get(c.code, 0) + w.get("vol", 0) * vol.get(c.code, 0) + w.get("momentum", 0) * mom.get(c.code, 0) + w.get("growth", 0) * grw.get(c.code, 0))
            composite[c.code] = s
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
