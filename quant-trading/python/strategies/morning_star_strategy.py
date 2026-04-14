"""
Morning Star / Evening Star Strategy (启明星/黄昏星).

Category: Pattern Recognition
Markets: All

Signal logic:
  - BUY:  Morning star (3-bar bullish reversal at bottom)
  - SELL: Evening star (3-bar bearish reversal at top)

Parameters:
  - trend_period: Period to determine trend context (default 10)
  - small_body_pct: Max body size for middle candle as % of range (default 30%)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MorningStarStrategy(BaseStrategy):
    """Morning Star / Evening Star three-candle reversal strategy."""

    STRATEGY_META = StrategyMeta(
        id="morning-star",
        name="启明星/黄昏星 (Morning/Evening Star)",
        description=(
            "启明星/黄昏星策略：三根K线组合反转形态。"
            "启明星（买入）：大阴线 + 小实体（跳空低开）+ 大阳线。"
            "黄昏星（卖出）：大阳线 + 小实体（跳空高开）+ 大阴线。"
            "经典三K线反转形态，可靠性高于单根K线信号。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("trend_period", "number", 10, "趋势判断周期", min=5, max=30),
            StrategyParameter("small_body_pct", "number", 30, "中间K线最大实体比(%)", min=10, max=50, step=5),
        ],
    )

    params = (
        ("trend_period", 10),
        ("small_body_pct", 30),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)

    def _body(self, offset: int = 0) -> float:
        return abs(self.data.close[offset] - self.data.open[offset])

    def _range(self, offset: int = 0) -> float:
        return self.data.high[offset] - self.data.low[offset]

    def _is_morning_star(self) -> bool:
        """Morning star: big bearish + small body + big bullish."""
        # Bar -2: bearish candle
        if self.data.close[-2] >= self.data.open[-2]:
            return False
        # Bar -1: small body
        r = self._range(-1)
        if r <= 0 or (self._body(-1) / r * 100) > self.p.small_body_pct:
            return False
        # Bar 0: bullish candle closing above midpoint of bar -2
        if self.data.close[0] <= self.data.open[0]:
            return False
        midpoint = (self.data.open[-2] + self.data.close[-2]) / 2
        if self.data.close[0] < midpoint:
            return False
        # Downtrend context
        return self.data.close[-2] < self.sma[-2]

    def _is_evening_star(self) -> bool:
        """Evening star: big bullish + small body + big bearish."""
        if self.data.close[-2] <= self.data.open[-2]:
            return False
        r = self._range(-1)
        if r <= 0 or (self._body(-1) / r * 100) > self.p.small_body_pct:
            return False
        if self.data.close[0] >= self.data.open[0]:
            return False
        midpoint = (self.data.open[-2] + self.data.close[-2]) / 2
        if self.data.close[0] > midpoint:
            return False
        return self.data.close[-2] > self.sma[-2]

    def next(self):
        if not self.position:
            if self._is_morning_star():
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        "启明星买入: 大阴线+小实体+大阳线反转",
                    )
        else:
            if self._is_evening_star():
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    "黄昏星卖出: 大阳线+小实体+大阴线反转",
                )
