"""
Hammer / Hanging Man Strategy (锤子线/上吊线).

Category: Pattern / Candlestick
Markets: All

Detects hammer (bullish reversal at bottom) and hanging man (bearish reversal at top).

Signal logic:
  - BUY:  Hammer at bottom of downtrend (long lower shadow, small body at top)
  - SELL: Hanging man at top of uptrend (long lower shadow, small body at top after uptrend)

Parameters:
  - shadow_ratio: Min lower shadow to body ratio (default 2.0)
  - trend_period: Trend identification period (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class HammerStrategy(BaseStrategy):
    """Hammer/Hanging Man candlestick reversal strategy."""

    STRATEGY_META = StrategyMeta(
        id="hammer",
        name="锤子线/上吊线 (Hammer)",
        description=(
            "锤子线策略：下降趋势中出现长下影线短实体的锤子线买入（底部反转），"
            "上升趋势中出现类似形态的上吊线卖出（顶部反转）。"
            "经典的单K线反转形态。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("shadow_ratio", "number", 2.0, "下影线/实体比", min=1.5, max=4.0, step=0.5),
            StrategyParameter("trend_period", "number", 10, "趋势周期", min=5, max=30),
        ],
    )

    params = (
        ("shadow_ratio", 2.0),
        ("trend_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)

    def _is_hammer_shape(self):
        high = self.data.high[0]
        low = self.data.low[0]
        open_ = self.data.open[0]
        close = self.data.close[0]

        body = abs(close - open_)
        if body <= 0:
            body = 0.001
        body_top = max(open_, close)
        body_bot = min(open_, close)

        lower_shadow = body_bot - low
        upper_shadow = high - body_top

        return (lower_shadow >= self.p.shadow_ratio * body
                and upper_shadow < body)

    def next(self):
        if len(self) < self.p.trend_period + 1:
            return

        is_hammer = self._is_hammer_shape()

        if not self.position:
            # Hammer: hammer shape in downtrend
            if is_hammer and self.data.close[0] < self.sma[0]:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"锤子线买入: 长下影线反转, 均线={self.sma[0]:.2f}",
                    )
        else:
            # Hanging man: hammer shape in uptrend
            if is_hammer and self.data.close[0] > self.sma[0]:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"上吊线卖出: 趋势顶部反转信号",
                )
