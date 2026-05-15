"""V23 风格均衡策略（2×2 Fama-French象限）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_ep_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor, compute_earnings_growth_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

QUADRANT_NAMES = ["large_value", "large_growth", "small_value", "small_growth"]

@dataclass
class V23SbalConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 20; min_mv: float = 50_000.0; max_mv: float = 10_000_000.0
    quadrant_stocks: int = 8; weight_ep: float = 0.25; weight_growth: float = 0.25
    weight_roe: float = 0.20; weight_low_vol: float = 0.15; weight_momentum: float = 0.15
    mv_split_percentile: float = 50.0; mdd_gradient: bool = True; drawdown_timing_enabled: bool = True; max_industry_pct: float = 0.15; min_quadrant_stocks: int = 3; dd_level1_threshold: float = 0.10; dd_level2_threshold: float = 0.20; dd_restore_threshold: float = 0.05; dd_level1_scale: float = 0.70; dd_level2_scale: float = 0.50
    max_pb: float = 15.0; min_roe: float = 0.0

class V23SbalStrategy(EquityRotationStrategy):
    name = "v23_sbal"
    def __init__(self, config: V23SbalConfig | None = None, **kw: object):
        super().__init__(config=config or V23SbalConfig(), **kw)
        self._nav_high: float = 0.0
        self._timing_scale: float = 1.0
    @property
    def config(self) -> V23SbalConfig: return self._config  # type: ignore
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
            ctx.metadata["position_scale"] = 1.0
            return
        dd = (self._nav_high - nav) / self._nav_high if self._nav_high > 0 else 0.0
        if dd >= cfg.dd_level2_threshold:
            self._timing_scale = cfg.dd_level2_scale
        elif dd >= cfg.dd_level1_threshold:
            self._timing_scale = cfg.dd_level1_scale
        elif dd <= cfg.dd_restore_threshold:
            self._timing_scale = 1.0
        # else: hysteresis zone — keep current scale
        ctx.metadata["position_scale"] = self._timing_scale
    def _extra_filter(self, c: "EquityCandidateInfo", ctx: StrategyContext) -> bool:
        cfg = self.config
        pb = getattr(c, "pb", None)
        if pb is None:
            pb = (getattr(c, "metadata", {}) or {}).get("pb")
        if pb is not None and pb > cfg.max_pb:
            return False
        roe = getattr(c, "roe", None)
        if roe is None:
            roe = (getattr(c, "metadata", {}) or {}).get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False
        return True
    def _classify_quadrants(self, candidates: list[EquityCandidateInfo]) -> dict[str, list[EquityCandidateInfo]]:
        mvs = sorted(getattr(c, "circ_mv", 0) or 0 for c in candidates)
        median_mv = mvs[len(mvs) // 2] if mvs else 0
        bps = {c.code: getattr(c, "pb", 1) or 1 for c in candidates}
        median_bp = sorted(bps.values())[len(bps) // 2] if bps else 1
        quads: dict[str, list[EquityCandidateInfo]] = {q: [] for q in QUADRANT_NAMES}
        for c in candidates:
            mv = getattr(c, "circ_mv", 0) or 0; bp = bps.get(c.code, 1)
            is_large = mv >= median_mv; is_value = bp <= median_bp
            if is_large and is_value: quads["large_value"].append(c)
            elif is_large: quads["large_growth"].append(c)
            elif is_value: quads["small_value"].append(c)
            else: quads["small_growth"].append(c)
        return quads
    def _score_within_quadrant(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        ep = compute_ep_factor(candidates); grw = compute_earnings_growth_factor(candidates)
        roe = compute_quality_factor(candidates); vol = compute_volatility_factor(ph, candidates)
        mom = compute_momentum_factor(ph, candidates)
        composite = {c.code: cfg.weight_ep * ep.get(c.code, 0) + cfg.weight_growth * grw.get(c.code, 0) + cfg.weight_roe * roe.get(c.code, 0) + cfg.weight_low_vol * vol.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; quads = self._classify_quadrants(candidates)
        result: list[str] = []
        for qname in QUADRANT_NAMES:
            group = quads[qname]
            if not group: continue
            ranked = self._score_within_quadrant(group, ctx)
            result.extend(ranked[:cfg.quadrant_stocks])
        return result[:cfg.top_k]
