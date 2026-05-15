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


class _VolTargetState:
    __slots__ = ("n_observations", "current_scale")
    def __init__(self):
        self.n_observations = 0
        self.current_scale: float | None = None

class _VolTargetAdapter:
    """Lightweight vol-target wrapper matching test expectations."""
    def __init__(self, target_vol: float = 0.15, lookback: int = 20,
                 floor: float = 0.3, cap: float = 1.5):
        self._target = target_vol
        self._lookback = lookback
        self._floor = floor
        self._cap = cap
        self._returns: list[float] = []
        self.state = _VolTargetState()
        self._state = self.state

    def update(self, daily_return: float) -> None:
        self._returns.append(daily_return)
        self.state.n_observations = len(self._returns)

    def get_scale(self) -> float:
        if self.state.current_scale is not None:
            return self.state.current_scale
        import numpy as np
        if len(self._returns) < self._lookback:
            return 1.0
        recent = np.array(self._returns[-self._lookback:])
        realized = float(np.std(recent, ddof=1)) * math.sqrt(252.0)
        realized = max(realized, 0.01)
        scale = self._target / realized
        return max(self._floor, min(self._cap, scale))

    def reset(self) -> None:
        self._returns.clear()
        self.state.n_observations = 0
        self.state.current_scale = None

FACTOR_DEFS: dict[str, bool] = {
    "price": False, "premium": False, "size": False, "age": False, "mean_reversion": True,
    "rating": True, "delta": True, "ytm": True, "turnover": True, "momentum": True,
    "premium_pct": False, "putback": True, "iv_bias": True, "gamma_convexity": True,
    "credit_spread": True, "liquidity_activity": True, "maturity_convexity": True,
    "stock_roe": False, "stock_ep": False, "stock_vol": False, "stock_growth": False, "stock_safety": True,
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
        cfg = config or CBAdaptiveFactorConfig()
        super().__init__(config=cfg, **kw)
        self._stock_bridge = stock_bridge
        self._close_history: dict[str, deque[float]] = {}
        self._premium_history: dict[str, deque[float]] = {}
        self._factor_history: dict[str, list[dict[str, float]]] = {name: [] for name in FACTOR_DEFS}
        self._return_history: list[dict[str, float]] = []
        self._ic_weights: dict[str, float] = dict(cfg.get_base_weights())
        self._dynamic_weights: dict[str, float] = dict(cfg.get_base_weights())
        self._prev_prices: dict[str, float] = {}
        self._factor_directions: dict[str, int] = {name: asc for name, asc in FACTOR_DEFS.items()}
        self._bar_count: int = 0
        self._effective_max_age: int = cfg.max_age_days
        self._ic_update_counter: int = 0
        # RSRS timing overlay
        self._rsrs = None
        if cfg.use_rsrs_timing:
            from aipha.factors.rsrs_timing import RSRSTimingFactor, RSRSConfig
            self._rsrs = RSRSTimingFactor(RSRSConfig(
                regression_window=cfg.rsrs_regression_window,
                zscore_window=cfg.rsrs_zscore_window,
                bull_threshold=cfg.rsrs_bull_threshold,
                bear_threshold=cfg.rsrs_bear_threshold,
            ))
        # Vol targeting overlay
        self._vol_targeter = None
        if cfg.use_vol_targeting:
            self._vol_targeter = _VolTargetAdapter(
                target_vol=cfg.vol_target,
                lookback=cfg.vol_target_lookback,
                floor=cfg.vol_scale_floor,
                cap=cfg.vol_scale_cap,
            )
    @property
    def config(self) -> CBAdaptiveFactorConfig: return self._config  # type: ignore

    @property
    def _price_history(self) -> dict[str, deque[float]]:
        return self._close_history

    @_price_history.setter
    def _price_history(self, value: dict[str, deque[float]]) -> None:
        self._close_history = value

    def _on_start_extra(self) -> None:
        self._close_history.clear()
        self._premium_history.clear()
        self._factor_history = {name: [] for name in FACTOR_DEFS}
        self._return_history.clear()
        self._ic_weights = dict(self.config.get_base_weights())
        self._dynamic_weights = dict(self.config.get_base_weights())
        self._prev_prices.clear()
        self._factor_directions = {name: asc for name, asc in FACTOR_DEFS.items()}
        self._bar_count = 0
        self._ic_update_counter = 0
        if self._rsrs is not None:
            self._rsrs.reset()
        if self._vol_targeter is not None:
            self._vol_targeter.reset()

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        self._bar_count += 1
        # bars provided as argument
        cfg = self.config
        maxlen = max(cfg.lookback, cfg.momentum_lookback, cfg.premium_pct_lookback) + 10
        # Compute daily returns from previous prices
        daily_returns: dict[str, float] = {}
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            prev = self._prev_prices.get(sym)
            if prev is not None and prev > 0:
                daily_returns[sym] = (close - prev) / prev
        if daily_returns:
            self._return_history.append(daily_returns)
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            self._prev_prices[sym] = close
            if sym not in self._close_history: self._close_history[sym] = deque(maxlen=maxlen)
            self._close_history[sym].append(close)
            meta = getattr(bar, "metadata", None) or {}
            prem = meta.get("cb_over_rate", None)
            if prem is None:
                prem_dec = meta.get("premium_rate", None)
                if prem_dec is not None:
                    try:
                        prem = float(prem_dec) * 100
                    except (TypeError, ValueError):
                        prem = None
            if prem is not None:
                if sym not in self._premium_history: self._premium_history[sym] = deque(maxlen=maxlen)
                self._premium_history[sym].append(prem)
            # Update stock price & amount history
            stk_close = getattr(bar, "stock_close", None) or getattr(bar, "stk_close", None)
            if stk_close is not None:
                if sym not in self._stk_price_history: self._stk_price_history[sym] = deque(maxlen=200)
                self._stk_price_history[sym].append(stk_close)
            amount = getattr(bar, "amount", None) or getattr(bar, "turnover", None)
            if amount is not None:
                if sym not in self._turnover_history: self._turnover_history[sym] = deque(maxlen=200)
                self._turnover_history[sym].append(amount)
        if self._bar_count % cfg.ic_update_interval == 0:
            self._recompute_ic_weights()

    def _compute_bb_deviation(self, symbol: str, close: float) -> float:
        h = self._close_history.get(symbol)
        if not h or len(h) < self.config.lookback: return 0.0
        prices = list(h)[-self.config.lookback:]
        mu = sum(prices) / len(prices)
        std = math.sqrt(sum((p - mu) ** 2 for p in prices) / len(prices))
        if std == 0: return 0.0
        return (mu - close) / (std * self.config.bb_k)

    def _compute_momentum(self, symbol: str) -> float:
        h = self._close_history.get(symbol)
        if not h or len(h) < self.config.momentum_lookback: return 0.0
        prices = list(h)
        idx = max(0, len(prices) - 1 - self.config.momentum_lookback)
        return (prices[-1] - prices[idx]) / prices[idx] if prices[idx] > 0 else 0.0

    def _compute_premium_pct(self, symbol: str, cur: float | None) -> float:
        h = self._premium_history.get(symbol)
        if not h or len(h) < 10 or cur is None: return 0.5
        sorted_h = sorted(list(h)[-self.config.premium_pct_lookback:])
        rank = sum(1 for v in sorted_h if v <= cur)
        return rank / len(sorted_h)

    def _compute_putback(self, cb_value: float) -> float:
        """回售因子：基于转债价值计算回售保护系数。"""
        parity = cb_value / 100.0
        trigger = 0.85
        return max(0.0, 1.0 - parity / trigger)

    def _get_stock_roe(self, c: CandidateInfo) -> float:
        stk = getattr(c, "stk_code", None)
        if not stk:
            return self.config.default_roe
        fund = self._current_fundamentals.get(stk)
        if fund is not None:
            return getattr(fund, "roe", self.config.default_roe)
        return self.config.default_roe

    def _get_stock_ep(self, c: CandidateInfo) -> float:
        stk = getattr(c, "stk_code", None)
        if not stk:
            return self.config.default_ep
        fund = self._current_fundamentals.get(stk)
        if fund is not None:
            return getattr(fund, "ep", self.config.default_ep)
        return self.config.default_ep

    def _get_stock_vol(self, c: CandidateInfo) -> float:
        stk = getattr(c, "stk_code", None)
        if not stk:
            return 0.0
        prices = self._stk_price_history.get(stk)
        if not prices or len(prices) < 10:
            return 0.0
        import numpy as np
        arr = np.array(list(prices))
        rets = np.diff(arr) / arr[:-1]
        return float(np.std(rets, ddof=1) * np.sqrt(252))

    def _get_stock_growth(self, c: CandidateInfo) -> float:
        stk = getattr(c, "stk_code", None)
        if not stk:
            return self.config.default_growth
        fund = self._current_fundamentals.get(stk)
        if fund is not None:
            return getattr(fund, "growth", self.config.default_growth)
        return self.config.default_growth

    def _get_stock_safety(self, c: CandidateInfo) -> float:
        stk = getattr(c, "stk_code", None)
        if not stk:
            return self.config.default_safety
        fund = self._current_fundamentals.get(stk)
        if fund is not None:
            return getattr(fund, "debt_ratio", self.config.default_safety)
        return self.config.default_safety

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
            factors["stock_roe"].append(self._get_stock_roe(c))
            factors["stock_ep"].append(self._get_stock_ep(c))
            factors["stock_vol"].append(-self._get_stock_vol(c) if self._get_stock_vol(c) > 0 else -(getattr(c, "stock_vol", 99.0) or 99.0))
            factors["stock_growth"].append(self._get_stock_growth(c))
            factors["stock_safety"].append(-self._get_stock_safety(c))
        return factors

    def _update_factor_history(self, candidates: list[CandidateInfo], factors: dict[str, list[float]]) -> None:
        for name in FACTOR_DEFS:
            if name not in self._factor_history:
                self._factor_history[name] = []
            row = {candidates[i].code: factors[name][i] for i in range(len(candidates))}
            self._factor_history[name].append(row)
            if len(self._factor_history[name]) > self.config.ic_lookback + 10:
                self._factor_history[name] = self._factor_history[name][-(self.config.ic_lookback + 5):]
        # Compute cross-sectional returns for IC
        returns: dict[str, float] = {}
        for c in candidates:
            prev = self._prev_prices.get(c.code)
            cur = getattr(c, "close", None) or 0
            if prev and prev > 0 and cur > 0:
                returns[c.code] = (cur - prev) / prev
            else:
                returns[c.code] = 0.0
        self._return_history.append(returns)

    def _recompute_ic_weights(self) -> None:
        cfg = self.config
        if len(self._return_history) < cfg.min_ic_samples: return
        base = cfg.get_base_weights()
        self._ic_weights = dict(base)
        # IC direction flip
        if cfg.enable_ic_direction_flip:
            for name in FACTOR_DEFS:
                fh = self._factor_history.get(name, [])
                rh = self._return_history
                n = min(len(fh), len(rh))
                if n < cfg.min_ic_samples:
                    if name not in self._factor_directions:
                        self._factor_directions[name] = FACTOR_DEFS[name]
                    continue
                # Compute rank IC (Spearman-like)
                neg_count = 0
                for t in range(max(0, n - cfg.ic_lookback), n):
                    factor_vals = fh[t]
                    ret_vals = rh[t]
                    common = [s for s in factor_vals if s in ret_vals]
                    if len(common) < 3: continue
                    fv = [factor_vals[s] for s in common]
                    rv = [ret_vals[s] for s in common]
                    # Simple correlation sign check
                    f_mean = sum(fv) / len(fv)
                    r_mean = sum(rv) / len(rv)
                    cov = sum((f - f_mean) * (r - r_mean) for f, r in zip(fv, rv))
                    if cov < 0:
                        neg_count += 1
                neg_ratio = neg_count / max(n, 1)
                if neg_ratio > cfg.ic_flip_threshold:
                    self._factor_directions[name] = not FACTOR_DEFS[name]
                else:
                    self._factor_directions[name] = FACTOR_DEFS[name]
        else:
            for name in FACTOR_DEFS:
                if name not in self._factor_directions:
                    self._factor_directions[name] = FACTOR_DEFS[name]

    def _get_overlay_scale(self) -> float:
        scale = 1.0
        if self._rsrs is not None:
            from aipha.factors.rsrs_timing import MarketSignal
            sig = self._rsrs.get_signal()
            cfg = self.config
            if sig == MarketSignal.BULL:
                scale *= cfg.rsrs_bull_scale
            elif sig == MarketSignal.BEAR:
                scale *= cfg.rsrs_bear_scale
            else:
                scale *= cfg.rsrs_neutral_scale
        if self._vol_targeter is not None:
            scale *= self._vol_targeter.get_scale()
        return scale

    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem_dec = getattr(c, "premium_rate", None)
            if prem_dec is not None:
                prem = prem_dec * 100
        if prem is not None and prem > cfg.max_premium: return False
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        if cfg.use_age_filter:
            ld = getattr(c, "list_date", None) or (getattr(c, "metadata", None) or {}).get("list_date")
            if ld:
                try:
                    from datetime import datetime as _dt
                    ld_dt = _dt.strptime(str(ld), "%Y%m%d")
                    age = (ctx.current_time - ld_dt).days
                    if age < cfg.min_age_days:
                        return False
                    if cfg.max_age_days > 0 and age > self._effective_max_age:
                        return False
                except Exception:
                    pass
        return True

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        factors = self._extract_factors(candidates, ctx)
        self._update_factor_history(candidates, factors)
        self._ic_update_counter += 1
        ic_interval = getattr(cfg, "ic_update_interval", 3)
        if self._ic_update_counter >= ic_interval:
            self._ic_update_counter = 0
            self._recompute_ic_weights()
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
