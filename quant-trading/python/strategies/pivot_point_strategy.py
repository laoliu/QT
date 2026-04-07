"""
Pivot Point Strategy (枢轴点策略).

Category: Composite
Markets: All

Uses classic pivot points (support/resistance levels) derived from
the previous day's high, low, and close.

Signal logic:
  - BUY:  Price breaks above R1 (first resistance) with momentum
  - SELL: Price falls below S1 (first support) or back to pivot

Parameters:
  - method: Pivot calculation method - "classic" (default)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class PivotPointStrategy(BaseStrategy):
    """Pivot Point support/resistance strategy."""

    STRATEGY_META = StrategyMeta(
        id="pivot-point",
        name="枢轴点策略 (Pivot Point)",
        description=(
            "枢轴点策略：价格突破R1阻力位买入，跌破S1支撑位卖出。"
            "枢轴点由前一日OHLC计算，是日内交易者最常用的支撑阻力系统。"
            "Pivot = (H+L+C)/3, R1 = 2P-L, S1 = 2P-H。"
        ),
        category="composite",
        parameters=[],
    )

    params = ()

    def __init__(self):
        super().__init__()
        self._pivot = 0.0
        self._r1 = 0.0
        self._s1 = 0.0

    def next(self):
        h = self.data.high[-1]
        l = self.data.low[-1]
        c = self.data.close[-1]
        self._pivot = (h + l + c) / 3.0
        self._r1 = 2 * self._pivot - l
        self._s1 = 2 * self._pivot - h

        close = self.data.close[0]

        if not self.position:
            if close > self._r1:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"突破R1买入: 价格{close:.2f} > R1={self._r1:.2f}, "
                        f"Pivot={self._pivot:.2f}",
                    )
        else:
            if close < self._s1:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"跌破S1卖出: 价格{close:.2f} < S1={self._s1:.2f}",
                )
            elif close < self._pivot and self.data.close[-1] >= self._pivot:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"回到枢轴卖出: 价格{close:.2f} < Pivot={self._pivot:.2f}",
                )
