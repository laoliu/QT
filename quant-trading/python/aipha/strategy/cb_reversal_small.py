"""可转债小盘反转策略"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class CBReversalSmallConfig(CBRotationConfig):
    top_k: int = 8; rebalance_days: int = 3; max_issue_size: float = 30000.0
    max_premium: float = 80.0; max_single_drop: float = -0.20
    reversal_lookback: int = 5; warmup_bars: int = 10
    size_weight: float = 0.30; reversal_weight: float = 0.70

class CBReversalSmallStrategy(CBRotationStrategy):
    name = "cb_reversal_small"
    def __init__(self, config: CBReversalSmallConfig | None = None, **kw: object):
        super().__init__(config=config or CBReversalSmallConfig(), **kw)
    @property
    def config(self) -> CBReversalSmallConfig: return self._config  # type: ignore
    def _should_rebalance(self) -> bool:
        if self._bar_count < self.config.warmup_bars:
            return False
        return super()._should_rebalance()
    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            if close is None: continue
            if sym not in self._close_history: self._close_history[sym] = deque(maxlen=self.config.reversal_lookback + 5)
            self._close_history[sym].append(close)
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem = getattr(c, "premium_rate", None)
            if prem is not None: prem = prem * 100.0
        if prem is not None and prem > self.config.max_premium: return False
        h = self._price_history.get(c.code)
        if not h or len(h) <= self.config.reversal_lookback: return False
        prices = list(h)
        ref = prices[-(self.config.reversal_lookback + 1)]
        if ref > 0:
            ret = (prices[-1] - ref) / ref
            if ret < self.config.max_single_drop: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        size = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        rev: dict[str, float] = {}
        for c in candidates:
            h = self._close_history.get(c.code); prices = list(h) if h else []
            if len(prices) > cfg.reversal_lookback and prices[-(cfg.reversal_lookback + 1)] > 0:
                ref = prices[-(cfg.reversal_lookback + 1)]
                ret = (prices[-1] - ref) / ref
                rev[c.code] = -ret if ret > cfg.max_single_drop else 0.0
            else: rev[c.code] = 0.0
        rs, rr = _rank_scores(size), _rank_scores(rev)
        composite = {c.code: cfg.size_weight * rs.get(c.code, 0) + cfg.reversal_weight * rr.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
