"""
Envelope / Percentage Band Strategy (百分比包络线).

Category: Channel
Markets: All

Signal logic:
  - BUY:  Price touches or crosses below the lower envelope (oversold bounce)
  - SELL: Price touches or crosses above the upper envelope (overbought)

Parameters:
  - ma_period:  Moving average period (default 20)
  - pct:        Percentage distance from MA for bands (default 3.0%)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class EnvelopeStrategy(BaseStrategy):
    """Percentage envelope mean-reversion strategy."""

    STRATEGY_META = StrategyMeta(
        id="envelope",
        name="百分比包络线 (Envelope)",
        description=(
            "百分比包络线策略：价格触及下轨买入，触及上轨卖出。"
            "上下轨为均线±固定百分比，比布林带更简单直观。"
            "适合稳定波动的品种，震荡行情中表现优异。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("ma_period", "number", 20, "均线周期", min=5, max=60),
            StrategyParameter("pct", "number", 3.0, "包络百分比(%)", min=1.0, max=10.0, step=0.5),
        ],
    )

    params = (
        ("ma_period", 20),
        ("pct", 3.0),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.ma_period)
        pct_factor = self.p.pct / 100.0
        self.upper = self.sma * (1.0 + pct_factor)
        self.lower = self.sma * (1.0 - pct_factor)

    def next(self):
        close = self.data.close[0]
        upper = self.upper[0]
        lower = self.lower[0]
        mid = self.sma[0]

        if not self.position:
            if close <= lower:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"包络线下轨买入: 价格{close:.2f} <= 下轨{lower:.2f}",
                    )
        else:
            if close >= upper:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"包络线上轨卖出: 价格{close:.2f} >= 上轨{upper:.2f}",
                )
            elif close >= mid and self.data.close[-1] < self.sma[-1]:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"回到均线止盈: 价格{close:.2f} 穿越中轨{mid:.2f}",
                )
