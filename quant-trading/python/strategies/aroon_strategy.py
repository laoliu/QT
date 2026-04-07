"""
Aroon Indicator Strategy (阿隆指标).

Category: Trend Following
Markets: All

The Aroon indicator, developed by Tushar Chande, measures the time
elapsed since the highest high and lowest low over a given period.

Signal logic:
  - BUY:  Aroon Up crosses above 70 AND Aroon Down below 30 (strong uptrend)
  - SELL: Aroon Down crosses above 70 AND Aroon Up below 30 (strong downtrend)

Parameters:
  - period:          Aroon calculation period (default 25)
  - upper_threshold: Upper threshold for trend confirmation (default 70)
  - lower_threshold: Lower threshold for trend confirmation (default 30)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _AroonIndicator(bt.Indicator):
    """Custom Aroon indicator with Up, Down, and Oscillator lines."""

    lines = ("aroon_up", "aroon_down", "aroon_osc")

    params = (("period", 25),)

    def __init__(self):
        super().__init__()
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.period + 1)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.period + 1)
        self.addminperiod(self.p.period + 1)

    def next(self):
        period = self.p.period
        # Find bars since highest high within the lookback window
        bars_since_high = 0
        high_val = self.highest[0]
        for i in range(period + 1):
            if self.data.high[-i] == high_val:
                bars_since_high = i
                break

        # Find bars since lowest low within the lookback window
        bars_since_low = 0
        low_val = self.lowest[0]
        for i in range(period + 1):
            if self.data.low[-i] == low_val:
                bars_since_low = i
                break

        self.lines.aroon_up[0] = ((period - bars_since_high) / period) * 100.0
        self.lines.aroon_down[0] = ((period - bars_since_low) / period) * 100.0
        self.lines.aroon_osc[0] = self.lines.aroon_up[0] - self.lines.aroon_down[0]


class AroonStrategy(BaseStrategy):
    """Aroon indicator trend trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="aroon",
        name="阿隆指标 (Aroon)",
        description=(
            "阿隆指标策略：Aroon Up上穿70且Aroon Down低于30时买入，反之卖出。"
            "由Tushar Chande发明，用于识别趋势的开始和强度。"
            "Aroon Up衡量距最高价的周期数，Aroon Down衡量距最低价的周期数。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 25, "阿隆计算周期", min=10, max=50),
            StrategyParameter("upper_threshold", "number", 70, "上阈值（趋势确认）", min=60, max=90),
            StrategyParameter("lower_threshold", "number", 30, "下阈值（趋势确认）", min=10, max=40),
        ],
    )

    params = (
        ("period", 25),
        ("upper_threshold", 70),
        ("lower_threshold", 30),
    )

    def __init__(self):
        super().__init__()
        self.aroon = _AroonIndicator(self.data, period=self.p.period)
        self._prev_aroon_up = None
        self._prev_aroon_down = None

    def next(self):
        aroon_up = self.aroon.aroon_up[0]
        aroon_down = self.aroon.aroon_down[0]
        prev_up = self._prev_aroon_up
        prev_down = self._prev_aroon_down

        # Update previous values for next iteration
        self._prev_aroon_up = aroon_up
        self._prev_aroon_down = aroon_down

        if prev_up is None or prev_down is None:
            return

        if not self.position:
            # BUY: Aroon Up crosses above upper_threshold AND Aroon Down below lower_threshold
            if (
                prev_up <= self.p.upper_threshold
                and aroon_up > self.p.upper_threshold
                and aroon_down < self.p.lower_threshold
            ):
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"阿隆买入: Up={aroon_up:.1f}上穿{self.p.upper_threshold}, "
                        f"Down={aroon_down:.1f}<{self.p.lower_threshold}",
                    )
        else:
            # SELL: Aroon Down crosses above upper_threshold AND Aroon Up below lower_threshold
            if (
                prev_down <= self.p.upper_threshold
                and aroon_down > self.p.upper_threshold
                and aroon_up < self.p.lower_threshold
            ):
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"阿隆卖出: Down={aroon_down:.1f}上穿{self.p.upper_threshold}, "
                    f"Up={aroon_up:.1f}<{self.p.lower_threshold}",
                )
