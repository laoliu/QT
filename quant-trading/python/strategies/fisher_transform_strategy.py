"""
Fisher Transform Strategy (费雪变换).

Category: Oscillator / Advanced
Markets: All

The Fisher Transform converts prices to a Gaussian normal distribution,
making turning points sharper and easier to identify.

Signal logic:
  - BUY:  Fisher Transform crosses above its trigger line
  - SELL: Fisher Transform crosses below its trigger line

Parameters:
  - period: Fisher Transform lookback (default 10)
"""

from __future__ import annotations

import math

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _FisherTransform(bt.Indicator):
    """Fisher Transform indicator."""

    lines = ("fisher", "trigger")
    params = (("period", 10),)

    def __init__(self):
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.period)
        self._prev_value = 0.0
        self._prev_fisher = 0.0

    def next(self):
        hl_range = self.highest[0] - self.lowest[0]
        if hl_range > 0:
            mid = (self.data.high[0] + self.data.low[0]) / 2.0
            value = 2.0 * ((mid - self.lowest[0]) / hl_range - 0.5)
        else:
            value = 0.0

        # Clamp to avoid math domain error
        value = max(-0.999, min(0.999, 0.33 * value + 0.67 * self._prev_value))

        fisher = 0.5 * math.log((1 + value) / (1 - value)) + 0.5 * self._prev_fisher

        self.lines.trigger[0] = self._prev_fisher
        self.lines.fisher[0] = fisher

        self._prev_value = value
        self._prev_fisher = fisher


class FisherTransformStrategy(BaseStrategy):
    """Fisher Transform turning-point strategy."""

    STRATEGY_META = StrategyMeta(
        id="fisher-transform",
        name="费雪变换 (Fisher Transform)",
        description=(
            "费雪变换策略：将价格转换为高斯正态分布，使转折点更加尖锐清晰。"
            "费雪线上穿触发线买入，下穿卖出。对趋势转折点非常敏感。"
            "由John Ehlers发明。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 10, "费雪变换周期", min=5, max=30),
        ],
    )

    params = (
        ("period", 10),
    )

    def __init__(self):
        super().__init__()
        self.fisher = _FisherTransform(self.data, period=self.p.period)

    def next(self):
        f_val = self.fisher.fisher[0]
        t_val = self.fisher.trigger[0]

        if not self.position:
            if f_val > t_val and (len(self) < 2 or self.fisher.fisher[-1] <= self.fisher.trigger[-1]):
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"费雪金叉: Fisher={f_val:.3f} > Trigger={t_val:.3f}",
                    )
        else:
            if f_val < t_val and self.fisher.fisher[-1] >= self.fisher.trigger[-1]:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"费雪死叉: Fisher={f_val:.3f} < Trigger={t_val:.3f}",
                )
