"""趋势+质量管理策略"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_value_factor, compute_quality_factor,
    compute_volatility_factor, compute_earnings_stability_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class TrendQualityManagedConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 15; min_mv: float = 500_000.0
    weight_quality: float = 0.30; weight_stability: float = 0.30; weight_vol: float = 0.20
    weight_value: float = 0.20; ma_short: int = 20; ma_long: int = 60; bear_scale: float = 0.50
    min_roe: float = 5.0; max_pe: float = 60.0

class TrendQualityManagedStrategy(EquityRotationStrategy):
    name = "trend_quality_managed"
    def __init__(self, config: TrendQualityManagedConfig | None = None, **kw: object):
        super().__init__(config=config or TrendQualityManagedConfig(), **kw)
        self._index_history: deque[float] = deque(maxlen=200); self._is_bear: bool = False
    @property
    def config(self) -> TrendQualityManagedConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        idx = getattr(ctx, "index_close", None)
        if idx: self._index_history.append(idx)
        if len(self._index_history) >= self.config.ma_long:
            h = list(self._index_history)
            ma_s = sum(h[-self.config.ma_short:]) / self.config.ma_short
            ma_l = sum(h[-self.config.ma_long:]) / self.config.ma_long
            self._is_bear = ma_s < ma_l
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        roe = getattr(c, "roe", None)
        if roe is not None and roe < cfg.min_roe: return False
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        qual = compute_quality_factor(candidates); stab = compute_earnings_stability_factor(candidates)
        vol = compute_volatility_factor(ph, candidates); val = compute_value_factor(candidates)
        scale = cfg.bear_scale if self._is_bear else 1.0
        effective_k = max(1, int(cfg.top_k * scale))
        composite = {c.code: cfg.weight_quality * qual.get(c.code, 0) + cfg.weight_stability * stab.get(c.code, 0) + cfg.weight_vol * vol.get(c.code, 0) + cfg.weight_value * val.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:effective_k]  # type: ignore
