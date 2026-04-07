"""
TRIX (Triple Exponential Average) Strategy (三重指数平滑).

Category: Trend / Indicator
Markets: All

TRIX is the rate-of-change of a triple-smoothed EMA, filtering out noise.

Signal logic:
  - BUY:  TRIX crosses above its signal line
  - SELL: TRIX crosses below its signal line

Parameters:
  - period:        TRIX EMA period (default 15)
  - signal_period: Signal line SMA period (default 9)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class TRIXStrategy(BaseStrategy):
    """TRIX triple-smoothed momentum strategy."""

    STRATEGY_META = StrategyMeta(
        id="trix",
        name="三重指数TRIX (TRIX)",
        description=(
            "TRIX策略：三重EMA的变化率指标，上穿信号线买入，下穿卖出。"
            "经过三次平滑后极大过滤了短期噪音，适合捕捉中长期趋势。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 15, "TRIX周期", min=8, max=30),
            StrategyParameter("signal_period", "number", 9, "信号线周期", min=3, max=15),
        ],
    )

    params = (
        ("period", 15),
        ("signal_period", 9),
    )

    def __init__(self):
        super().__init__()
        self.trix = bt.indicators.Trix(self.data.close, period=self.p.period)
        self.signal = bt.indicators.SMA(self.trix, period=self.p.signal_period)
        self.crossover = bt.indicators.CrossOver(self.trix, self.signal)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"TRIX金叉: TRIX={self.trix[0]:.4f} > Signal={self.signal[0]:.4f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"TRIX死叉: TRIX={self.trix[0]:.4f} < Signal={self.signal[0]:.4f}",
                )
