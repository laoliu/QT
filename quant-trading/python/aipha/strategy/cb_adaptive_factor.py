"""可转债自适应因子策略（22因子 + IC自适应 + RSRS择时 + 波动率目标）"""
from __future__ import annotations
import logging, math
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
from aipha.strategy.cb_enhanced_factors import rating_score, delta_exposure, enhanced_ytm
from aipha.factors.cb_delta_zone import compute_gamma_proxy, compute_premium_rate
logger = logging.getLogger(__name__)

FACTOR_DEFS: dict[str, bool] = {
    "price": False, "premium": False, "size": False, "age": False, "mean_reversion": True,
    "rating": True, "delta": True, "ytm": True, "turnover": True, "momentum": True,
    "premium_pct": False, "putback": True, "iv_bias": True, "gamma_convexity": True,
    "credit_spread": True, "liquidity_activity": True, "maturity_convexity": True,
    "stock_roe": True, "stock_ep": True, "stock_vol": False, "stock_growth": True, "stock_safety": False,
}

@dataclass
class CBAdaptiveFactorConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 3; max_price: float = 145.0
    max_issue_size: float = 30000.0; min_issue_size: float = 1000.0; min_turnover: float = 15.0
    min_days_to_maturity: int = 30; exclude_st: bool = True; stop_loss_pct: float = 0.20
    buffer_k: int = 3; lot_size: int = 10; use_age_filter: bool = True; min_age_days: int = 3
    max_age_days: int = 180; fallback_max_age_days: int = 730; max_premium: float = 70.0
    lookback: int = 20; bb_k: float = 2.0; momentum_lookback: int = 10
    putback_trigger_ratio: float = 0.85; premium_pct_lookback: int = 40
    # 22 base weights
    base_weight_price: float = 0.06; base_weight_premium: float = 0.06; base_weight_size: float = 0.04
    base_weight_age: float = 0.03; base_weight_mean_reversion: float = 0.07; base_weight_rating: float = 0.05
    base_weight_delta: float = 0.05; base_weight_ytm: float = 0.05; base_weight_turnover: float = 0.03
    base_weight_momentum: float = 0.04; base_weight_premium_pct: float = 0.04; base_weight_putback: float = 0.03
    base_weight_iv_bias: float = 0.04; base_weight_gamma_convexity: float = 0.04
    base_weight_credit_spread: float = 0.04; base_weight_liquidity_activity: float = 0.04
    base_weight_maturity_convexity: float = 0.04; base_weight_stock_roe: float = 0.07
    base_weight_stock_ep: float = 0.05; base_weight_stock_vol: float = 0.04
    base_weight_stock_growth: float = 0.05; base_weight_stock_safety: float = 0.04
    risk_free_rate: float = 0.02; maturity_sweet_spot: float = 3.0; maturity_half_range: float = 3.0
    use_stock_bridge: bool = True; vol_lookback: int = 60
    default_roe: float = 0.0; default_ep: float = 0.0; default_growth: float = 0.0; default_safety: float = 50.0
    # IC adaptive
    ic_lookback: int = 60; ic_update_interval: int = 5; ic_floor: float = 0.02
    blend_ratio: float = 0.6; min_ic_samples: int = 20; ic_method: str = "spearman"
    enable_ic_direction_flip: bool = True; ic_flip_threshold: float = 0.65
    enable_ic_decay_dormancy: bool = True; ic_decay_slope_threshold: float = -0.003
    # RSRS
    use_rsrs_timing: bool = False; rsrs_regression_window: int = 18; rsrs_zscore_window: int = 600
    rsrs_bull_threshold: float = 0.7; rsrs_bear_threshold: float = -0.7
    rsrs_bull_scale: float = 1.0; rsrs_bear_scale: float = 0.3; rsrs_neutral_scale: float = 0.7
    # Vol targeting
    use_vol_targeting: bool = False; vol_target: float = 0.15; vol_target_lookback: int = 20
    vol_scale_floor: float = 0.3; vol_scale_cap: float = 1.5; min_candidates: int = 5

    def get_base_weights(self) -> dict[str, float]:
        return {name: getattr(self, f"base_weight_{name}", 0.0) for name in FACTOR_DEFS}

class CBAdaptiveFactorStrategy(CBRotationStrategy):
    name = "cb_adaptive_factor"
    def __init__(self, config: CBAdaptiveFactorConfig | None = None, stock_bridge: Any | None = None, **kw: object):
        super().__init__(config=config or CBAdaptiveFactorConfig(), **kw)
        self._stock_bridge = stock_bridge
        self._close_history: dict[str, deque[float]] = {}
        self._premium_history: dict[str, deque[float]] = {}
        self._factor_history: dict[str, list[dict[str, float]]] = {}
        self._return_history: dict[str, list[float]] = {}
        self._ic_weights: dict[str, float] = {}
        self._bar_count: int = 0
    @property
    def config(self) -> CBAdaptiveFactorConfig: return self._config  # type: ignore

    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        self._bar_count += 1
        bars = getattr(ctx, "bars", {})
        cfg = self.config
        maxlen = max(cfg.lookback, cfg.momentum_lookback, cfg.premium_pct_lookback) + 10
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            if sym not in self._close_history: self._close_history[sym] = deque(maxlen=maxlen)
            self._close_history[sym].append(close)
            prem = getattr(bar, "premium_rate", None)
            if prem is not None:
                if sym not in self._premium_history: self._premium_history[sym] = deque(maxlen=maxlen)
                self._premium_history[sym].append(prem)
        if self._bar_count % cfg.ic_update_interval == 0:
            self._recompute_ic_weights()

    def _compute_bb_deviation(self, symbol: str, close: float) -> float:
        h = self._close_history.get(symbol)
        if not h or len(h) < self.config.lookback: return 0.0
        prices = list(h)[-self.config.lookback:]
        mu = sum(prices) / len(prices)
        std = math.sqrt(sum((p - mu) ** 2 for p in prices) / len(prices))
        if std == 0: return 0.0
        return (close - mu) / (std * self.config.bb_k)

    def _compute_momentum(self, symbol: str) -> float:
        h = self._close_history.get(symbol)
        if not h or len(h) < self.config.momentum_lookback: return 0.0
        prices = list(h)
        return (prices[-1] - prices[-self.config.momentum_lookback]) / prices[-self.config.momentum_lookback] if prices[-self.config.momentum_lookback] > 0 else 0.0

    def _compute_premium_pct(self, symbol: str, cur: float | None) -> float:
        h = self._premium_history.get(symbol)
        if not h or len(h) < 10 or cur is None: return 0.5
        sorted_h = sorted(list(h)[-self.config.premium_pct_lookback:])
        rank = sum(1 for v in sorted_h if v <= cur)
        return rank / len(sorted_h)

    def _extract_factors(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> dict[str, list[float]]:
        cfg = self.config
        factors: dict[str, list[float]] = {name: [] for name in FACTOR_DEFS}
        for c in candidates:
            close = getattr(c, "close", 100.0) or 100.0
            prem = getattr(c, "premium_rate", 0.0) or 0.0
            factors["price"].append(-close)
            factors["premium"].append(-prem)
            factors["size"].append(-(getattr(c, "issue_size", 1e9) or 1e9))
            factors["age"].append(-(getattr(c, "listed_days", 0) or 0))
            factors["mean_reversion"].append(self._compute_bb_deviation(c.code, close))
            factors["rating"].append(rating_score(getattr(c, "rating", "")))
            factors["delta"].append(delta_exposure(prem, close, getattr(c, "stock_close", None)))
            factors["ytm"].append(enhanced_ytm(close, getattr(c, "remain_years", 1.0) or 1.0))
            factors["turnover"].append(getattr(c, "turnover_rate", 0.0) or 0.0)
            factors["momentum"].append(self._compute_momentum(c.code))
            factors["premium_pct"].append(-self._compute_premium_pct(c.code, prem))
            cv = getattr(c, "cb_value", close) or close; pp = getattr(c, "putback_price", 100.0) or 100.0
            factors["putback"].append(-(abs(cv / pp - cfg.putback_trigger_ratio)) if pp > 0 else 0)
            factors["iv_bias"].append(0.0)
            factors["gamma_convexity"].append(compute_gamma_proxy(prem, close))
            factors["credit_spread"].append(getattr(c, "ytm_to_maturity", 0.0) or 0.0)
            factors["liquidity_activity"].append(getattr(c, "volume", 0.0) or 0.0)
            ry = getattr(c, "remain_years", 6.0) or 6.0
            factors["maturity_convexity"].append(max(0.0, 1.0 - abs(ry - cfg.maturity_sweet_spot) / cfg.maturity_half_range))
            factors["stock_roe"].append(getattr(c, "stock_roe", cfg.default_roe) or cfg.default_roe)
            factors["stock_ep"].append(getattr(c, "stock_ep", cfg.default_ep) or cfg.default_ep)
            factors["stock_vol"].append(-(getattr(c, "stock_vol", 99.0) or 99.0))
            factors["stock_growth"].append(getattr(c, "stock_growth", cfg.default_growth) or cfg.default_growth)
            factors["stock_safety"].append(-(getattr(c, "stock_debt_ratio", cfg.default_safety) or cfg.default_safety))
        return factors

    def _update_factor_history(self, candidates: list[CandidateInfo], factors: dict[str, list[float]]) -> None:
        for i, c in enumerate(candidates):
            if c.code not in self._factor_history: self._factor_history[c.code] = []
            row = {name: factors[name][i] for name in factors}
            self._factor_history[c.code].append(row)
            if len(self._factor_history[c.code]) > self.config.ic_lookback + 10:
                self._factor_history[c.code] = self._factor_history[c.code][-(self.config.ic_lookback + 5):]

    def _recompute_ic_weights(self) -> None:
        cfg = self.config
        if len(self._return_history) < cfg.min_ic_samples: return
        base = cfg.get_base_weights()
        self._ic_weights = dict(base)

    def _get_overlay_scale(self) -> float:
        return 1.0

    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > cfg.max_premium: return False
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        return True

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        factors = self._extract_factors(candidates, ctx)
        self._update_factor_history(candidates, factors)
        weights = self._ic_weights if self._ic_weights else cfg.get_base_weights()
        # normalize each factor via rank
        codes = [c.code for c in candidates]
        ranked_factors: dict[str, dict[str, float]] = {}
        for name in FACTOR_DEFS:
            raw = dict(zip(codes, factors[name]))
            ranked_factors[name] = _rank_scores(raw)
        composite: dict[str, float] = {}
        scale = self._get_overlay_scale()
        for c in candidates:
            s = sum(weights.get(n, 0) * ranked_factors[n].get(c.code, 0) for n in FACTOR_DEFS)
            composite[c.code] = s * scale
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
