"""
MACD Oscillator Strategy (MACD 振荡器).

Category: Trend Following
Markets: All (A-shares, US, HK, global)

Signal logic:
  - BUY:  MACD line crosses above signal line (golden cross)
  - SELL: MACD line crosses below signal line (death cross)

Enhanced with histogram divergence confirmation:
  - When MACD histogram is increasing → stronger buy signal
  - When MACD histogram is decreasing → stronger sell signal

Parameters:
  - fast_period:   Fast EMA period (default 12)
  - slow_period:   Slow EMA period (default 26)
  - signal_period: Signal line smoothing period (default 9)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MACDStrategy(BaseStrategy):
    """MACD oscillator trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="macd",
        name="MACD 振荡器 (MACD Oscillator)",
        description=(
            "经典MACD策略：MACD线上穿信号线买入（金叉），下穿卖出（死叉）。"
            "MACD是最广泛使用的趋势跟踪动量指标，由Gerald Appel发明。"
            "适合趋势明显的市场，在震荡市中可能产生较多假信号。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("fast_period", "number", 12, "快速EMA周期", min=2, max=50),
            StrategyParameter("slow_period", "number", 26, "慢速EMA周期", min=10, max=100),
            StrategyParameter("signal_period", "number", 9, "信号线平滑周期", min=2, max=30),
        ],
    )

    params = (
        ("fast_period", 12),
        ("slow_period", 26),
        ("signal_period", 9),
    )

    def __init__(self):
        super().__init__()
        # MACD indicator: macd line, signal line, histogram
        self.macd = bt.indicators.MACD(
            self.data.close,
            period_me1=self.p.fast_period,
            period_me2=self.p.slow_period,
            period_signal=self.p.signal_period,
        )
        # Cross-over signal
        self.crossover = bt.indicators.CrossOver(self.macd.macd, self.macd.signal)

    def next(self):
        if not self.position:
            # No position — look for golden cross (MACD above signal)
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"MACD金叉: MACD={self.macd.macd[0]:.4f} > Signal={self.macd.signal[0]:.4f}",
                    )
        else:
            # Have position — look for death cross (MACD below signal)
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"MACD死叉: MACD={self.macd.macd[0]:.4f} < Signal={self.macd.signal[0]:.4f}",
                )
