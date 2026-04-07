"""
Engulfing Pattern Strategy (吞没形态).

Category: Pattern / Candlestick
Markets: All

Detects bullish/bearish engulfing candlestick patterns.

Signal logic:
  - BUY:  Bullish engulfing (down candle followed by larger up candle)
  - SELL: Bearish engulfing (up candle followed by larger down candle)

Parameters:
  - confirm_trend: Require prior trend for confirmation (default True)
  - trend_period:  Lookback for trend confirmation (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class EngulfingStrategy(BaseStrategy):
    """Engulfing candlestick pattern strategy."""

    STRATEGY_META = StrategyMeta(
        id="engulfing",
        name="吞没形态 (Engulfing)",
        description=(
            "吞没形态策略：看涨吞没（阴线后出现更大的阳线完全包裹前K线）买入，"
            "看跌吞没（阳线后出现更大的阴线完全包裹前K线）卖出。"
            "经典的K线反转形态，结合趋势确认提高准确率。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("confirm_trend", "boolean", True, "需要趋势确认"),
            StrategyParameter("trend_period", "number", 10, "趋势确认周期", min=5, max=30),
        ],
    )

    params = (
        ("confirm_trend", True),
        ("trend_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)

    def _is_bullish_engulfing(self):
        if len(self) < 2:
            return False
        prev_open = self.data.open[-1]
        prev_close = self.data.close[-1]
        curr_open = self.data.open[0]
        curr_close = self.data.close[0]
        if prev_close >= prev_open:
            return False
        if curr_close <= curr_open:
            return False
        if curr_open <= prev_close and curr_close >= prev_open:
            return True
        return False

    def _is_bearish_engulfing(self):
        if len(self) < 2:
            return False
        prev_open = self.data.open[-1]
        prev_close = self.data.close[-1]
        curr_open = self.data.open[0]
        curr_close = self.data.close[0]
        if prev_close <= prev_open:
            return False
        if curr_close >= curr_open:
            return False
        if curr_open >= prev_close and curr_close <= prev_open:
            return True
        return False

    def next(self):
        if not self.position:
            if self._is_bullish_engulfing():
                if self.p.confirm_trend and self.data.close[0] > self.sma[0]:
                    return
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"看涨吞没: 前K({self.data.open[-1]:.2f}->{self.data.close[-1]:.2f}), "
                        f"当K({self.data.open[0]:.2f}->{self.data.close[0]:.2f})",
                    )
        else:
            if self._is_bearish_engulfing():
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"看跌吞没: 前K({self.data.open[-1]:.2f}->{self.data.close[-1]:.2f}), "
                    f"当K({self.data.open[0]:.2f}->{self.data.close[0]:.2f})",
                )
