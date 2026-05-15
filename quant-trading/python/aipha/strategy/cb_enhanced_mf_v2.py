"""可转债增强多因子V2"""
from __future__ import annotations
import logging, math
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
from aipha.strategy.cb_enhanced_factors import rating_score, delta_exposure, enhanced_ytm
from aipha.factors.cb_delta_zone import compute_gamma_proxy
logger = logging.getLogger(__name__)

@dataclass
class EnhancedMFV2Config(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 3
    price_weight: float = 0.10; premium_weight: float = 0.10; cb_value_weight: float = 0.08
    size_weight: float = 0.10; delta_weight: float = 0.08; rating_weight: float = 0.06
    ytm_weight: float = 0.06; iv_bias_weight: float = 0.10; gamma_convexity_weight: float = 0.10
    credit_spread_weight: float = 0.08; liquidity_activity_weight: float = 0.06
    maturity_convexity_weight: float = 0.08
    max_price: float = 130.0; max_premium: float = 40.0; min_turnover: float = 50.0
    min_days_to_maturity: int = 30; exclude_st: bool = True; stop_loss_pct: float = 0.15
    buffer_k: int = 0; risk_free_rate: float = 0.02; maturity_sweet_spot: float = 3.0
    maturity_half_range: float = 3.0; lot_size: int = 10

def _maturity_convexity(remain_years: float, sweet: float = 3.0, half_range: float = 3.0) -> float:
    return max(0.0, 1.0 - abs(remain_years - sweet) / half_range)

class CBEnhancedMultifactorV2Strategy(CBRotationStrategy):
    name = "cb_enhanced_mf_v2"
    def __init__(self, config: EnhancedMFV2Config | None = None, **kw: object):
        super().__init__(config=config or EnhancedMFV2Config(), **kw)
    @property
    def config(self) -> EnhancedMFV2Config: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        p = getattr(c, "cb_over_rate", None)
        if p is None:
            p = getattr(c, "premium_rate", None)
            if p is not None: p = p * 100.0
        if p is None: return False
        if p > cfg.max_premium: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        price_s = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem_s = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        cbv = {c.code: getattr(c, "cb_value", 0.0) or 0.0 for c in candidates}
        size = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        delta = {c.code: delta_exposure(getattr(c, "premium_rate", None), getattr(c, "close", None), getattr(c, "stock_close", None)) for c in candidates}
        rat = {c.code: rating_score(getattr(c, "rating", "")) for c in candidates}
        ytm = {c.code: enhanced_ytm(getattr(c, "close", 100.0) or 100.0, getattr(c, "remain_years", 1.0) or 1.0) for c in candidates}
        gamma = {c.code: compute_gamma_proxy(getattr(c, "premium_rate", None), getattr(c, "close", None)) for c in candidates}
        vol = {c.code: getattr(c, "volume", 0.0) or 0.0 for c in candidates}
        mat = {c.code: _maturity_convexity(getattr(c, "remain_years", 6.0) or 6.0, cfg.maturity_sweet_spot, cfg.maturity_half_range) for c in candidates}
        factors = {"price": price_s, "premium": prem_s, "cb_value": cbv, "size": size, "delta": delta, "rating": rat, "ytm": ytm, "gamma_convexity": gamma, "liquidity_activity": vol, "maturity_convexity": mat}
        weights = {"price": cfg.price_weight, "premium": cfg.premium_weight, "cb_value": cfg.cb_value_weight, "size": cfg.size_weight, "delta": cfg.delta_weight, "rating": cfg.rating_weight, "ytm": cfg.ytm_weight, "gamma_convexity": cfg.gamma_convexity_weight, "liquidity_activity": cfg.liquidity_activity_weight, "maturity_convexity": cfg.maturity_convexity_weight}
        ranked = {name: _rank_scores(vals) for name, vals in factors.items()}
        composite: dict[str, float] = {}
        for c in candidates:
            s = sum(weights.get(name, 0) * ranked[name].get(c.code, 0) for name in ranked)
            composite[c.code] = s
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
