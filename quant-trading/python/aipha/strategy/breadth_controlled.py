"""宽度控制策略（市场广度信号 + 因子选股）"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_value_factor, compute_quality_factor,
    compute_momentum_factor, compute_size_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class BreadthControlledConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 10
    weight_quality: float = 0.25; weight_value: float = 0.25; weight_momentum: float = 0.25
    weight_size: float = 0.25; breadth_bullish: float = 0.60; breadth_bearish: float = 0.40
    bear_scale: float = 0.50; breadth_lookback: int = 5

class BreadthControlledStrategy(EquityRotationStrategy):
    name = "breadth_controlled"
    def __init__(self, config: BreadthControlledConfig | None = None, **kw: object):
        super().__init__(config=config or BreadthControlledConfig(), **kw)
        self._breadth_history: deque[float] = deque(maxlen=100); self._is_bearish: bool = False
    @property
    def config(self) -> BreadthControlledConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        breadth = getattr(context, "market_breadth", None)
        if breadth is not None:
            self._breadth_history.append(breadth)
            recent = list(self._breadth_history)[-self.config.breadth_lookback:]
            avg = sum(recent) / len(recent)
            self._is_bearish = avg < self.config.breadth_bearish
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        val = compute_value_factor(candidates); qual = compute_quality_factor(candidates)
        mom = compute_momentum_factor(ph, candidates); sz = compute_size_factor(candidates)
        scale = cfg.bear_scale if self._is_bearish else 1.0
        effective_k = max(1, int(cfg.top_k * scale))
        composite = {c.code: cfg.weight_value * val.get(c.code, 0) + cfg.weight_quality * qual.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) + cfg.weight_size * sz.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:effective_k]  # type: ignore
