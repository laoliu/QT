"""
Donchian Channel Breakout Strategy (唐奇安通道突破).

Category: Channel
Markets: All

The Donchian Channel is the basis of the Turtle Trading system.
This is a pure channel breakout strategy without the Turtle's pyramiding.

Signal logic:
  - BUY:  Price breaks above N-day highest high
  - SELL: Price breaks below M-day lowest low

Parameters:
  - upper_period: Lookback for upper channel (default 20)
  - lower_period: Lookback for lower channel (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DonchianBreakoutStrategy(BaseStrategy):
    """Donchian Channel breakout trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="donchian-breakout",
        name="唐奇安通道突破 (Donchian Breakout)",
        description=(
            "唐奇安通道突破策略：价格突破N日最高价买入，跌破M日最低价卖出。"
            "唐奇安通道是海龟交易法的基础，由Richard Donchian发明。"
            "简单有效的趋势跟踪方法，适合捕捉中长期趋势。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("upper_period", "number", 20, "上轨突破周期", min=5, max=60),
            StrategyParameter("lower_period", "number", 10, "下轨突破周期", min=5, max=30),
        ],
    )

    params = (
        ("upper_period", 20),
        ("lower_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.upper_period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.lower_period)

    def next(self):
        close = self.data.close[0]

        if not self.position:
            if close > self.highest[-1]:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"唐奇安突破买入: 价格{close:.2f} > "
                        f"{self.p.upper_period}日高点{self.highest[-1]:.2f}",
                    )
        else:
            if close < self.lowest[-1]:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"唐奇安跌破卖出: 价格{close:.2f} < "
                    f"{self.p.lower_period}日低点{self.lowest[-1]:.2f}",
                )
