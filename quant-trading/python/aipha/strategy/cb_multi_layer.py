"""可转债多层复合策略（正股基本面+条款+Gamma）"""
from __future__ import annotations
import logging, math
from collections import deque
from dataclasses import dataclass
from typing import Any
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
from aipha.factors.cb_delta_zone import compute_gamma_proxy
logger = logging.getLogger(__name__)

@dataclass
class MultiLayerCBConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5; max_price: float = 140.0
    max_issue_size: float = float("inf"); min_turnover: float = 20.0; min_days_to_maturity: int = 30
    stop_loss_pct: float = 0.22; lot_size: int = 10; use_age_filter: bool = True; min_age_days: int = 10
    max_premium: float = 70.0
    weight_price: float = 0.15; weight_premium: float = 0.12; weight_stock_roe: float = 0.15
    weight_stock_ep: float = 0.10; weight_stock_vol: float = 0.08; weight_gamma: float = 0.08
    weight_putback: float = 0.15; weight_maturity: float = 0.10; weight_issue_size: float = 0.07
    vol_lookback: int = 60; use_stock_bridge: bool = True; default_roe: float = 0.0
    default_ep: float = 0.0; putback_trigger_ratio: float = 0.70

class CBMultiLayerStrategy(CBRotationStrategy):
    name = "cb_multi_layer"
    def __init__(self, config: MultiLayerCBConfig | None = None, stock_bridge: Any | None = None, **kw: object):
        super().__init__(config=config or MultiLayerCBConfig(), **kw)
        self._stock_bridge = stock_bridge
        self._stk_price_history: dict[str, deque[float]] = {}
        self._current_fundamentals: dict[str, Any] = {}

    @property
    def config(self) -> MultiLayerCBConfig: return self._config  # type: ignore

    @property
    def _bridge(self) -> Any | None:
        return self._stock_bridge

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        for sym, bar in bars.items():
            meta = getattr(bar, "metadata", {}) or {}
            stk = meta.get("stk_code") or getattr(bar, "stock_code", None)
            sc = meta.get("cb_value") or getattr(bar, "stock_close", None)
            if stk and sc:
                if stk not in self._stk_price_history:
                    self._stk_price_history[stk] = deque(maxlen=self.config.vol_lookback + 5)
                self._stk_price_history[stk].append(sc)
                if self._stock_bridge and self.config.use_stock_bridge:
                    try:
                        fund = self._stock_bridge.get_fundamentals(stk)
                        if fund:
                            self._current_fundamentals[stk] = fund
                    except Exception:
                        pass

    def _compute_stk_vol(self, stk_code: str) -> float | None:
        h = self._stk_price_history.get(stk_code)
        if not h or len(h) < 10:
            return None
        prices = list(h)
        rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(rets) < 2:
            return None
        mu = sum(rets) / len(rets)
        var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var * 252) if var > 0 else 0.0

    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem = getattr(c, "premium_rate", None)
            if prem is not None:
                prem = prem * 100.0
        if prem is not None and prem > self.config.max_premium:
            return False
        if self.config.use_age_filter and self.config.min_age_days > 0:
            list_date = getattr(c, "list_date", None)
            if list_date and ctx.current_time:
                try:
                    from datetime import datetime as _dt
                    if isinstance(list_date, str):
                        ld = _dt.strptime(list_date, "%Y%m%d")
                    else:
                        ld = list_date
                    age_days = (ctx.current_time - ld).days
                    if age_days < self.config.min_age_days:
                        return False
                except Exception:
                    pass
        return True

    def _compute_maturity_catalyst(self, c: CandidateInfo, ctx: StrategyContext) -> float:
        """Maturity catalyst: bell curve peaking at 1-3 years."""
        mat_str = getattr(c, "maturity_date", None)
        if not mat_str:
            return 0.0  # no maturity → lowest score
        try:
            from datetime import datetime as _dt
            if isinstance(mat_str, str):
                mat = _dt.strptime(mat_str, "%Y%m%d")
            else:
                mat = mat_str
            days = (mat - (ctx.current_time or _dt.now())).days
            years = days / 365.0
            if years <= 0:
                return 0.0
            # Peak at 2 years, gaussian-like
            peak = 2.0
            sigma = 1.5
            return math.exp(-0.5 * ((years - peak) / sigma) ** 2)
        except Exception:
            return 0.0

    def _compute_putback_score(self, c: CandidateInfo) -> float:
        """Putback proximity: higher when cb_value/100 is close to putback trigger ratio."""
        cb_val = getattr(c, "cb_value", None) or getattr(c, "close", 100.0)
        parity = cb_val / 100.0
        trigger = self.config.putback_trigger_ratio
        # Lower parity → closer to trigger → higher score
        return -(abs(parity - trigger))

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        codes = [c.code for c in candidates]

        # Factor 1: Price (lower is better)
        price_vals = [c.close or 200.0 for c in candidates]
        r_price = _rank_scores(price_vals, ascending=False)

        # Factor 2: Premium (lower is better)
        prem_vals = [getattr(c, "premium_rate", 0.5) or 0.5 for c in candidates]
        r_prem = _rank_scores(prem_vals, ascending=False)

        # Factor 3: ROE (higher is better) - from fundamentals
        roe_vals = []
        for c in candidates:
            stk = (getattr(c, "metadata", {}) or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk)
            roe = getattr(fund, "roe", cfg.default_roe) if fund else cfg.default_roe
            roe_vals.append(roe)
        r_roe = _rank_scores(roe_vals, ascending=True)

        # Factor 4: EP (higher is better) - from fundamentals
        ep_vals = []
        for c in candidates:
            stk = (getattr(c, "metadata", {}) or {}).get("stk_code", "")
            fund = self._current_fundamentals.get(stk)
            ep = getattr(fund, "ep", cfg.default_ep) if fund else cfg.default_ep
            ep_vals.append(ep)
        r_ep = _rank_scores(ep_vals, ascending=True)

        # Factor 5: Stock volatility (lower is better)
        vol_vals = []
        for c in candidates:
            stk = (getattr(c, "metadata", {}) or {}).get("stk_code", "")
            v = self._compute_stk_vol(stk)
            vol_vals.append(v if v is not None else 99.0)
        r_vol = _rank_scores(vol_vals, ascending=False)

        # Factor 6: Gamma (higher is better)
        gamma_vals = [compute_gamma_proxy(getattr(c, "premium_rate", None), getattr(c, "close", None)) for c in candidates]
        r_gamma = _rank_scores(gamma_vals, ascending=True)

        # Factor 7: Putback proximity (higher is better)
        putback_vals = [self._compute_putback_score(c) for c in candidates]
        r_putback = _rank_scores(putback_vals, ascending=True)

        # Factor 8: Maturity catalyst (higher is better, peaks at 1-3y)
        mat_vals = [self._compute_maturity_catalyst(c, ctx) for c in candidates]
        r_mat = _rank_scores(mat_vals, ascending=True)

        # Factor 9: Issue size (smaller is better)
        size_vals = [getattr(c, "issue_size", 1e9) or 1e9 for c in candidates]
        r_size = _rank_scores(size_vals, ascending=False)

        # Composite score
        composite = {}
        for i, c in enumerate(candidates):
            composite[c.code] = (
                cfg.weight_price * r_price[i] +
                cfg.weight_premium * r_prem[i] +
                cfg.weight_stock_roe * r_roe[i] +
                cfg.weight_stock_ep * r_ep[i] +
                cfg.weight_stock_vol * r_vol[i] +
                cfg.weight_gamma * r_gamma[i] +
                cfg.weight_putback * r_putback[i] +
                cfg.weight_maturity * r_mat[i] +
                cfg.weight_issue_size * r_size[i]
            )
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
