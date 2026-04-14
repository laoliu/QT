"""双均线Demo策略"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass, field
from aipha.strategy.base import Strategy, StrategyContext
logger = logging.getLogger(__name__)

@dataclass
class DualMAStrategy(Strategy):
    name: str = "DualMA"
    short_window: int = 5; long_window: int = 20; position_pct: float = 0.10
    max_positions: int = 10; target_symbols: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._close_history: dict[str, deque[float]] = {}

    def on_start(self) -> None:
        self._close_history.clear()

    def _calculate_ma(self, symbol: str, window: int) -> float | None:
        h = self._close_history.get(symbol)
        if not h or len(h) < window: return None
        return sum(list(h)[-window:]) / window

    def on_bar(self, bars: dict, ctx: StrategyContext) -> list:
        intents: list = []
        for sym, bar in bars.items():
            if self.target_symbols and sym not in self.target_symbols: continue
            close = getattr(bar, "close", None)
            if close is None: continue
            if sym not in self._close_history: self._close_history[sym] = deque(maxlen=self.long_window + 5)
            self._close_history[sym].append(close)
            short_ma = self._calculate_ma(sym, self.short_window)
            long_ma = self._calculate_ma(sym, self.long_window)
            if short_ma is None or long_ma is None: continue
            pos = ctx.portfolio.get(sym)
            if short_ma > long_ma and pos is None:
                intents.append({"action": "BUY", "symbol": sym, "pct": self.position_pct})
            elif short_ma < long_ma and pos is not None:
                intents.append({"action": "SELL", "symbol": sym})
        return intents
