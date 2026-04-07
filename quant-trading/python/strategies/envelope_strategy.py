"""
Envelope / Moving Average Envelope Strategy (均线包络线).

Category: Channel
Markets: All

Envelopes are percentage-based bands plotted above and below a moving average.

Signal logic:
  - BUY:  Price drops below lower envelope (oversold bounce)
  - SELL: Price rises above upper envelope (overbought) or returns to MA

Parameters:
  - period:    MA period (default 20)
  - pct:       Envelope width as percentage (default 3.0%)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class EnvelopeStrategy(BaseStrategy):
    """Moving Average Envelope mean-reversion strategy."""

    STRATEGY_META = StrategyMeta(
        id="envelope",
        name="均线包络线 (MA Envelope)",
        description=(
            "均线包络线策略：以均线为中心，上下各偏移固定百分比形成通道。"
            "价格触及下边界买入（超卖反弹），触及上边界或回归均线时卖出。"
            "比布林带更稳定，适合波动率较为均匀的品种。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("period", "number", 20, "均线周期", min=10, max=60),
            StrategyParameter("pct", "number", 3.0, "包络线宽度(%)", min=1.0, max=10.0, step=0.5),
        ],
    )

    params = (
        ("period", 20),
        ("pct", 3.0),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.period)

    def next(self):
        close = self.data.close[0]
        mid = self.sma[0]
        pct = self.p.pct / 100.0
        upper = mid * (1 + pct)
        lower = mid * (1 - pct)

        if not self.position:
            if close <= lower:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"包络线超卖: 价格{close:.2f} <= 下轨{lower:.2f}",
                    )
        else:
            if close >= upper:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"包络线超买: 价格{close:.2f} >= 上轨{upper:.2f}",
                )
            elif close >= mid and self.data.close[-1] < self.sma[-1]:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"回归均线: 价格{close:.2f} >= MA{self.p.period}={mid:.2f}",
                )
