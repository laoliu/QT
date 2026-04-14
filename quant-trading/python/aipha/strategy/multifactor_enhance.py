"""多因子增强策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_value_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor, compute_size_factor,
    compute_piotroski_factor, compute_valuation_percentile_factor,
    compute_accruals_quality_factor, compute_dividend_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class MultiFactorEnhanceConfig(EquityRotationConfig):
    top_k: int = 50; rebalance_days: int = 20; benchmark: str = "000300.SH"
    weight_value: float = 0.25; weight_quality: float = 0.20; weight_momentum: float = 0.20
    weight_volatility: float = 0.20; weight_size: float = 0.15
    weight_piotroski: float = 0.0; weight_valuation_pct: float = 0.0
    weight_accruals: float = 0.0; weight_dividend: float = 0.0
    value_metric: str = "pe_ttm"; volatility_lookback: int = 60

class MultiFactorEnhanceStrategy(EquityRotationStrategy):
    name = "multifactor_enhance"
    def __init__(self, config: MultiFactorEnhanceConfig | None = None, **kw: object):
        super().__init__(config=config or MultiFactorEnhanceConfig(), **kw)
    @property
    def config(self) -> MultiFactorEnhanceConfig: return self._config  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        factors: dict[str, dict[str, float]] = {"value": compute_value_factor(candidates), "quality": compute_quality_factor(candidates),
            "momentum": compute_momentum_factor(ph, candidates), "volatility": compute_volatility_factor(ph, candidates, lookback=cfg.volatility_lookback),
            "size": compute_size_factor(candidates)}
        if cfg.weight_piotroski: factors["piotroski"] = compute_piotroski_factor(candidates)
        if cfg.weight_valuation_pct: factors["valuation_pct"] = compute_valuation_percentile_factor(candidates)
        if cfg.weight_accruals: factors["accruals"] = compute_accruals_quality_factor(candidates)
        if cfg.weight_dividend: factors["dividend"] = compute_dividend_factor(candidates)
        weights = {n: getattr(cfg, f"weight_{n}", 0) for n in factors}
        composite = {c.code: sum(weights.get(n, 0) * factors[n].get(c.code, 0) for n in factors) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
