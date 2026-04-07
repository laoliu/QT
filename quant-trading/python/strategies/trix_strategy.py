"""
TRIX Strategy (三重指数平滑移动平均指标).

Category: Trend Following
Markets: All

TRIX is the 1-period percentage change of a triple-smoothed EMA.
It filters out insignificant short-term movements, making it
an excellent trend-following indicator.

Signal logic:
  - BUY:  TRIX crosses above signal line (or above zero)
  - SELL: TRIX crosses below signal line (or below zero)

Parameters:
  - period:        TRIX EMA period (default 12)
  - signal_period: Signal line period (default 9)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class TRIXStrategy(BaseStrategy):
    """TRIX triple-smoothed EMA trend strategy."""

    STRATEGY_META = StrategyMeta(
        id="trix",
        name="TRIX 三重指数平滑 (Triple EMA)",
        description=(
            "TRIX策略：TRIX线上穿信号线时买入，下穿信号线时卖出。"
            "TRIX是三次指数平滑移动平均线的1周期变化率，"
            "通过三重平滑过滤短期噪音，能有效捕捉中长期趋势。"
            "信号稳定但有一定滞后性，适合趋势跟踪交易。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 12, "TRIX周期", min=5, max=30),
            StrategyParameter("signal_period", "number", 9, "信号线周期", min=3, max=20),
        ],
    )

    params = (
        ("period", 12),
        ("signal_period", 9),
    )

    def __init__(self):
        super().__init__()
        self.trix = bt.indicators.Trix(self.data.close, period=self.p.period)
        self.signal = bt.indicators.EMA(self.trix, period=self.p.signal_period)
        self.crossover = bt.indicators.CrossOver(self.trix, self.signal)

    def next(self):
        trix_val = self.trix[0]
        sig_val = self.signal[0]

        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"TRIX金叉: TRIX={trix_val:.4f} > Signal={sig_val:.4f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"TRIX死叉: TRIX={trix_val:.4f} < Signal={sig_val:.4f}",
                )
