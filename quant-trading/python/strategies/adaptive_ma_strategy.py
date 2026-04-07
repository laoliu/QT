"""
Adaptive Moving Average Strategy (自适应均线).

Category: Composite (Advanced)
Markets: All

Uses Kaufman's Adaptive Moving Average (KAMA) which adjusts smoothing
based on market noise/efficiency ratio.

Signal logic:
  - BUY:  Price crosses above KAMA + KAMA is trending up
  - SELL: Price crosses below KAMA

Parameters:
  - period:      Efficiency ratio period (default 10)
  - fast_period: Fast EMA constant (default 2)
  - slow_period: Slow EMA constant (default 30)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class AdaptiveMAStrategy(BaseStrategy):
    """Kaufman Adaptive Moving Average strategy."""

    STRATEGY_META = StrategyMeta(
        id="adaptive-ma",
        name="自适应均线 (Adaptive MA / KAMA)",
        description=(
            "自适应均线策略：价格上穿KAMA且KAMA上行时买入，下穿KAMA时卖出。"
            "KAMA由Perry Kaufman发明，根据市场效率比率自动调整平滑系数。"
            "趋势明确时KAMA快速跟随，震荡时KAMA缓慢变化减少假信号。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("period", "number", 10, "效率比率周期", min=5, max=30),
            StrategyParameter("fast_period", "number", 2, "快速EMA常数", min=2, max=5),
            StrategyParameter("slow_period", "number", 30, "慢速EMA常数", min=20, max=60),
        ],
    )

    params = (
        ("period", 10),
        ("fast_period", 2),
        ("slow_period", 30),
    )

    def __init__(self):
        super().__init__()
        self.kama = bt.indicators.KAMA(
            self.data.close,
            period=self.p.period,
            fast=self.p.fast_period,
            slow=self.p.slow_period,
        )

    def next(self):
        close = self.data.close[0]
        kama_val = self.kama[0]
        kama_prev = self.kama[-1] if len(self.kama) > 1 else kama_val
        kama_rising = kama_val > kama_prev

        if not self.position:
            if close > kama_val and kama_rising:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"KAMA买入: 价格{close:.2f} > KAMA={kama_val:.2f} (上行)",
                    )
        else:
            if close < kama_val:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"KAMA卖出: 价格{close:.2f} < KAMA={kama_val:.2f}",
                )
