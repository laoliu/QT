"""
Adaptive Moving Average (KAMA) Strategy (考夫曼自适应均线).

Category: Trend / Advanced
Markets: All

KAMA (Kaufman Adaptive Moving Average) adjusts its smoothing based on market
noise — fast in trending markets, slow in choppy markets.

Signal logic:
  - BUY:  Price crosses above KAMA AND KAMA is rising
  - SELL: Price crosses below KAMA AND KAMA is falling

Parameters:
  - period:      KAMA efficiency ratio period (default 10)
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
        name="自适应均线KAMA (Adaptive MA)",
        description=(
            "考夫曼自适应均线策略：KAMA根据市场噪音自动调整灵敏度，"
            "趋势明确时跟踪快速均线，震荡时接近慢速均线。"
            "价格上穿KAMA且KAMA上行时买入，下穿且KAMA下行时卖出。"
            "由Perry Kaufman发明，被称为'智能均线'。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 10, "效率比周期", min=5, max=30),
            StrategyParameter("fast_period", "number", 2, "快速常数", min=2, max=5),
            StrategyParameter("slow_period", "number", 30, "慢速常数", min=20, max=60),
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

        if len(self) < 2:
            return

        kama_rising = kama_val > self.kama[-1]
        kama_falling = kama_val < self.kama[-1]

        if not self.position:
            if close > kama_val and kama_rising:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"KAMA上穿: 价格{close:.2f} > KAMA={kama_val:.2f}↑",
                    )
        else:
            if close < kama_val and kama_falling:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"KAMA下穿: 价格{close:.2f} < KAMA={kama_val:.2f}↓",
                )
