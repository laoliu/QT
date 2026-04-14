"""可转债定价偏差策略"""
from __future__ import annotations
import logging, math
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
from aipha.strategy.cb_enhanced_factors import rating_score
logger = logging.getLogger(__name__)

DEFAULT_RISK_FREE_RATE: float = 0.02
DEFAULT_VOL_PROXY: float = 0.30

def estimate_bond_floor(years_to_maturity: float, risk_free_rate: float = 0.02, face_value: float = 100.0, coupon_rate: float = 0.003) -> float:
    if years_to_maturity <= 0: return face_value
    pv_coupons = sum(face_value * coupon_rate / (1 + risk_free_rate) ** t for t in range(1, int(years_to_maturity) + 1))
    pv_face = face_value / (1 + risk_free_rate) ** years_to_maturity
    return pv_coupons + pv_face

def estimate_option_time_value(cb_value: float, years_to_maturity: float, vol_proxy: float = 0.30) -> float:
    if years_to_maturity <= 0 or cb_value <= 0: return 0.0
    return cb_value * vol_proxy * math.sqrt(years_to_maturity) * 0.4

def estimate_theoretical_price(cb_value: float, years_to_maturity: float, risk_free_rate: float = 0.02, vol_proxy: float = 0.30) -> float:
    bf = estimate_bond_floor(years_to_maturity, risk_free_rate)
    otv = estimate_option_time_value(cb_value, years_to_maturity, vol_proxy)
    return max(bf, cb_value) + otv

def compute_pricing_bias(market_price: float, cb_value: float, years_to_maturity: float, risk_free_rate: float = 0.02, vol_proxy: float = 0.30) -> float:
    theo = estimate_theoretical_price(cb_value, years_to_maturity, risk_free_rate, vol_proxy)
    return (market_price - theo) / theo if theo > 0 else 0.0

def compute_time_value_ratio(market_price: float, cb_value: float, years_to_maturity: float, vol_proxy: float = 0.30) -> float:
    otv = estimate_option_time_value(cb_value, years_to_maturity, vol_proxy)
    tv = max(market_price - max(cb_value, estimate_bond_floor(years_to_maturity)), 0)
    return tv / otv if otv > 0 else 0.0

@dataclass
class PricingBiasCBConfig(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 5; max_price: float = 160.0
    max_issue_size: float = float("inf"); min_turnover: float = 20.0; min_days_to_maturity: int = 60
    stop_loss_pct: float = 0.20; lot_size: int = 10; use_age_filter: bool = True; min_age_days: int = 3
    max_premium: float = 100.0; risk_free_rate: float = 0.02; vol_proxy: float = 0.30
    weight_pricing_bias: float = 0.30; weight_time_value: float = 0.15; weight_price: float = 0.20
    weight_premium: float = 0.15; weight_rating: float = 0.10; weight_issue_size: float = 0.10

class CBPricingBiasStrategy(CBRotationStrategy):
    name = "cb_pricing_bias"
    def __init__(self, config: PricingBiasCBConfig | None = None, **kw: object):
        super().__init__(config=config or PricingBiasCBConfig(), **kw)
    @property
    def config(self) -> PricingBiasCBConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > cfg.max_premium: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        bias: dict[str, float] = {}; tvr: dict[str, float] = {}
        for c in candidates:
            price = getattr(c, "close", 100.0) or 100.0
            cbv = getattr(c, "cb_value", price) or price
            ry = getattr(c, "remain_years", 1.0) or 1.0
            bias[c.code] = -compute_pricing_bias(price, cbv, ry, cfg.risk_free_rate, cfg.vol_proxy)
            tvr[c.code] = -compute_time_value_ratio(price, cbv, ry, cfg.vol_proxy)
        price_s = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem_s = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        rat_s = {c.code: rating_score(getattr(c, "rating", "")) for c in candidates}
        size_s = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        rb, rt, rp, rpm, rr, rsz = _rank_scores(bias), _rank_scores(tvr), _rank_scores(price_s), _rank_scores(prem_s), _rank_scores(rat_s), _rank_scores(size_s)
        composite = {c.code: cfg.weight_pricing_bias * rb.get(c.code, 0) + cfg.weight_time_value * rt.get(c.code, 0) + cfg.weight_price * rp.get(c.code, 0) + cfg.weight_premium * rpm.get(c.code, 0) + cfg.weight_rating * rr.get(c.code, 0) + cfg.weight_issue_size * rsz.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
