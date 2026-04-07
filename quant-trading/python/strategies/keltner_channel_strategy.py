"""
Keltner Channel Strategy (肯特纳通道).

Category: Channel
Markets: All

Signal logic:
  - BUY:  Price closes above upper Keltner channel (breakout)
  - SELL: Price closes below lower Keltner channel or back to middle

Parameters:
  - ema_period:   EMA period for middle band (default 20)
  - atr_period:   ATR period (default 20)
  - atr_mult:     ATR multiplier for channel width (default 2.0)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class KeltnerChannelStrategy(BaseStrategy):
    """Keltner Channel breakout trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="keltner-channel",
        name="肯特纳通道 (Keltner Channel)",
        description=(
            "肯特纳通道策略：价格突破上轨买入，跌破下轨或回到中轨卖出。"
            "通道由EMA中轨±N倍ATR构成，相比布林带更平滑。"
            "适合趋势突破交易，ATR通道能更好地适应波动率变化。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("ema_period", "number", 20, "EMA中轨周期", min=10, max=50),
            StrategyParameter("atr_period", "number", 20, "ATR周期", min=10, max=50),
            StrategyParameter("atr_mult", "number", 2.0, "ATR乘数", min=1.0, max=4.0, step=0.5),
        ],
    )

    params = (
        ("ema_period", 20),
        ("atr_period", 20),
        ("atr_mult", 2.0),
    )

    def __init__(self):
        super().__init__()
        self.mid = bt.indicators.EMA(self.data.close, period=self.p.ema_period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self.upper = self.mid + self.p.atr_mult * self.atr
        self.lower = self.mid - self.p.atr_mult * self.atr

    def next(self):
        close = self.data.close[0]
        upper = self.upper[0]
        lower = self.lower[0]
        mid = self.mid[0]

        if not self.position:
            if close > upper:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"肯特纳突破买入: 价格{close:.2f} > 上轨{upper:.2f}",
                    )
        else:
            if close < lower:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"肯特纳下轨卖出: 价格{close:.2f} < 下轨{lower:.2f}",
                )
            elif close < mid and self.data.close[-1] >= self.mid[-1]:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"回到中轨卖出: 价格{close:.2f} < 中轨{mid:.2f}",
                )
