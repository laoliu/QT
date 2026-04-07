"""
Doji Star Reversal Strategy (十字星反转).

Category: Pattern / Candlestick
Markets: All

Detects Doji star patterns at potential reversal points.

Signal logic:
  - BUY:  Morning doji star (downtrend + doji + bullish candle)
  - SELL: Evening doji star (uptrend + doji + bearish candle)

Parameters:
  - doji_pct:     Max body-to-range ratio for doji (default 0.1 = 10%)
  - trend_period: Trend confirmation lookback (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DojiStarStrategy(BaseStrategy):
    """Doji Star reversal pattern strategy."""

    STRATEGY_META = StrategyMeta(
        id="doji-star",
        name="十字星反转 (Doji Star)",
        description=(
            "十字星反转策略：在下降趋势末端出现十字星后接阳线（早晨之星）买入，"
            "在上升趋势末端出现十字星后接阴线（黄昏之星）卖出。"
            "十字星表示多空力量均衡，是潜在反转信号。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("doji_pct", "number", 0.1, "十字星实体比例阈值", min=0.03, max=0.2, step=0.01),
            StrategyParameter("trend_period", "number", 10, "趋势确认周期", min=5, max=30),
        ],
    )

    params = (
        ("doji_pct", 0.1),
        ("trend_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)

    def _is_doji(self, idx=0):
        high = self.data.high[idx]
        low = self.data.low[idx]
        open_ = self.data.open[idx]
        close = self.data.close[idx]
        candle_range = high - low
        if candle_range <= 0:
            return True
        body = abs(close - open_)
        return body / candle_range <= self.p.doji_pct

    def _is_bearish(self, idx=0):
        return self.data.close[idx] < self.data.open[idx]

    def _is_bullish(self, idx=0):
        return self.data.close[idx] > self.data.open[idx]

    def next(self):
        if len(self) < 3:
            return

        if not self.position:
            # Morning doji star: bearish candle → doji → bullish candle
            if (self._is_bearish(-2)
                    and self._is_doji(-1)
                    and self._is_bullish(0)
                    and self.data.close[-2] > self.sma[-2]):  # was in downtrend correction
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        "早晨十字星: 阴线+十字星+阳线反转",
                    )
        else:
            # Evening doji star: bullish candle → doji → bearish candle
            if (self._is_bullish(-2)
                    and self._is_doji(-1)
                    and self._is_bearish(0)):
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    "黄昏十字星: 阳线+十字星+阴线反转",
                )
