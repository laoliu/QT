"""
Price Channel Breakout Strategy (价格通道突破).

Category: Channel
Markets: All

A simplified breakout strategy using N-day high/low channels with
volume confirmation to filter false breakouts.

Signal logic:
  - BUY:  Price breaks N-day high with above-average volume
  - SELL: Price breaks M-day low OR trailing stop hit

Parameters:
  - breakout_period:  Period for breakout detection (default 20)
  - exit_period:      Period for exit channel (default 10)
  - vol_confirm:      Require volume confirmation (default true)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class PriceChannelBreakoutStrategy(BaseStrategy):
    """Price channel breakout with volume confirmation."""

    STRATEGY_META = StrategyMeta(
        id="price-channel-breakout",
        name="价格通道突破 (Price Channel Breakout)",
        description=(
            "价格通道突破策略：价格放量突破N日高点买入，跌破M日低点卖出。"
            "通过成交量确认过滤假突破信号，提高胜率。"
            "适合捕捉趋势启动的第一波行情。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("breakout_period", "number", 20, "突破检测周期", min=5, max=60),
            StrategyParameter("exit_period", "number", 10, "出场通道周期", min=3, max=30),
            StrategyParameter("vol_confirm", "boolean", True, "需要量能确认"),
        ],
    )

    params = (
        ("breakout_period", 20),
        ("exit_period", 10),
        ("vol_confirm", True),
    )

    def __init__(self):
        super().__init__()
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.breakout_period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.exit_period)
        self.vol_sma = bt.indicators.SMA(self.data.volume, period=20)

    def next(self):
        close = self.data.close[0]

        if not self.position:
            is_breakout = close > self.highest[-1]
            vol_ok = (not self.p.vol_confirm) or (
                self.vol_sma[0] > 0 and self.data.volume[0] > self.vol_sma[0] * 1.5
            )
            if is_breakout and vol_ok:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    vol_ratio = (
                        self.data.volume[0] / self.vol_sma[0]
                        if self.vol_sma[0] > 0
                        else 0
                    )
                    self.record_signal(
                        "buy",
                        close,
                        f"通道突破买入: 价格{close:.2f} > "
                        f"{self.p.breakout_period}日高点{self.highest[-1]:.2f}, "
                        f"量比={vol_ratio:.1f}x",
                    )
        else:
            if close < self.lowest[-1]:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"通道跌破卖出: 价格{close:.2f} < "
                    f"{self.p.exit_period}日低点{self.lowest[-1]:.2f}",
                )
