"""
ATR Channel Breakout Strategy (ATR通道突破).

Category: Channel
Markets: All

Uses ATR-based channels around a moving average for volatility-adjusted breakouts.

Signal logic:
  - BUY:  Price breaks above MA + ATR_mult * ATR
  - SELL: Price falls below MA - ATR_mult * ATR or trails with ATR stop

Parameters:
  - ma_period:  Moving average period (default 20)
  - atr_period: ATR period (default 14)
  - atr_mult:   ATR multiplier (default 2.5)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ATRChannelStrategy(BaseStrategy):
    """ATR channel breakout trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="atr-channel",
        name="ATR通道突破 (ATR Channel)",
        description=(
            "ATR通道突破策略：价格突破均线+N倍ATR上轨买入，跌破下轨卖出。"
            "ATR通道根据市场波动率自动调整通道宽度。"
            "波动率高时通道变宽减少假突破，波动率低时通道变窄提高灵敏度。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("ma_period", "number", 20, "均线周期", min=10, max=60),
            StrategyParameter("atr_period", "number", 14, "ATR周期", min=7, max=30),
            StrategyParameter("atr_mult", "number", 2.5, "ATR乘数", min=1.0, max=4.0, step=0.5),
        ],
    )

    params = (
        ("ma_period", 20),
        ("atr_period", 14),
        ("atr_mult", 2.5),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.ma_period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self._trailing_stop = 0.0

    def next(self):
        close = self.data.close[0]
        upper = self.sma[0] + self.p.atr_mult * self.atr[0]
        lower = self.sma[0] - self.p.atr_mult * self.atr[0]

        if not self.position:
            if close > upper:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self._trailing_stop = close - self.p.atr_mult * self.atr[0]
                    self.record_signal(
                        "buy",
                        close,
                        f"ATR通道突破买入: 价格{close:.2f} > 上轨{upper:.2f}",
                    )
        else:
            # Update trailing stop
            new_stop = close - self.p.atr_mult * self.atr[0]
            if new_stop > self._trailing_stop:
                self._trailing_stop = new_stop

            if close < self._trailing_stop:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"ATR跟踪止损: 价格{close:.2f} < 止损{self._trailing_stop:.2f}",
                )
            elif close < lower:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"ATR通道下轨卖出: 价格{close:.2f} < 下轨{lower:.2f}",
                )
