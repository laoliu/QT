"""
Dual Thrust Strategy (双推力策略).

Category: Composite (Advanced)
Markets: All

A classic CTA strategy developed by Michael Chalek.
Uses N-day high/low/close to compute dynamic breakout levels.

Signal logic:
  - Range = Max(HH-LC, HC-LL) over N days
  - BUY:  Price > Open + k1 × Range
  - SELL: Price < Open - k2 × Range (or trailing stop)

Parameters:
  - lookback:  Days for range calculation (default 4)
  - k1:        Upper breakout multiplier (default 0.5)
  - k2:        Lower breakout multiplier (default 0.5)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DualThrustStrategy(BaseStrategy):
    """Dual Thrust CTA breakout strategy."""

    STRATEGY_META = StrategyMeta(
        id="dual-thrust",
        name="双推力策略 (Dual Thrust)",
        description=(
            "Dual Thrust策略：经典CTA突破策略，由Michael Chalek开发。"
            "根据过去N日的价格波动范围计算上下突破线。"
            "价格突破开盘价+k×Range买入，跌破开盘价-k×Range止损。"
            "曾是全球最活跃的CTA策略之一。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("lookback", "number", 4, "回看天数", min=2, max=10),
            StrategyParameter("k1", "number", 0.5, "上突破乘数", min=0.2, max=1.0, step=0.1),
            StrategyParameter("k2", "number", 0.5, "下突破乘数", min=0.2, max=1.0, step=0.1),
        ],
    )

    params = (
        ("lookback", 4),
        ("k1", 0.5),
        ("k2", 0.5),
    )

    def __init__(self):
        super().__init__()
        self.hh = bt.indicators.Highest(self.data.high, period=self.p.lookback)
        self.ll = bt.indicators.Lowest(self.data.low, period=self.p.lookback)
        self.hc = bt.indicators.Highest(self.data.close, period=self.p.lookback)
        self.lc = bt.indicators.Lowest(self.data.close, period=self.p.lookback)

    def next(self):
        close = self.data.close[0]
        open_price = self.data.open[0]

        range1 = self.hh[-1] - self.lc[-1]
        range2 = self.hc[-1] - self.ll[-1]
        dual_range = max(range1, range2)

        upper = open_price + self.p.k1 * dual_range
        lower = open_price - self.p.k2 * dual_range

        if not self.position:
            if close > upper and dual_range > 0:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"Dual Thrust突破: 价格{close:.2f} > 上轨{upper:.2f}",
                    )
        else:
            if close < lower:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"Dual Thrust止损: 价格{close:.2f} < 下轨{lower:.2f}",
                )
