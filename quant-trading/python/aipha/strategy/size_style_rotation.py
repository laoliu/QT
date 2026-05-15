"""大小盘风格轮动策略"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_value_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor, compute_size_factor,
    compute_earnings_growth_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class SizeStyleRotationConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 20; style_lookback: int = 60
    # Large-cap weights
    large_weight_value: float = 0.40; large_weight_quality: float = 0.35; large_weight_lowvol: float = 0.25
    # Small-cap weights
    small_weight_size: float = 0.40; small_weight_momentum: float = 0.35; small_weight_growth: float = 0.25

class SizeStyleRotationStrategy(EquityRotationStrategy):
    name = "size_style_rotation"
    def __init__(self, config: SizeStyleRotationConfig | None = None, **kw: object):
        super().__init__(config=config or SizeStyleRotationConfig(), **kw)
        self._style_mode: str = "SMALL"; self._index_history: deque[float] = deque(maxlen=200)
    @property
    def config(self) -> SizeStyleRotationConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        lg = getattr(context, "large_cap_index", None); sm = getattr(context, "small_cap_index", None)
        if lg and sm:
            self._index_history.append(sm / lg if lg > 0 else 1.0)
            if len(self._index_history) >= self.config.style_lookback:
                recent = list(self._index_history)[-self.config.style_lookback:]
                self._style_mode = "SMALL" if recent[-1] > recent[0] else "LARGE"
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        if self._style_mode == "LARGE":
            val = compute_value_factor(candidates); qual = compute_quality_factor(candidates)
            vol = compute_volatility_factor(ph, candidates)
            composite = {c.code: cfg.large_weight_value * val.get(c.code, 0) + cfg.large_weight_quality * qual.get(c.code, 0) + cfg.large_weight_lowvol * vol.get(c.code, 0) for c in candidates}
        else:
            sz = compute_size_factor(candidates); mom = compute_momentum_factor(ph, candidates)
            grw = compute_earnings_growth_factor(candidates)
            composite = {c.code: cfg.small_weight_size * sz.get(c.code, 0) + cfg.small_weight_momentum * mom.get(c.code, 0) + cfg.small_weight_growth * grw.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
