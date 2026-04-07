"""
Volume Breakout Strategy (放量突破).

Category: Pattern / Volume
Markets: All

Combines price breakout with volume confirmation.

Signal logic:
  - BUY:  Price breaks N-day high with volume > M × average
  - SELL: Price breaks below MA or volume dries up

Parameters:
  - price_period:  Price breakout lookback (default 20)
  - vol_period:    Volume average period (default 20)
  - vol_mult:      Volume spike multiplier (default 2.0)
  - ma_period:     Exit MA period (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class BreakoutVolumeStrategy(BaseStrategy):
    """Volume-confirmed breakout strategy."""

    STRATEGY_META = StrategyMeta(
        id="breakout-volume",
        name="放量突破 (Volume Breakout)",
        description=(
            "放量突破策略：价格创N日新高且成交量超过均量M倍时买入，"
            "价格跌破均线或量能萎缩时卖出。量价齐升确认突破有效性，"
            "是技术分析中最基本的交易法则之一。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("price_period", "number", 20, "价格突破周期", min=10, max=60),
            StrategyParameter("vol_period", "number", 20, "均量周期", min=10, max=40),
            StrategyParameter("vol_mult", "number", 2.0, "放量倍数", min=1.2, max=4.0, step=0.2),
            StrategyParameter("ma_period", "number", 10, "止损均线周期", min=5, max=30),
        ],
    )

    params = (
        ("price_period", 20),
        ("vol_period", 20),
        ("vol_mult", 2.0),
        ("ma_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.price_period)
        self.vol_sma = bt.indicators.SMA(self.data.volume, period=self.p.vol_period)
        self.ma = bt.indicators.SMA(self.data.close, period=self.p.ma_period)

    def next(self):
        close = self.data.close[0]
        vol_ratio = self.data.volume[0] / self.vol_sma[0] if self.vol_sma[0] > 0 else 0

        if not self.position:
            if close > self.highest[-1] and vol_ratio >= self.p.vol_mult:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"放量突破: 创{self.p.price_period}日新高, "
                        f"量比={vol_ratio:.1f}x",
                    )
        else:
            if close < self.ma[0]:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"跌破均线: 价格{close:.2f} < MA{self.p.ma_period}={self.ma[0]:.2f}",
                )
