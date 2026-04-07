"""
Aroon Oscillator Strategy (阿隆指标).

Category: Trend Following (CTA)
Markets: All

Aroon identifies the beginning of a new trend and measures trend strength.

Signal logic:
  - BUY:  Aroon Up > 70 AND Aroon Down < 30
  - SELL: Aroon Down > 70 AND Aroon Up < 30

Parameters:
  - period:          Aroon lookback period (default 25)
  - upper_threshold: Threshold for strong trend (default 70)
  - lower_threshold: Threshold for weak trend (default 30)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _AroonIndicator(bt.Indicator):
    """Custom Aroon Up/Down indicator."""

    lines = ("aroon_up", "aroon_down")
    params = (("period", 25),)

    def __init__(self):
        self.addminperiod(self.p.period + 1)

    def next(self):
        period = self.p.period
        highs = [self.data.high[-i] for i in range(period + 1)]
        lows = [self.data.low[-i] for i in range(period + 1)]

        days_since_high = highs.index(max(highs))
        days_since_low = lows.index(min(lows))

        self.lines.aroon_up[0] = ((period - days_since_high) / period) * 100
        self.lines.aroon_down[0] = ((period - days_since_low) / period) * 100


class AroonStrategy(BaseStrategy):
    """Aroon oscillator trend-identification strategy."""

    STRATEGY_META = StrategyMeta(
        id="aroon",
        name="阿隆指标 (Aroon)",
        description=(
            "阿隆指标策略：Aroon Up>70且Aroon Down<30时确认上升趋势买入，"
            "Aroon Down>70且Aroon Up<30时确认下降趋势卖出。"
            "由Tushar Chande发明，擅长识别新趋势的起点。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 25, "阿隆周期", min=10, max=50),
            StrategyParameter("upper_threshold", "number", 70, "强势阈值", min=60, max=90),
            StrategyParameter("lower_threshold", "number", 30, "弱势阈值", min=10, max=40),
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

    def next(self):
        up = self.aroon.aroon_up[0]
        down = self.aroon.aroon_down[0]

        if not self.position:
            if up > self.p.upper_threshold and down < self.p.lower_threshold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"阿隆买入: Up={up:.0f} > {self.p.upper_threshold}, "
                        f"Down={down:.0f} < {self.p.lower_threshold}",
                    )
        else:
            if down > self.p.upper_threshold and up < self.p.lower_threshold:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"阿隆卖出: Down={down:.0f}, Up={up:.0f}",
                )
