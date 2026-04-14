"""可转债正股质量V2（含现金流+盈利稳定性+成长）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import Any
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class StockQualityV2Config(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 5
    weight_price: float = 0.15; weight_premium: float = 0.15; weight_stock_roe: float = 0.15
    weight_stock_ep: float = 0.10; weight_stock_vol: float = 0.10; weight_cash_flow: float = 0.15
    weight_earnings_stability: float = 0.10; weight_growth: float = 0.10
    vol_lookback: int = 60; use_stock_bridge: bool = True; use_age_filter: bool = True
    min_age_days: int = 3; max_premium: float = 80.0
    default_roe: float = 0.0; default_ep: float = 0.0
    default_ocf_ni_ratio: float = 1.0; default_roe_std: float = 0.05; default_revenue_growth: float = 0.0

class CBStockQualityV2Strategy(CBRotationStrategy):
    name = "cb_stock_quality_v2"
    def __init__(self, config: StockQualityV2Config | None = None, stock_bridge: Any | None = None, **kw: object):
        super().__init__(config=config or StockQualityV2Config(), **kw)
        self._stock_bridge = stock_bridge
    @property
    def config(self) -> StockQualityV2Config: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > self.config.max_premium: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        price_s = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem_s = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        roe_s = {c.code: getattr(c, "stock_roe", cfg.default_roe) or cfg.default_roe for c in candidates}
        ep_s = {c.code: getattr(c, "stock_ep", cfg.default_ep) or cfg.default_ep for c in candidates}
        vol_s = {c.code: -(getattr(c, "stock_vol", 99.0) or 99.0) for c in candidates}
        cf_s = {c.code: getattr(c, "stock_ocf_ni_ratio", cfg.default_ocf_ni_ratio) or cfg.default_ocf_ni_ratio for c in candidates}
        stab_s = {c.code: -(getattr(c, "stock_roe_std", cfg.default_roe_std) or cfg.default_roe_std) for c in candidates}
        grow_s = {c.code: getattr(c, "stock_revenue_growth", cfg.default_revenue_growth) or cfg.default_revenue_growth for c in candidates}
        w = {"price": cfg.weight_price, "premium": cfg.weight_premium, "roe": cfg.weight_stock_roe, "ep": cfg.weight_stock_ep, "vol": cfg.weight_stock_vol, "cf": cfg.weight_cash_flow, "stab": cfg.weight_earnings_stability, "grow": cfg.weight_growth}
        ranked = {n: _rank_scores(v) for n, v in [("price", price_s), ("premium", prem_s), ("roe", roe_s), ("ep", ep_s), ("vol", vol_s), ("cf", cf_s), ("stab", stab_s), ("grow", grow_s)]}
        composite = {c.code: sum(w[n] * ranked[n].get(c.code, 0) for n in w) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
