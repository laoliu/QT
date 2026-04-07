"""
ATR Channel / Volatility Channel Strategy (ATR波动通道).

Category: Channel
Markets: All

Uses ATR to build dynamic volatility-based channels around an EMA.

Signal logic:
  - BUY:  Price breaks above upper ATR channel (volatility breakout)
  - SELL: Price falls below EMA or lower channel (trend failure)

Parameters:
  - ema_period:   EMA center line period (default 20)
  - atr_period:   ATR period (default 14)
  - upper_mult:   ATR multiplier for upper band (default 2.5)
  - lower_mult:   ATR multiplier for lower band / stop (default 1.5)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ATRChannelStrategy(BaseStrategy):
    """ATR Volatility Channel breakout strategy."""

    STRATEGY_META = StrategyMeta(
        id="atr-channel",
        name="ATR波动通道 (ATR Channel)",
        description=(
            "ATR波动通道策略：以EMA为中心线，ATR的倍数构建动态通道。"
            "波动率自适应，高波动时通道扩大减少假信号，低波动时收窄提高灵敏度。"
            "突破上轨买入，跌破中线卖出。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("ema_period", "number", 20, "EMA周期", min=10, max=50),
            StrategyParameter("atr_period", "number", 14, "ATR周期", min=7, max=30),
            StrategyParameter("upper_mult", "number", 2.5, "上轨ATR倍数", min=1.0, max=4.0, step=0.5),
            StrategyParameter("lower_mult", "number", 1.5, "下轨ATR倍数", min=0.5, max=3.0, step=0.5),
        ],
    )

    params = (
        ("ema_period", 20),
        ("atr_period", 14),
        ("upper_mult", 2.5),
        ("lower_mult", 1.5),
    )

    def __init__(self):
        super().__init__()
        self.ema = bt.indicators.EMA(self.data.close, period=self.p.ema_period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)

    def next(self):
        close = self.data.close[0]
        mid = self.ema[0]
        atr_val = self.atr[0]
        upper = mid + self.p.upper_mult * atr_val
        lower = mid - self.p.lower_mult * atr_val

        if not self.position:
            if close > upper:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"ATR通道突破: 价格{close:.2f} > 上轨{upper:.2f}",
                    )
        else:
            if close < lower:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"ATR通道跌破: 价格{close:.2f} < 下轨{lower:.2f}",
                )
            elif close < mid:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"回中卖出: 价格{close:.2f} < EMA{self.p.ema_period}={mid:.2f}",
                )
