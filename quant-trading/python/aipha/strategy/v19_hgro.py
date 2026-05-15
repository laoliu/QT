"""V19 高成长小盘策略"""
from __future__ import annotations
import logging
import math
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_ep_factor, compute_value_factor,
    compute_momentum_factor, compute_earnings_growth_factor, compute_size_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class V19HgroConfig(EquityRotationConfig):
    top_k: int = 15; rebalance_days: int = 20; min_mv: float = 20_000.0; max_mv: float = 300_000.0
    weight_small_size: float = 0.40; weight_momentum: float = 0.25; weight_growth: float = 0.20
    weight_ep: float = 0.10; weight_bp: float = 0.05
    momentum_lookback: int = 240; vol_scaling: bool = True; vol_lookback: int = 20
    mdd_gradient: bool = True; mdd_window: int = 60; vol_scaling_enabled: bool = False; vol_target: float = 0.20; momentum_skip_recent: int = 20; max_pb: float = 5.0

class V19HgroStrategy(EquityRotationStrategy):
    name = "v19_hgro"
    def __init__(self, config: V19HgroConfig | None = None, **kw: object):
        super().__init__(config=config or V19HgroConfig(), **kw)
        self._vol_scale: float = 1.0
        self._mdd_scale: float = 1.0
    @property
    def config(self) -> V19HgroConfig: return self._config  # type: ignore
    def on_start(self) -> None:
        super().on_start()
        self._vol_scale = 1.0
        self._mdd_scale = 1.0
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        mv = getattr(c, "circ_mv", None)
        if mv is not None and (mv < cfg.min_mv or mv > cfg.max_mv): return False
        pb = c.metadata.get("pb") if hasattr(c, "metadata") else None
        if pb is not None and pb > cfg.max_pb: return False
        pe = c.metadata.get("pe_ttm") if hasattr(c, "metadata") else None
        if pe is not None and pe <= 0: return False
        return True
    def _on_bar_update_state(self, bars: dict, ctx: StrategyContext) -> None:
        cfg = self.config
        if not cfg.vol_scaling_enabled:
            return
        nav_history = getattr(ctx, "nav_history", None)
        if not nav_history or len(nav_history) < 2:
            return
        # Compute realized volatility from NAV returns
        window = min(cfg.vol_lookback, len(nav_history) - 1)
        if window < 2:
            return
        navs = nav_history[-window - 1:]
        rets = [(navs[i] / navs[i - 1] - 1) for i in range(1, len(navs)) if navs[i - 1] > 0]
        if not rets:
            return
        mean_ret = sum(rets) / len(rets)
        var = sum((r - mean_ret) ** 2 for r in rets) / len(rets)
        realized_vol = math.sqrt(var) * math.sqrt(252)
        if realized_vol > 0:
            self._vol_scale = cfg.vol_target / realized_vol
        else:
            self._vol_scale = 1.0
        # Inject position_scale into context metadata
        if not hasattr(ctx, "metadata") or ctx.metadata is None:
            ctx.metadata = {}
        ctx.metadata["position_scale"] = min(self._vol_scale, self._mdd_scale)
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        if not candidates:
            return []
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        sz = compute_size_factor(candidates); mom = compute_momentum_factor(ph, candidates)
        grw = compute_earnings_growth_factor(candidates)
        ep = compute_ep_factor(candidates); bp = compute_value_factor(candidates)
        composite = {c.code: cfg.weight_small_size * sz.get(c.code, 0) + cfg.weight_momentum * mom.get(c.code, 0) + cfg.weight_growth * grw.get(c.code, 0) + cfg.weight_ep * ep.get(c.code, 0) + cfg.weight_bp * bp.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
