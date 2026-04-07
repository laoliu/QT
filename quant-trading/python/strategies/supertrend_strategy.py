"""
SuperTrend Strategy (超级趋势).

Category: Trend Following (CTA)
Markets: All

SuperTrend is an ATR-based trailing stop that flips direction when price
crosses the band, providing clear trend direction signals.

Signal logic:
  - BUY:  Price crosses above the SuperTrend line (trend flips bullish)
  - SELL: Price crosses below the SuperTrend line (trend flips bearish)

Parameters:
  - atr_period:   ATR period (default 10)
  - multiplier:   ATR multiplier for band distance (default 3.0)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _SuperTrendIndicator(bt.Indicator):
    """Custom SuperTrend indicator."""

    lines = ("supertrend", "direction")

    params = (
        ("atr_period", 10),
        ("multiplier", 3.0),
    )

    def __init__(self):
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self._upper = []
        self._lower = []
        self._dir = 1  # 1 = up, -1 = down

    def next(self):
        hl2 = (self.data.high[0] + self.data.low[0]) / 2.0
        atr_val = self.atr[0]

        upper = hl2 + self.p.multiplier * atr_val
        lower = hl2 - self.p.multiplier * atr_val

        if len(self._upper) > 0:
            prev_upper = self._upper[-1]
            prev_lower = self._lower[-1]
            if upper < prev_upper or self.data.close[-1] > prev_upper:
                upper = upper
            else:
                upper = prev_upper
            if lower > prev_lower or self.data.close[-1] < prev_lower:
                lower = lower
            else:
                lower = prev_lower

        self._upper.append(upper)
        self._lower.append(lower)

        if self._dir == 1:
            if self.data.close[0] < lower:
                self._dir = -1
        else:
            if self.data.close[0] > upper:
                self._dir = 1

        self.lines.direction[0] = self._dir
        self.lines.supertrend[0] = lower if self._dir == 1 else upper


class SuperTrendStrategy(BaseStrategy):
    """SuperTrend ATR-based trend-following strategy."""

    STRATEGY_META = StrategyMeta(
        id="supertrend",
        name="超级趋势 (SuperTrend)",
        description=(
            "超级趋势策略：基于ATR的动态趋势跟踪线，价格突破上方线转为多头买入，"
            "跌破下方线转为空头卖出。信号清晰明确，是CTA策略中常用的趋势指标。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("atr_period", "number", 10, "ATR周期", min=5, max=30),
            StrategyParameter("multiplier", "number", 3.0, "ATR倍数", min=1.0, max=5.0, step=0.5),
        ],
    )

    params = (
        ("atr_period", 10),
        ("multiplier", 3.0),
    )

    def __init__(self):
        super().__init__()
        self.st = _SuperTrendIndicator(
            self.data,
            atr_period=self.p.atr_period,
            multiplier=self.p.multiplier,
        )
        self._prev_dir = 0

    def next(self):
        curr_dir = self.st.direction[0]
        st_val = self.st.supertrend[0]

        if not self.position:
            if curr_dir == 1 and self._prev_dir == -1:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"超级趋势翻多: SuperTrend={st_val:.2f}",
                    )
        else:
            if curr_dir == -1 and self._prev_dir == 1:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"超级趋势翻空: SuperTrend={st_val:.2f}",
                )

        self._prev_dir = curr_dir
