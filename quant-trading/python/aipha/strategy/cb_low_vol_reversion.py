"""可转债低波反转策略"""
from __future__ import annotations
import logging, math
from collections import deque
from dataclasses import dataclass
from typing import Any
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class CBLowVolReversionConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5; max_price: float = 130.0; min_turnover: float = 20.0
    min_days_to_maturity: int = 30; exclude_st: bool = True; stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.0; lot_size: int = 10; buffer_k: int = 5; max_premium: float = 50.0
    use_age_filter: bool = True; min_age_days: int = 5
    weight_low_vol: float = 0.30; weight_reversal: float = 0.0; weight_bond_floor: float = 0.25
    weight_premium: float = 0.15; weight_stock_roe: float = 0.20; weight_issue_size: float = 0.10
    vol_lookback: int = 20; reversal_lookback: int = 10; use_stock_bridge: bool = True

class CBLowVolReversionStrategy(CBRotationStrategy):
    name = "cb_low_vol_reversion"
    def __init__(self, config: CBLowVolReversionConfig | None = None, stock_bridge: Any | None = None, **kw: object):
        super().__init__(config=config or CBLowVolReversionConfig(), **kw)
        self._stock_bridge = stock_bridge
        self._close_history: dict[str, deque[float]] = {}
    @property
    def config(self) -> CBLowVolReversionConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        bars = getattr(ctx, "bars", {})
        maxlen = max(self.config.vol_lookback, self.config.reversal_lookback) + 5
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            if sym not in self._close_history: self._close_history[sym] = deque(maxlen=maxlen)
            self._close_history[sym].append(close)
    def _compute_volatility(self, symbol: str, lookback: int) -> float:
        hist = self._close_history.get(symbol)
        if not hist or len(hist) < lookback + 1: return 99.0
        prices = list(hist)[-(lookback + 1):]
        rets = [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices)) if prices[i - 1] > 0]
        if len(rets) < 2: return 99.0
        mu = sum(rets) / len(rets); var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var * 252) if var > 0 else 0.0
    def _compute_return(self, symbol: str, lookback: int) -> float:
        hist = self._close_history.get(symbol)
        if not hist or len(hist) < lookback: return 0.0
        prices = list(hist)
        return (prices[-1] - prices[-lookback]) / prices[-lookback] if prices[-lookback] > 0 else 0.0
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > self.config.max_premium: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        vol = {c.code: -self._compute_volatility(c.code, cfg.vol_lookback) for c in candidates}
        rev = {c.code: -self._compute_return(c.code, cfg.reversal_lookback) for c in candidates}
        bf = {c.code: getattr(c, "bond_floor", 0.0) or 0.0 for c in candidates}
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        roe = {c.code: getattr(c, "stock_roe", 0.0) or 0.0 for c in candidates}
        size = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        rv, rr, rbf, rpm, rroe, rsz = _rank_scores(vol), _rank_scores(rev), _rank_scores(bf), _rank_scores(prem), _rank_scores(roe), _rank_scores(size)
        composite = {c.code: cfg.weight_low_vol * rv.get(c.code, 0) + cfg.weight_reversal * rr.get(c.code, 0) + cfg.weight_bond_floor * rbf.get(c.code, 0) + cfg.weight_premium * rpm.get(c.code, 0) + cfg.weight_stock_roe * rroe.get(c.code, 0) + cfg.weight_issue_size * rsz.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
