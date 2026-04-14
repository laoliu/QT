"""换手率突增策略"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class TurnoverSurgeConfig(CBRotationConfig):
    top_k: int = 10; rebalance_days: int = 3; max_price: float = 140.0; max_premium: float = 40.0
    lookback_days: int = 20; min_surge_ratio: float = 2.0; require_positive_return: bool = True

class TurnoverSurgeStrategy(CBRotationStrategy):
    name = "turnover_surge"
    def __init__(self, config: TurnoverSurgeConfig | None = None, **kw: object):
        super().__init__(config=config or TurnoverSurgeConfig(), **kw)
        self._turnover_history: dict[str, deque[float]] = {}
        self._close_history: dict[str, deque[float]] = {}
    @property
    def config(self) -> TurnoverSurgeConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        bars = getattr(ctx, "bars", {})
        maxlen = self.config.lookback_days + 5
        for sym, bar in bars.items():
            tv = getattr(bar, "turnover", None) or getattr(bar, "amount", 0.0)
            close = getattr(bar, "close", None)
            if tv is not None:
                if sym not in self._turnover_history: self._turnover_history[sym] = deque(maxlen=maxlen)
                self._turnover_history[sym].append(tv)
            if close is not None:
                if sym not in self._close_history: self._close_history[sym] = deque(maxlen=maxlen)
                self._close_history[sym].append(close)
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        price = getattr(c, "close", None)
        if price is not None and price > cfg.max_price: return False
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem > cfg.max_premium: return False
        th = self._turnover_history.get(c.code)
        if not th or len(th) < cfg.lookback_days: return False
        tvs = list(th); avg = sum(tvs[:-1]) / max(len(tvs) - 1, 1)
        if avg <= 0 or tvs[-1] / avg < cfg.min_surge_ratio: return False
        if cfg.require_positive_return:
            ch = self._close_history.get(c.code)
            if ch and len(ch) >= 2 and ch[-1] <= ch[-2]: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        surge_ratio: dict[str, float] = {}
        for c in candidates:
            th = self._turnover_history.get(c.code)
            if not th: surge_ratio[c.code] = 0.0; continue
            tvs = list(th); avg = sum(tvs[:-1]) / max(len(tvs) - 1, 1)
            surge_ratio[c.code] = tvs[-1] / avg if avg > 0 else 0.0
        return sorted(surge_ratio, key=surge_ratio.get, reverse=True)[:self.config.top_k]  # type: ignore
