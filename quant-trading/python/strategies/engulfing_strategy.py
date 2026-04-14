"""
Engulfing Pattern Strategy (吞没形态).

Category: Pattern Recognition
Markets: All

Signal logic:
  - BUY:  Bullish engulfing (bearish candle followed by larger bullish candle)
  - SELL: Bearish engulfing (bullish candle followed by larger bearish candle)

Parameters:
  - trend_period: Period to determine prior trend context (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class EngulfingStrategy(BaseStrategy):
    """Engulfing candlestick pattern strategy."""

    STRATEGY_META = StrategyMeta(
        id="engulfing",
        name="吞没形态 (Engulfing Pattern)",
        description=(
            "吞没形态策略：看涨吞没买入，看跌吞没卖出。"
            "看涨吞没：下跌趋势中，阳线实体完全包裹前一根阴线实体。"
            "看跌吞没：上涨趋势中，阴线实体完全包裹前一根阳线实体。"
            "经典K线反转信号，胜率较高。"
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

    def _is_bullish_engulfing(self) -> bool:
        """Check for bullish engulfing pattern."""
        # Previous candle is bearish
        prev_bearish = self.data.close[-1] < self.data.open[-1]
        # Current candle is bullish
        curr_bullish = self.data.close[0] > self.data.open[0]
        # Current body engulfs previous body
        engulfs = (
            self.data.open[0] <= self.data.close[-1]
            and self.data.close[0] >= self.data.open[-1]
        )
        # In a downtrend context
        downtrend = self.data.close[0] < self.sma[0]
        return prev_bearish and curr_bullish and engulfs and downtrend

    def _is_bearish_engulfing(self) -> bool:
        """Check for bearish engulfing pattern."""
        prev_bullish = self.data.close[-1] > self.data.open[-1]
        curr_bearish = self.data.close[0] < self.data.open[0]
        engulfs = (
            self.data.open[0] >= self.data.close[-1]
            and self.data.close[0] <= self.data.open[-1]
        )
        uptrend = self.data.close[0] > self.sma[0]
        return prev_bullish and curr_bearish and engulfs and uptrend

    def next(self):
        if not self.position:
            if self._is_bullish_engulfing():
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        "看涨吞没: 阳线吞没前阴线，反转买入",
                    )
        else:
            if self._is_bearish_engulfing():
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    "看跌吞没: 阴线吞没前阳线，反转卖出",
                )
