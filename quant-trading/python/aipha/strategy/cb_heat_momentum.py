"""可转债热度动量策略"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class CBHeatMomentumConfig(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 5; max_price: float = 135.0; min_turnover: float = 20.0
    min_issue_size: float = 0.0; max_issue_size: float = float("inf"); min_days_to_maturity: int = 30
    exclude_st: bool = True; stop_loss_pct: float = 0.10; buffer_k: int = 2
    force_redeem_threshold: float = 130.0; force_redeem_days: int = 8; exclude_called: bool = True
    min_rating: str = ""; exclude_unrated: bool = False; max_premium: float = 50.0
    accel_short_window: int = 5; accel_long_window: int = 20; min_history: int = 22
    w_accel: float = 0.15; w_premium: float = 0.35; w_price: float = 0.35; w_rating: float = 0.15

class CBHeatMomentumStrategy(CBRotationStrategy):
    name = "cb_heat_momentum"
    def __init__(self, config: CBHeatMomentumConfig | None = None, **kw: object):
        super().__init__(config=config or CBHeatMomentumConfig(), **kw)
        self._turnover_history: dict[str, deque[float]] = {}
    @property
    def config(self) -> CBHeatMomentumConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        bars = getattr(ctx, "bars", {})
        for sym, bar in bars.items():
            tv = getattr(bar, "turnover", None) or getattr(bar, "amount", 0.0)
            if tv is None: continue
            if sym not in self._turnover_history:
                self._turnover_history[sym] = deque(maxlen=self.config.accel_long_window + 5)
            self._turnover_history[sym].append(tv)
    def _compute_accel(self, symbol: str) -> float:
        hist = self._turnover_history.get(symbol)
        if not hist or len(hist) < self.config.min_history: return 0.0
        short_w = self.config.accel_short_window; long_w = self.config.accel_long_window
        recent = list(hist)
        short_avg = sum(recent[-short_w:]) / short_w if len(recent) >= short_w else 0.0
        long_avg = sum(recent[-long_w:]) / long_w if len(recent) >= long_w else 0.0
        return (short_avg - long_avg) / long_avg if long_avg > 0 else 0.0
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > self.config.max_premium: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        accel = {c.code: self._compute_accel(c.code) for c in candidates}
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        price = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        from aipha.strategy.cb_enhanced_factors import rating_score as rs
        rat = {c.code: rs(getattr(c, "rating", "")) for c in candidates}
        ra, rpm, rp, rr = _rank_scores(accel), _rank_scores(prem), _rank_scores(price), _rank_scores(rat)
        composite = {c.code: cfg.w_accel * ra.get(c.code, 0) + cfg.w_premium * rpm.get(c.code, 0) + cfg.w_price * rp.get(c.code, 0) + cfg.w_rating * rr.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
