"""
TRIX Strategy (三重指数平滑移动平均).

Category: Trend / Indicator
Markets: All

TRIX is a momentum oscillator that shows the percentage rate of change
of a triple exponentially smoothed moving average.

Signal logic:
  - BUY:  TRIX crosses above zero or its signal line
  - SELL: TRIX crosses below zero or its signal line

Parameters:
  - period:        TRIX period (default 15)
  - signal_period: Signal line period (default 9)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class TRIXStrategy(BaseStrategy):
    """TRIX triple exponential smoothing strategy."""

    STRATEGY_META = StrategyMeta(
        id="trix",
        name="三重指数平滑 (TRIX)",
        description=(
            "TRIX策略：TRIX上穿信号线买入，下穿信号线卖出。"
            "TRIX对三次EMA平滑后的变化率进行计算，有效过滤短期噪音。"
            "由Jack Hutson发明，是趋势确认的利器，滞后性较大但信号可靠。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 15, "TRIX周期", min=5, max=30),
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
                        "buy",
                        self.data.close[0],
                        f"TRIX金叉: TRIX={self.trix[0]:.4f} > "
                        f"Signal={self.signal[0]:.4f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"TRIX死叉: TRIX={self.trix[0]:.4f} < "
                    f"Signal={self.signal[0]:.4f}",
                )
