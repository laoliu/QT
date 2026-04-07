"""
Triple EMA Crossover Strategy (三重EMA交叉).

Category: Trend Following (CTA)
Markets: All

Uses three EMAs (fast/medium/slow) for trend confirmation.

Signal logic:
  - BUY:  Fast > Medium > Slow (aligned uptrend)
  - SELL: Fast < Medium < Slow (aligned downtrend)

Parameters:
  - fast_period:   Fast EMA period (default 5)
  - medium_period: Medium EMA period (default 13)
  - slow_period:   Slow EMA period (default 34)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class EMATripleStrategy(BaseStrategy):
    """Triple EMA crossover trend-following strategy."""

    STRATEGY_META = StrategyMeta(
        id="ema-triple",
        name="三重EMA交叉 (Triple EMA)",
        description=(
            "三重EMA交叉策略：当快速EMA > 中速EMA > 慢速EMA形成多头排列时买入，"
            "空头排列时卖出。通过三重确认减少假信号，适合中长线趋势跟踪。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("fast_period", "number", 5, "快速EMA周期", min=2, max=20),
            StrategyParameter("medium_period", "number", 13, "中速EMA周期", min=8, max=40),
            StrategyParameter("slow_period", "number", 34, "慢速EMA周期", min=20, max=120),
        ],
    )

    params = (
        ("fast_period", 5),
        ("medium_period", 13),
        ("slow_period", 34),
    )

    def __init__(self):
        super().__init__()
        self.ema_fast = bt.indicators.EMA(self.data.close, period=self.p.fast_period)
        self.ema_med = bt.indicators.EMA(self.data.close, period=self.p.medium_period)
        self.ema_slow = bt.indicators.EMA(self.data.close, period=self.p.slow_period)

    def next(self):
        fast = self.ema_fast[0]
        med = self.ema_med[0]
        slow = self.ema_slow[0]

        if not self.position:
            if fast > med > slow:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"多头排列: EMA{self.p.fast_period}={fast:.2f} > "
                        f"EMA{self.p.medium_period}={med:.2f} > EMA{self.p.slow_period}={slow:.2f}",
                    )
        else:
            if fast < med < slow:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"空头排列: EMA{self.p.fast_period}={fast:.2f} < "
                    f"EMA{self.p.medium_period}={med:.2f} < EMA{self.p.slow_period}={slow:.2f}",
                )
