"""双均线Demo策略"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass, field
from aipha.domain.models import Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext
logger = logging.getLogger(__name__)

@dataclass
class DualMAStrategy(Strategy):
    name: str = "DualMA"
    short_window: int = 5; long_window: int = 20; position_pct: float = 0.10
    max_positions: int = 10; target_symbols: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._close_history: dict[str, deque[float]] = {}
        self._prev_ma_state: dict[str, str] = {}
        self._bar_count: int = 0

    @property
    def _price_history(self) -> dict[str, deque]:
        return self._close_history

    @_price_history.setter
    def _price_history(self, value):
        self._close_history = value

    @classmethod
    def param_space(cls) -> "ParamSpace":
        from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType
        return ParamSpace("dual_ma", [
            ParamSpec("short_window", ParamType.INT, default=5, low=2, high=20, step=1, is_critical=True),
            ParamSpec("long_window", ParamType.INT, default=20, low=10, high=120, step=5, is_critical=True),
            ParamSpec("position_pct", ParamType.FLOAT, default=0.10, low=0.05, high=0.20, step=0.05),
            ParamSpec("max_positions", ParamType.INT, default=10, low=5, high=20, step=5),
        ])

    def on_start(self) -> None:
        self._close_history.clear()
        self._prev_ma_state.clear()
        self._bar_count = 0

    def _calculate_ma(self, symbol: str, window: int) -> float | None:
        h = self._close_history.get(symbol)
        if not h or len(h) < window: return None
        recent = list(h)[-window:]
        # Handle (date, price) tuples
        values = [v[1] if isinstance(v, (tuple, list)) else v for v in recent]
        return sum(values) / window

    def on_bar(self, bars: dict, ctx: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        intents: list[OrderIntent] = []
        current_positions = sum(1 for v in ctx.positions.values() if v > 0)
        for sym, bar in bars.items():
            if self.target_symbols and sym not in self.target_symbols: continue
            close = getattr(bar, "close", None)
            if close is None: continue
            if sym not in self._close_history: self._close_history[sym] = deque(maxlen=self.long_window + 5)
            self._close_history[sym].append(close)
            short_ma = self._calculate_ma(sym, self.short_window)
            long_ma = self._calculate_ma(sym, self.long_window)
            if short_ma is None or long_ma is None: continue
            prev_state = self._prev_ma_state.get(sym)
            if short_ma > long_ma:
                new_state = "above"
            elif short_ma < long_ma:
                new_state = "below"
            else:
                new_state = prev_state or "neutral"
            self._prev_ma_state[sym] = new_state
            pos = ctx.positions.get(sym, 0)
            if short_ma > long_ma and pos == 0 and (prev_state == "below" or prev_state is None):
                buy_count = sum(1 for i in intents if i.side == Side.BUY)
                if current_positions + buy_count >= self.max_positions:
                    continue
                qty = max(1, int(ctx.nav * self.position_pct / close))
                intents.append(OrderIntent(symbol=sym, side=Side.BUY, qty=qty, intent_type=IntentType.ORDER, reason="golden_cross"))
            elif short_ma < long_ma and pos > 0:
                intents.append(OrderIntent(symbol=sym, side=Side.SELL, qty=pos, intent_type=IntentType.ORDER, reason="death_cross"))
        return intents
