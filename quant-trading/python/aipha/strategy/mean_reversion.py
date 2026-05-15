"""均值回归策略 (Demo) — 布林带偏离度信号"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from aipha.domain.models import Bar, Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext
from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType


@dataclass
class MeanReversionStrategy(Strategy):
    """均值回归 — 价格低于 entry_std 倍标准差时买入, 回归 exit_std 时卖出"""
    name: str = "MeanReversion"
    lookback_days: int = 20
    entry_std: float = 2.0
    exit_std: float = 0.5
    max_positions: int = 10
    position_pct: float = 0.10
    target_symbols: list[str] = field(default_factory=list)
    _price_history: dict[str, list[float]] = field(default_factory=dict, repr=False)
    _entry_prices: dict[str, float] = field(default_factory=dict, repr=False)
    _bar_count: int = field(default=0, repr=False)

    @classmethod
    def param_space(cls) -> ParamSpace:
        return ParamSpace("mean_reversion", [
            ParamSpec("lookback_days", ParamType.INT, default=20, low=10, high=60, step=5),
            ParamSpec("entry_std", ParamType.FLOAT, default=2.0, low=1.0, high=3.5, step=0.5, is_critical=True),
            ParamSpec("exit_std", ParamType.FLOAT, default=0.5, low=0.0, high=1.5, step=0.25, is_critical=True),
            ParamSpec("max_positions", ParamType.INT, default=10, low=3, high=20, step=1),
            ParamSpec("position_pct", ParamType.FLOAT, default=0.10, low=0.05, high=0.30, step=0.05),
        ])

    def on_start(self) -> None:
        self._price_history.clear()
        self._entry_prices.clear()
        self._bar_count = 0

    def on_end(self) -> None:
        pass

    def on_bar(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        for sym, bar in bars.items():
            self._price_history.setdefault(sym, []).append(bar.close)
        return self._generate_signals(bars, context)

    def _generate_signals(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        current_pos = getattr(context, "positions", {})
        num_pos = sum(1 for v in current_pos.values() if v > 0)

        for sym, bar in bars.items():
            stats = self._calculate_statistics(sym)
            if stats is None:
                continue
            mean, std = stats
            if std < 1e-9:
                continue
            z = (bar.close - mean) / std

            qty = current_pos.get(sym, 0)
            if qty > 0:
                # 持仓中 — 检查是否回归
                if z > -self.exit_std:
                    intents.append(OrderIntent(
                        symbol=sym, intent_type=IntentType.EXIT,
                        side=Side.SELL, quantity=qty,
                    ))
                    self._entry_prices.pop(sym, None)
            else:
                # 未持仓 — 检查是否进场
                if z < -self.entry_std and num_pos < self.max_positions:
                    cash = getattr(context, "cash", 0.0)
                    alloc = cash * self.position_pct
                    qty_buy = int(alloc / bar.close / 100) * 100
                    if qty_buy > 0:
                        intents.append(OrderIntent(
                            symbol=sym, intent_type=IntentType.ENTRY,
                            side=Side.BUY, quantity=qty_buy,
                        ))
                        self._entry_prices[sym] = bar.close
                        num_pos += 1
        return intents

    def _calculate_statistics(self, symbol: str) -> Optional[tuple[float, float]]:
        prices = self._price_history.get(symbol)
        if prices is None or len(prices) < self.lookback_days:
            return None
        recent = prices[-self.lookback_days:]
        # Handle (date, price) tuples
        values = [v[1] if isinstance(v, (tuple, list)) else v for v in recent]
        mean = sum(values) / len(values)
        std = math.sqrt(sum((p - mean) ** 2 for p in values) / len(values))
        return mean, std
