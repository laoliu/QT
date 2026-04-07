"""
Multi-Timeframe MA Strategy (多周期均线策略).

Category: Composite (Advanced)
Markets: All

Uses moving averages on multiple timeframe concepts (short, medium, long)
to confirm trend alignment.

Signal logic:
  - BUY:  Short MA > Medium MA > Long MA (all aligned bullish)
  - SELL: Short MA < Medium MA (trend breaking down)

Parameters:
  - short_period:  Short-term MA (default 5)
  - medium_period: Medium-term MA (default 20)
  - long_period:   Long-term MA (default 60)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MultiTimeframeMAStrategy(BaseStrategy):
    """Multi-timeframe moving average alignment strategy."""

    STRATEGY_META = StrategyMeta(
        id="multi-tf-ma",
        name="多周期均线 (Multi-Timeframe MA)",
        description=(
            "多周期均线策略：短期>中期>长期均线（多头排列）时买入，"
            "短期<中期均线时卖出。三条均线同时确认趋势方向。"
            "多头排列是趋势确立的经典信号，空头排列反之。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("short_period", "number", 5, "短期均线周期", min=3, max=15),
            StrategyParameter("medium_period", "number", 20, "中期均线周期", min=10, max=40),
            StrategyParameter("long_period", "number", 60, "长期均线周期", min=40, max=120),
        ],
    )

    params = (
        ("short_period", 5),
        ("medium_period", 20),
        ("long_period", 60),
    )

    def __init__(self):
        super().__init__()
        self.short_ma = bt.indicators.SMA(self.data.close, period=self.p.short_period)
        self.medium_ma = bt.indicators.SMA(self.data.close, period=self.p.medium_period)
        self.long_ma = bt.indicators.SMA(self.data.close, period=self.p.long_period)

    def _is_bullish_aligned(self) -> bool:
        return self.short_ma[0] > self.medium_ma[0] > self.long_ma[0]

    def _was_not_aligned(self) -> bool:
        return not (self.short_ma[-1] > self.medium_ma[-1] > self.long_ma[-1])

    def next(self):
        short = self.short_ma[0]
        medium = self.medium_ma[0]
        long_val = self.long_ma[0]

        if not self.position:
            if self._is_bullish_aligned() and self._was_not_aligned():
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"多头排列买入: MA{self.p.short_period}={short:.2f} > "
                        f"MA{self.p.medium_period}={medium:.2f} > "
                        f"MA{self.p.long_period}={long_val:.2f}",
                    )
        else:
            if short < medium:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"排列破坏卖出: MA{self.p.short_period}={short:.2f} < "
                    f"MA{self.p.medium_period}={medium:.2f}",
                )
