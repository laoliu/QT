"""
EMA Crossover Strategy (EMA均线交叉).

Category: Trend Following
Markets: All

Signal logic:
  - BUY:  Fast EMA crosses above Slow EMA (golden cross)
  - SELL: Fast EMA crosses below Slow EMA (death cross)

Parameters:
  - fast_period: Fast EMA period (default 10)
  - slow_period: Slow EMA period (default 30)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class EMACrossStrategy(BaseStrategy):
    """EMA crossover trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="ema-cross",
        name="EMA均线交叉 (EMA Crossover)",
        description=(
            "EMA双均线交叉策略：快速EMA上穿慢速EMA买入（金叉），下穿卖出（死叉）。"
            "指数移动平均线对近期价格赋予更大权重，比SMA对趋势变化更敏感。"
            "适合中短期趋势交易，震荡市中可能产生较多假信号。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("fast_period", "number", 10, "快速EMA周期", min=3, max=50),
            StrategyParameter("slow_period", "number", 30, "慢速EMA周期", min=10, max=120),
        ],
    )

    params = (
        ("fast_period", 10),
        ("slow_period", 30),
    )

    def __init__(self):
        super().__init__()
        self.fast_ema = bt.indicators.EMA(self.data.close, period=self.p.fast_period)
        self.slow_ema = bt.indicators.EMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ema, self.slow_ema)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"EMA金叉: EMA{self.p.fast_period}={self.fast_ema[0]:.2f} > "
                        f"EMA{self.p.slow_period}={self.slow_ema[0]:.2f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"EMA死叉: EMA{self.p.fast_period}={self.fast_ema[0]:.2f} < "
                    f"EMA{self.p.slow_period}={self.slow_ema[0]:.2f}",
                )
