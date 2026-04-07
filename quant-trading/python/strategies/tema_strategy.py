"""
TEMA Crossover Strategy (TEMA三重指数均线交叉).

Category: Trend Following
Markets: All

Signal logic:
  - BUY:  Fast TEMA crosses above Slow TEMA (golden cross)
  - SELL: Fast TEMA crosses below Slow TEMA (death cross)

Parameters:
  - fast_period: Fast TEMA period (default 10)
  - slow_period: Slow TEMA period (default 30)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class TEMACrossStrategy(BaseStrategy):
    """TEMA crossover trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="tema-cross",
        name="TEMA三重指数均线 (TEMA Crossover)",
        description=(
            "TEMA双均线交叉策略：快速TEMA上穿慢速TEMA买入（金叉），下穿卖出（死叉）。"
            "三重指数移动平均线通过三次平滑消除滞后，比EMA对趋势变化更敏感。"
            "适合中短期趋势交易，能更早捕捉趋势反转信号。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("fast_period", "number", 10, "快速TEMA周期", min=3, max=50),
            StrategyParameter("slow_period", "number", 30, "慢速TEMA周期", min=10, max=120),
        ],
    )

    params = (
        ("fast_period", 10),
        ("slow_period", 30),
    )

    def __init__(self):
        super().__init__()
        self.fast_tema = bt.indicators.TEMA(self.data.close, period=self.p.fast_period)
        self.slow_tema = bt.indicators.TEMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_tema, self.slow_tema)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"TEMA金叉: TEMA{self.p.fast_period}={self.fast_tema[0]:.2f} > "
                        f"TEMA{self.p.slow_period}={self.slow_tema[0]:.2f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"TEMA死叉: TEMA{self.p.fast_period}={self.fast_tema[0]:.2f} < "
                    f"TEMA{self.p.slow_period}={self.slow_tema[0]:.2f}",
                )
