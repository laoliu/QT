"""
Donchian Channel Breakout Strategy (唐奇安通道突破).

Category: Channel
Markets: All

Donchian Channel = N-period highest high / lowest low.
A classic breakout system (basis of the Turtle trading system).

Signal logic:
  - BUY:  Price breaks above the upper channel
  - SELL: Price breaks below the lower channel

Parameters:
  - entry_period: Channel period for entry (default 20)
  - exit_period:  Channel period for exit (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DonchianStrategy(BaseStrategy):
    """Donchian Channel breakout strategy."""

    STRATEGY_META = StrategyMeta(
        id="donchian",
        name="唐奇安通道 (Donchian Channel)",
        description=(
            "唐奇安通道突破策略：价格突破N日最高价（上轨）买入，跌破N日最低价（下轨）卖出。"
            "Richard Donchian发明，是海龟交易法的基础，经典的趋势突破系统。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("entry_period", "number", 20, "入场通道周期", min=10, max=60),
            StrategyParameter("exit_period", "number", 10, "出场通道周期", min=5, max=30),
        ],
    )

    params = (
        ("entry_period", 20),
        ("exit_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.entry_high = bt.indicators.Highest(self.data.high, period=self.p.entry_period)
        self.entry_low = bt.indicators.Lowest(self.data.low, period=self.p.entry_period)
        self.exit_low = bt.indicators.Lowest(self.data.low, period=self.p.exit_period)

    def next(self):
        close = self.data.close[0]

        if not self.position:
            if close > self.entry_high[-1]:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"唐奇安突破买入: 价格{close:.2f} > {self.p.entry_period}日高点{self.entry_high[-1]:.2f}",
                    )
        else:
            if close < self.exit_low[-1]:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"唐奇安跌破卖出: 价格{close:.2f} < {self.p.exit_period}日低点{self.exit_low[-1]:.2f}",
                )
