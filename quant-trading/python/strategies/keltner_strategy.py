"""
Keltner Channel Strategy (肯特纳通道).

Category: Channel
Markets: All

Keltner Channel = EMA ± multiplier × ATR.
Similar to Bollinger Bands but uses ATR instead of standard deviation.

Signal logic:
  - BUY:  Price closes above upper Keltner channel (momentum breakout)
  - SELL: Price closes below middle line (EMA) or lower channel

Parameters:
  - ema_period:   EMA period (default 20)
  - atr_period:   ATR period (default 10)
  - multiplier:   ATR multiplier (default 2.0)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class KeltnerStrategy(BaseStrategy):
    """Keltner Channel breakout strategy."""

    STRATEGY_META = StrategyMeta(
        id="keltner",
        name="肯特纳通道 (Keltner Channel)",
        description=(
            "肯特纳通道策略：以EMA为中线，ATR的倍数为通道宽度。"
            "价格突破上轨买入（动量突破），回落至中线或下轨卖出。"
            "相比布林带更平滑，减少假突破信号。"
        ),
        category="channel",
        parameters=[
            StrategyParameter("ema_period", "number", 20, "EMA周期", min=10, max=50),
            StrategyParameter("atr_period", "number", 10, "ATR周期", min=5, max=30),
            StrategyParameter("multiplier", "number", 2.0, "ATR倍数", min=1.0, max=4.0, step=0.5),
        ],
    )

    params = (
        ("ema_period", 20),
        ("atr_period", 10),
        ("multiplier", 2.0),
    )

    def __init__(self):
        super().__init__()
        self.ema = bt.indicators.EMA(self.data.close, period=self.p.ema_period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)

    def next(self):
        close = self.data.close[0]
        mid = self.ema[0]
        atr_val = self.atr[0]
        upper = mid + self.p.multiplier * atr_val
        lower = mid - self.p.multiplier * atr_val

        if not self.position:
            if close > upper:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"肯特纳突破: 价格{close:.2f} > 上轨{upper:.2f}",
                    )
        else:
            if close < mid:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"肯特纳回中: 价格{close:.2f} < 中线{mid:.2f}",
                )
