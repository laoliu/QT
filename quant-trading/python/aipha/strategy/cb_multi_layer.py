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
        self._stk_close_hist: dict[str, deque[float]] = {}
    @property
    def config(self) -> MultiLayerCBConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        bars = getattr(ctx, "bars", {})
        for sym, bar in bars.items():
            stk = getattr(bar, "stock_code", None)
            sc = getattr(bar, "stock_close", None)
            if stk and sc:
                if stk not in self._stk_close_hist: self._stk_close_hist[stk] = deque(maxlen=self.config.vol_lookback + 5)
                self._stk_close_hist[stk].append(sc)
    def _compute_stk_vol(self, stk_code: str) -> float:
        h = self._stk_close_hist.get(stk_code)
        if not h or len(h) < 10: return 99.0
        prices = list(h)
        rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(rets) < 2: return 99.0
        mu = sum(rets) / len(rets); var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var * 252) if var > 0 else 0.0
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
        vol_s = {c.code: -self._compute_stk_vol(getattr(c, "stock_code", "") or "") for c in candidates}
        gamma_s = {c.code: compute_gamma_proxy(getattr(c, "premium_rate", None), getattr(c, "close", None)) for c in candidates}
        putback_s: dict[str, float] = {}
        for c in candidates:
            cv = getattr(c, "cb_value", None) or getattr(c, "close", 100.0)
            pp = getattr(c, "putback_price", 100.0) or 100.0
            putback_s[c.code] = -(abs(cv / pp - cfg.putback_trigger_ratio)) if pp > 0 else 0
        mat_s = {c.code: -(getattr(c, "remain_years", 6.0) or 6.0) for c in candidates}
        size_s = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        ranked = {n: _rank_scores(v) for n, v in [("price", price_s), ("premium", prem_s), ("roe", roe_s), ("ep", ep_s), ("vol", vol_s), ("gamma", gamma_s), ("putback", putback_s), ("maturity", mat_s), ("size", size_s)]}
        w = {"price": cfg.weight_price, "premium": cfg.weight_premium, "roe": cfg.weight_stock_roe, "ep": cfg.weight_stock_ep, "vol": cfg.weight_stock_vol, "gamma": cfg.weight_gamma, "putback": cfg.weight_putback, "maturity": cfg.weight_maturity, "size": cfg.weight_issue_size}
        composite = {c.code: sum(w.get(n, 0) * ranked[n].get(c.code, 0) for n in ranked) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
