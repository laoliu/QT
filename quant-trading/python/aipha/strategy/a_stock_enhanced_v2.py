"""A股增强V2策略（8因子含另类数据）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import (compute_ep_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class AStockEnhancedV2Config(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 20
    weight_ep: float = 0.15; weight_roe: float = 0.15; weight_mom: float = 0.10
    weight_ivol: float = 0.10; weight_str: float = 0.15; weight_analyst: float = 0.15
    weight_holder: float = 0.10; weight_forecast: float = 0.10
    momentum_lookback: int = 60; ivol_lookback: int = 20; str_lookback: int = 5

class AStockEnhancedV2Strategy(EquityRotationStrategy):
    name = "a_stock_enhanced_v2"
    def __init__(self, config: AStockEnhancedV2Config | None = None, **kw: object):
        super().__init__(config=config or AStockEnhancedV2Config(), **kw)
    @property
    def config(self) -> AStockEnhancedV2Config: return self._config  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        ep = compute_ep_factor(candidates); roe = compute_quality_factor(candidates)
        mom = compute_momentum_factor(ph, candidates); ivol = compute_volatility_factor(ph, candidates, lookback=cfg.ivol_lookback)
        # 另类因子直接从候选人属性读取
        str_s = {c.code: getattr(c, "str_factor", 0.0) or 0.0 for c in candidates}
        analyst = {c.code: getattr(c, "analyst_score", 0.0) or 0.0 for c in candidates}
        holder = {c.code: getattr(c, "holder_change", 0.0) or 0.0 for c in candidates}
        forecast = {c.code: getattr(c, "forecast_score", 0.0) or 0.0 for c in candidates}
        # normalize alt factors
        for d in (str_s, analyst, holder, forecast):
            vals = list(d.values())
            if not vals: continue
            lo, hi = min(vals), max(vals); rng = hi - lo if hi > lo else 1.0
            for k in d: d[k] = (d[k] - lo) / rng
        composite = {c.code: cfg.weight_ep * ep.get(c.code, 0) + cfg.weight_roe * roe.get(c.code, 0) + cfg.weight_mom * mom.get(c.code, 0) + cfg.weight_ivol * ivol.get(c.code, 0) + cfg.weight_str * str_s.get(c.code, 0) + cfg.weight_analyst * analyst.get(c.code, 0) + cfg.weight_holder * holder.get(c.code, 0) + cfg.weight_forecast * forecast.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
