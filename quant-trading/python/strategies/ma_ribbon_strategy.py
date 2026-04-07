"""
MA Ribbon Strategy (均线带).

Category: Composite / Advanced
Markets: All

Uses a ribbon of multiple EMAs to gauge trend strength and direction.

Signal logic:
  - BUY:  All EMAs aligned bullishly (short > medium > long) + price above all
  - SELL: EMAs cross bearishly or price drops below the longest EMA

Parameters:
  - ema1: Shortest EMA (default 8)
  - ema2: Medium EMA (default 13)
  - ema3: Medium-long EMA (default 21)
  - ema4: Longest EMA (default 34)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MARibbonStrategy(BaseStrategy):
    """Moving Average Ribbon trend strategy."""

    STRATEGY_META = StrategyMeta(
        id="ma-ribbon",
        name="均线带 (MA Ribbon)",
        description=(
            "均线带策略：使用多条EMA构成均线带，全部多头排列且价格在所有均线之上时买入，"
            "均线空头排列或价格跌破最长均线时卖出。均线越密集排列趋势越强。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("ema1", "number", 8, "EMA1(最短)", min=3, max=15),
            StrategyParameter("ema2", "number", 13, "EMA2", min=8, max=25),
            StrategyParameter("ema3", "number", 21, "EMA3", min=15, max=40),
            StrategyParameter("ema4", "number", 34, "EMA4(最长)", min=25, max=60),
        ],
    )

    params = (
        ("ema1", 8),
        ("ema2", 13),
        ("ema3", 21),
        ("ema4", 34),
    )

    def __init__(self):
        super().__init__()
        self.e1 = bt.indicators.EMA(self.data.close, period=self.p.ema1)
        self.e2 = bt.indicators.EMA(self.data.close, period=self.p.ema2)
        self.e3 = bt.indicators.EMA(self.data.close, period=self.p.ema3)
        self.e4 = bt.indicators.EMA(self.data.close, period=self.p.ema4)

    def next(self):
        close = self.data.close[0]
        v1, v2, v3, v4 = self.e1[0], self.e2[0], self.e3[0], self.e4[0]

        if not self.position:
            if v1 > v2 > v3 > v4 and close > v1:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"均线带多头: EMA{self.p.ema1}={v1:.2f} > EMA{self.p.ema4}={v4:.2f}",
                    )
        else:
            if close < v4 or v1 < v2:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"均线带破位: 价格{close:.2f}, EMA{self.p.ema4}={v4:.2f}",
                )
