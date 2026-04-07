"""
Price Channel Breakout Strategy (价格通道突破).

Category: Channel
Markets: All

A simplified price channel using N-period high/low with midline.
Combines breakout entry with mean-reversion exit.

Signal logic:
  - BUY:  Close > N-period high (breakout)
  - SELL: Close < midline (mean reversion) or trail-stop

Parameters:
  - period:     Channel lookback period (default 20)
  - atr_period: ATR for stop-loss (default 14)
  - atr_mult:   ATR multiplier for stop-loss (default 2.0)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class PriceChannelStrategy(BaseStrategy):
    """Price Channel breakout with ATR stop."""

    STRATEGY_META = StrategyMeta(
        id="price-channel",
        name="价格通道突破 (Price Channel)",
        description=(
            "价格通道突破策略：价格突破N日最高价时买入，配合ATR动态止损。"
            "当价格回落到通道中线以下或触发止损时卖出。简单有效的通道突破系统。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("period", "number", 20, "通道周期", min=10, max=60),
            StrategyParameter("atr_period", "number", 14, "ATR周期", min=7, max=30),
            StrategyParameter("atr_mult", "number", 2.0, "止损ATR倍数", min=1.0, max=4.0, step=0.5),
        ],
    )

    params = (
        ("period", 20),
        ("atr_period", 14),
        ("atr_mult", 2.0),
    )

    def __init__(self):
        super().__init__()
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self._stop_price = 0.0

    def next(self):
        close = self.data.close[0]
        upper = self.highest[-1]
        lower = self.lowest[-1]
        mid = (upper + lower) / 2.0

        if not self.position:
            if close > upper:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self._stop_price = close - self.p.atr_mult * self.atr[0]
                    self.record_signal(
                        "buy", close,
                        f"通道突破: 价格{close:.2f} > {self.p.period}日高点{upper:.2f}",
                    )
        else:
            new_stop = close - self.p.atr_mult * self.atr[0]
            if new_stop > self._stop_price:
                self._stop_price = new_stop

            if close < self._stop_price:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"ATR止损: 价格{close:.2f} < 止损{self._stop_price:.2f}",
                )
            elif close < mid:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"通道中线卖出: 价格{close:.2f} < 中线{mid:.2f}",
                )
