"""可转债波动率突破策略"""
from __future__ import annotations
import logging, math
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class VolBreakoutConfig(CBRotationConfig):
    top_k: int = 10; rebalance_days: int = 3; max_price: float = 135.0; stop_loss_pct: float = 0.10
    atr_short: int = 5; atr_long: int = 20; squeeze_threshold: float = 0.6; breakout_lookback: int = 10

class VolBreakoutStrategy(CBRotationStrategy):
    name = "vol_breakout"
    def __init__(self, config: VolBreakoutConfig | None = None, **kw: object):
        super().__init__(config=config or VolBreakoutConfig(), **kw)
        self._bar_data: dict[str, deque[tuple[float, float, float]]] = {}; self._bar_count: int = 0
    @property
    def config(self) -> VolBreakoutConfig: return self._config  # type: ignore
    @staticmethod
    def _calc_atr(data: list[tuple[float, float, float]], period: int) -> float:
        if len(data) < period + 1: return 0.0
        trs = []
        for i in range(-period, 0):
            h, l, pc = data[i][0], data[i][1], data[i - 1][2] if i - 1 >= -len(data) else data[i][2]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs) / len(trs) if trs else 0.0
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        self._bar_count += 1; bars = getattr(ctx, "bars", {})
        maxlen = self.config.atr_long + self.config.breakout_lookback + 5
        for sym, bar in bars.items():
            h = getattr(bar, "high", 0); l = getattr(bar, "low", 0); c = getattr(bar, "close", 0)
            if sym not in self._bar_data: self._bar_data[sym] = deque(maxlen=maxlen)
            self._bar_data[sym].append((h, l, c))
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        price = getattr(c, "close", None)
        if price is not None and price > self.config.max_price: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; scores: dict[str, float] = {}
        for c in candidates:
            data = list(self._bar_data.get(c.code, []))
            if len(data) < cfg.atr_long + 1: continue
            atr_s = self._calc_atr(data, cfg.atr_short)
            atr_l = self._calc_atr(data, cfg.atr_long)
            squeeze = atr_s / atr_l if atr_l > 0 else 1.0
            if squeeze < cfg.squeeze_threshold:
                scores[c.code] = (cfg.squeeze_threshold - squeeze) * 10
        return sorted(scores, key=scores.get, reverse=True)[:cfg.top_k]  # type: ignore
