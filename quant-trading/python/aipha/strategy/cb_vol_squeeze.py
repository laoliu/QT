"""可转债波动率压缩策略"""
from __future__ import annotations
import logging, math
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
from aipha.strategy.cb_enhanced_factors import rating_score
logger = logging.getLogger(__name__)

@dataclass
class CBVolSqueezeConfig(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 5; max_price: float = 135.0; max_premium: float = 40.0
    short_window: int = 10; long_window: int = 60; min_history: int = 20
    w_amplitude: float = 0.15; w_price: float = 0.35; w_premium: float = 0.30
    w_size: float = 0.05; w_rating: float = 0.15
    force_redeem_threshold: float = 130.0; force_redeem_days: int = 8

class CBVolSqueezeStrategy(CBRotationStrategy):
    name = "cb_vol_squeeze"
    def __init__(self, config: CBVolSqueezeConfig | None = None, **kw: object):
        super().__init__(config=config or CBVolSqueezeConfig(), **kw)
        self._close_history: dict[str, deque[float]] = {}; self._bar_count: int = 0
    @property
    def config(self) -> CBVolSqueezeConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        self._bar_count += 1; bars = getattr(ctx, "bars", {})
        maxlen = self.config.long_window + 5
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            if sym not in self._close_history: self._close_history[sym] = deque(maxlen=maxlen)
            self._close_history[sym].append(close)
    def _compute_squeeze_ratio(self, symbol: str) -> float:
        h = self._close_history.get(symbol)
        if not h or len(h) < self.config.min_history: return 0.0
        prices = list(h)
        def _vol(window: int) -> float:
            if len(prices) < window + 1: return 0.0
            seg = prices[-window - 1:]
            rets = [math.log(seg[i] / seg[i - 1]) for i in range(1, len(seg)) if seg[i - 1] > 0]
            if len(rets) < 2: return 0.0
            mu = sum(rets) / len(rets); return math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1))
        sv = _vol(self.config.short_window); lv = _vol(self.config.long_window)
        return (lv - sv) / lv if lv > 0 else 0.0
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
        squeeze = {c.code: self._compute_squeeze_ratio(c.code) for c in candidates}
        price = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        size = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        rat = {c.code: rating_score(getattr(c, "rating", "")) for c in candidates}
        ra, rp, rpm, rs, rr = _rank_scores(squeeze), _rank_scores(price), _rank_scores(prem), _rank_scores(size), _rank_scores(rat)
        composite = {c.code: cfg.w_amplitude * ra.get(c.code, 0) + cfg.w_price * rp.get(c.code, 0) + cfg.w_premium * rpm.get(c.code, 0) + cfg.w_size * rs.get(c.code, 0) + cfg.w_rating * rr.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
