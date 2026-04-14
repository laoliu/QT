"""量价突破策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_momentum_factor, compute_quality_factor, compute_value_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class VolumePriceBreakoutConfig(EquityRotationConfig):
    top_k: int = 25; rebalance_days: int = 10; max_pe: float = 60.0
    weight_momentum: float = 0.35; weight_volume: float = 0.25; weight_quality: float = 0.20
    weight_value: float = 0.20; momentum_lookback: int = 60; volume_short_window: int = 5
    volume_long_window: int = 60; min_volume_ratio: float = 1.2

def _compute_volume_factor(candidates: list[EquityCandidateInfo]) -> dict[str, float]:
    raw = {c.code: getattr(c, "volume_ratio", None) or (getattr(c, "volume", 0) or 0) for c in candidates}
    if not raw: return {}
    lo, hi = min(raw.values()), max(raw.values())
    rng = hi - lo if hi > lo else 1.0
    return {k: (v - lo) / rng for k, v in raw.items()}

class VolumePriceBreakoutStrategy(EquityRotationStrategy):
    name = "volume_price_breakout"
    def __init__(self, config: VolumePriceBreakoutConfig | None = None, **kw: object):
        super().__init__(config=config or VolumePriceBreakoutConfig(), **kw)
    @property
    def config(self) -> VolumePriceBreakoutConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > self.config.max_pe: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        mom = compute_momentum_factor(ph, candidates); vol = _compute_volume_factor(candidates)
        qual = compute_quality_factor(candidates); val = compute_value_factor(candidates)
        composite = {c.code: cfg.weight_momentum * mom.get(c.code, 0) + cfg.weight_volume * vol.get(c.code, 0) + cfg.weight_quality * qual.get(c.code, 0) + cfg.weight_value * val.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
