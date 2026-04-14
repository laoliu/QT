"""小盘股轮动策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from aipha.factors.equity_factors import compute_momentum_factor, compute_quality_factor, compute_size_factor, compute_turnover_factor, compute_value_factor, compute_volatility_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class SmallCapConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 15; min_mv: float = 100_000.0; max_mv: float = 2_000_000.0
    min_price: float = 3.0; max_price: float = 50.0; min_turnover: float = 500.0; min_listed_days: int = 60
    buffer_k: int = 8; momentum_invert: bool = False
    weight_small_cap: float = 0.30; weight_momentum: float = 0.15; weight_turnover: float = 0.10
    weight_volatility: float = 0.15; weight_quality: float = 0.15; weight_value: float = 0.15
    regime_adaptive: bool = False
    regime_bull_delta_small_cap: float = 0.0; regime_bull_delta_momentum: float = 0.05
    regime_bull_delta_value: float = -0.05; regime_bull_delta_quality: float = 0.0
    regime_bear_delta_small_cap: float = -0.05; regime_bear_delta_momentum: float = -0.05
    regime_bear_delta_value: float = 0.05; regime_bear_delta_quality: float = 0.05

class SmallCapRotationStrategy(EquityRotationStrategy):
    name = "smallcap_rotation"
    def __init__(self, config: SmallCapConfig | None = None, **kw: object):
        super().__init__(config=config or SmallCapConfig(), **kw)
    @property
    def config(self) -> SmallCapConfig: return self._config  # type: ignore
    def _get_regime_weights(self, context: StrategyContext) -> dict[str, float]:
        cfg = self.config
        w = {"small_cap": cfg.weight_small_cap, "momentum": cfg.weight_momentum, "turnover": cfg.weight_turnover, "volatility": cfg.weight_volatility, "quality": cfg.weight_quality, "value": cfg.weight_value}
        if cfg.regime_adaptive:
            regime = getattr(context, "market_regime", "NEUTRAL")
            if regime == "BULL":
                w["small_cap"] += cfg.regime_bull_delta_small_cap; w["momentum"] += cfg.regime_bull_delta_momentum
                w["value"] += cfg.regime_bull_delta_value; w["quality"] += cfg.regime_bull_delta_quality
            elif regime == "BEAR":
                w["small_cap"] += cfg.regime_bear_delta_small_cap; w["momentum"] += cfg.regime_bear_delta_momentum
                w["value"] += cfg.regime_bear_delta_value; w["quality"] += cfg.regime_bear_delta_quality
        w = {k: max(0, v) for k, v in w.items()}; total = sum(w.values()) or 1.0
        return {k: v / total for k, v in w.items()}
    def _score_candidates(self, candidates: list[EquityCandidateInfo], context: StrategyContext) -> list[str]:
        cfg = self.config; w = self._get_regime_weights(context)
        ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        factors = {"small_cap": compute_size_factor(candidates), "momentum": compute_momentum_factor(ph, candidates), "turnover": compute_turnover_factor(candidates), "volatility": compute_volatility_factor(ph, candidates), "quality": compute_quality_factor(candidates), "value": compute_value_factor(candidates)}
        composite = {c.code: sum(w.get(f, 0) * factors.get(f, {}).get(c.code, 0.0) for f in w) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
