"""
DEMA Crossover Strategy (DEMA双重指数均线交叉).

Category: Trend Following
Markets: All

Signal logic:
  - BUY:  Fast DEMA crosses above Slow DEMA (golden cross)
  - SELL: Fast DEMA crosses below Slow DEMA (death cross)

DEMA = 2*EMA(N) - EMA(EMA(N))

Parameters:
  - fast_period: Fast DEMA period (default 10)
  - slow_period: Slow DEMA period (default 30)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DEMACrossStrategy(BaseStrategy):
    """DEMA crossover trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="dema-cross",
        name="DEMA双重指数均线 (DEMA Crossover)",
        description=(
            "DEMA双重指数均线交叉策略：快速DEMA上穿慢速DEMA买入（金叉），下穿卖出（死叉）。"
            "DEMA = 2*EMA(N) - EMA(EMA(N))，通过双重平滑减少滞后，"
            "比普通EMA更快响应价格变化。适合捕捉中短期趋势，减少信号延迟。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("fast_period", "number", 10, "快速DEMA周期", min=3, max=50),
            StrategyParameter("slow_period", "number", 30, "慢速DEMA周期", min=10, max=120),
        ],
    )

    params = (
        ("fast_period", 10),
        ("slow_period", 30),
    )

    def __init__(self):
        super().__init__()
        self.fast_dema = bt.indicators.DEMA(self.data.close, period=self.p.fast_period)
        self.slow_dema = bt.indicators.DEMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_dema, self.slow_dema)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"DEMA金叉: DEMA{self.p.fast_period}={self.fast_dema[0]:.2f} > "
                        f"DEMA{self.p.slow_period}={self.slow_dema[0]:.2f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"DEMA死叉: DEMA{self.p.fast_period}={self.fast_dema[0]:.2f} < "
                    f"DEMA{self.p.slow_period}={self.slow_dema[0]:.2f}",
                )
