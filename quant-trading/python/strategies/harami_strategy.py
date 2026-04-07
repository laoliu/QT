"""
Harami Pattern Strategy (孕线形态).

Category: Pattern Recognition
Markets: All

Signal logic:
  - BUY:  Bullish harami (large bearish candle followed by small bullish candle within it)
  - SELL: Bearish harami (large bullish candle followed by small bearish candle within it)

Parameters:
  - trend_period: Period to determine trend context (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class HaramiStrategy(BaseStrategy):
    """Harami candlestick pattern strategy."""

    STRATEGY_META = StrategyMeta(
        id="harami",
        name="孕线形态 (Harami Pattern)",
        description=(
            "孕线形态策略：看涨孕线买入，看跌孕线卖出。"
            "看涨孕线：大阴线后跟一根完全包含在其中的小阳线。"
            "看跌孕线：大阳线后跟一根完全包含在其中的小阴线。"
            "孕线表示趋势动能衰减，可能即将反转。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("trend_period", "number", 10, "趋势判断周期", min=5, max=30),
        ],
    )

    params = (("trend_period", 10),)

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)

    def _is_bullish_harami(self) -> bool:
        # Previous: large bearish candle
        prev_bearish = self.data.close[-1] < self.data.open[-1]
        # Current: small bullish candle contained within previous body
        curr_bullish = self.data.close[0] > self.data.open[0]
        contained = (
            self.data.open[0] > self.data.close[-1]
            and self.data.close[0] < self.data.open[-1]
        )
        # Downtrend context
        downtrend = self.data.close[-1] < self.sma[-1]
        return prev_bearish and curr_bullish and contained and downtrend

    def _is_bearish_harami(self) -> bool:
        prev_bullish = self.data.close[-1] > self.data.open[-1]
        curr_bearish = self.data.close[0] < self.data.open[0]
        contained = (
            self.data.open[0] < self.data.close[-1]
            and self.data.close[0] > self.data.open[-1]
        )
        uptrend = self.data.close[-1] > self.sma[-1]
        return prev_bullish and curr_bearish and contained and uptrend

    def next(self):
        if not self.position:
            if self._is_bullish_harami():
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        "看涨孕线买入: 大阴线内嵌小阳线",
                    )
        else:
            if self._is_bearish_harami():
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    "看跌孕线卖出: 大阳线内嵌小阴线",
                )
