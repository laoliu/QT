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
    top_k: int = 20; rebalance_days: int = 20; min_mv: float = 50_000.0; max_mv: float = 1_500_000.0
    weight_size: float = 0.25; weight_momentum: float = 0.25; weight_ep: float = 0.15
    weight_profit_growth: float = 0.15; weight_roe: float = 0.10; weight_low_vol: float = 0.10
    momentum_lookback: int = 60; vol_lookback: int = 60; drawdown_timing_enabled: bool = False; dd_reduce_threshold: float = 0.20; dd_restore_threshold: float = 0.10; dd_min_scale: float = 0.50; max_industry_pct: float = 0.20; max_pb: float = 8.0

class V22BmfStrategy(EquityRotationStrategy):
    name = "v22_bmf"
    def __init__(self, config: V22BmfConfig | None = None, **kw: object):
        super().__init__(config=config or V22BmfConfig(), **kw)
        self._nav_high: float = 0.0
        self._timing_scale: float = 1.0
    @property
    def config(self) -> V22BmfConfig: return self._config  # type: ignore
    def on_start(self) -> None:
        super().on_start()
        self._nav_high = 0.0
        self._timing_scale = 1.0
    def _on_bar_update_state(self, bars: dict, ctx: StrategyContext) -> None:
        nav = ctx.nav or 0.0
        if nav > self._nav_high:
            self._nav_high = nav
        cfg = self.config
        if not cfg.drawdown_timing_enabled:
            self._timing_scale = 1.0
            ctx.metadata["position_scale"] = 1.0
            return
        dd = (self._nav_high - nav) / self._nav_high if self._nav_high > 0 else 0.0
        if dd >= cfg.dd_reduce_threshold:
            self._timing_scale = cfg.dd_min_scale
        elif dd <= cfg.dd_restore_threshold:
            self._timing_scale = 1.0
        # else: hysteresis zone — keep current scale
        ctx.metadata["position_scale"] = self._timing_scale
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        pb = getattr(c, "pb", None)
        if pb is None:
            pb = (getattr(c, "metadata", {}) or {}).get("pb")
        if pb is not None and pb > cfg.max_pb:
            return False
        pe = getattr(c, "pe_ttm", None)
        if pe is None:
            pe = (getattr(c, "metadata", {}) or {}).get("pe_ttm")
        if pe is not None and pe <= 0:
            return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        sz = compute_size_factor(candidates); mom = compute_momentum_factor(ph, candidates)
        ep = compute_ep_factor(candidates); grw = compute_earnings_growth_factor(candidates)
        roe = compute_quality_factor(candidates)
        vol = compute_volatility_factor(ph, candidates, lookback=cfg.vol_lookback)
        composite = {c.code: cfg.weight_size * sz.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) + cfg.weight_ep * ep.get(c.code, 0) + cfg.weight_profit_growth * grw.get(c.code, 0) + cfg.weight_roe * roe.get(c.code, 0) + cfg.weight_low_vol * vol.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
