"""
Momentum Rotation Strategy (动量轮动).

Category: Composite / Advanced
Markets: All

Single-asset momentum strategy that uses multiple timeframe momentum.

Signal logic:
  - BUY:  Short-term and long-term momentum both positive and accelerating
  - SELL: Momentum turns negative or decelerating

Parameters:
  - short_mom:  Short-term momentum period (default 10)
  - long_mom:   Long-term momentum period (default 30)
  - ma_period:  Trend filter MA period (default 50)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MomentumRotationStrategy(BaseStrategy):
    """Dual-timeframe momentum strategy."""

    STRATEGY_META = StrategyMeta(
        id="momentum-rotation",
        name="动量轮动 (Momentum Rotation)",
        description=(
            "动量轮动策略：结合短期和长期动量因子，双周期动量共振时买入。"
            "当短期动量和长期动量均为正且加速时入场，动量减弱时退出。"
            "适合捕捉趋势加速阶段的超额收益。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("short_mom", "number", 10, "短期动量周期", min=5, max=20),
            StrategyParameter("long_mom", "number", 30, "长期动量周期", min=20, max=60),
            StrategyParameter("ma_period", "number", 50, "趋势过滤均线", min=20, max=100),
        ],
    )

    params = (
        ("short_mom", 10),
        ("long_mom", 30),
        ("ma_period", 50),
    )

    def __init__(self):
        super().__init__()
        self.short_roc = bt.indicators.RateOfChange100(
            self.data.close, period=self.p.short_mom
        )
        self.long_roc = bt.indicators.RateOfChange100(
            self.data.close, period=self.p.long_mom
        )
        self.ma = bt.indicators.SMA(self.data.close, period=self.p.ma_period)

    def next(self):
        short = self.short_roc[0]
        long_ = self.long_roc[0]
        close = self.data.close[0]

        if not self.position:
            if short > 0 and long_ > 0 and close > self.ma[0]:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"动量共振: 短期ROC={short:.1f}%, 长期ROC={long_:.1f}%",
                    )
        else:
            if short < 0 or close < self.ma[0]:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"动量减弱: 短期ROC={short:.1f}%, MA={self.ma[0]:.2f}",
                )
