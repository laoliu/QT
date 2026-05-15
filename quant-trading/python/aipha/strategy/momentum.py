"""动量轮动策略 (Demo) — N日涨幅排序 + 定期轮动"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from aipha.domain.models import Bar, Side
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext
from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType


@dataclass
class MomentumStrategy(Strategy):
    """简单动量轮动 — 按 lookback_days 区间涨幅排序买入 top_k"""
    name: str = "Momentum"
    lookback_days: int = 20
    top_k: int = 5
    rebalance_days: int = 5
    target_symbols: list[str] = field(default_factory=list)
    _price_history: dict[str, list[float]] = field(default_factory=dict, repr=False)
    _last_rebalance: Optional[date] = field(default=None, repr=False)
    _bar_count: int = field(default=0, repr=False)

    @classmethod
    def param_space(cls) -> ParamSpace:
        return ParamSpace("momentum", [
            ParamSpec("lookback_days", ParamType.INT, default=20, low=5, high=60, step=5, is_critical=True),
            ParamSpec("top_k", ParamType.INT, default=5, low=3, high=20, step=1, is_critical=True),
            ParamSpec("rebalance_days", ParamType.INT, default=5, low=1, high=20, step=1),
        ])

    def on_start(self) -> None:
        self._price_history.clear()
        self._last_rebalance = None
        self._bar_count = 0

    def on_end(self) -> None:
        pass

    def on_bar(self, bars: dict[str, Bar], context: StrategyContext) -> list[OrderIntent]:
        self._bar_count += 1
        max_len = self.lookback_days + 10
        # 更新价格历史
        for sym, bar in bars.items():
            self._price_history.setdefault(sym, []).append(bar.close)
            # Trim to avoid unbounded growth
            if len(self._price_history[sym]) > max_len:
                self._price_history[sym] = self._price_history[sym][-max_len:]

        current_date = next(iter(bars.values())).date if bars else None
        if current_date and not self._should_rebalance(current_date):
            return []

        scores = self._calculate_momentum_scores()
        if not scores:
            return []

        if current_date:
            self._last_rebalance = current_date

        sorted_syms = sorted(scores.keys(), key=lambda s: scores[s], reverse=True)
        target = sorted_syms[:self.top_k]
        return self._generate_rebalance_intents(target, bars, context)

    def _should_rebalance(self, current_date: date) -> bool:
        if self._last_rebalance is None:
            return True
        return (current_date - self._last_rebalance).days >= self.rebalance_days

    def _calculate_momentum_scores(self) -> dict[str, float]:
        scores: dict[str, float] = {}
        for sym, prices in self._price_history.items():
            if len(prices) < self.lookback_days:
                continue
            old = prices[-self.lookback_days]
            if old > 0:
                scores[sym] = (prices[-1] - old) / old
        return scores

    def _generate_rebalance_intents(
        self, target_holdings: list[str], bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        current_pos = getattr(context, "positions", {})
        # 卖出当前不在目标中的持仓
        for sym, qty in current_pos.items():
            if qty > 0 and sym not in target_holdings:
                intents.append(OrderIntent(
                    symbol=sym, intent_type=IntentType.EXIT,
                    side=Side.SELL, quantity=qty,
                ))
        # 等权买入目标
        if target_holdings and bars:
            cash = getattr(context, "cash", 0.0)
            per_stock = cash / len(target_holdings) if cash > 0 else 0
            for sym in target_holdings:
                if sym in current_pos and current_pos[sym] > 0:
                    continue
                if sym in bars and per_stock > 0:
                    price = bars[sym].close
                    qty = int(per_stock / price / 100) * 100
                    if qty > 0:
                        intents.append(OrderIntent(
                            symbol=sym, intent_type=IntentType.ENTRY,
                            side=Side.BUY, quantity=qty,
                        ))
        return intents
