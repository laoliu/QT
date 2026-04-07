"""
Dual Moving Average Crossover Strategy (双均线交叉).

Category: Trend Following (CTA)
Markets: All

Signal logic:
  - BUY:  Short MA crosses above Long MA (golden cross)
  - SELL: Short MA crosses below Long MA (death cross)

Parameters:
  - short_period: Short MA period (default 5)
  - long_period:  Long MA period (default 20)
  - ma_type:      MA type: 0=SMA, 1=EMA (default 1)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DualMAStrategy(BaseStrategy):
    """Dual Moving Average Crossover strategy."""

    STRATEGY_META = StrategyMeta(
        id="dual-ma",
        name="双均线交叉 (Dual MA Crossover)",
        description=(
            "双均线交叉策略：短期均线上穿长期均线时买入（金叉），下穿时卖出（死叉）。"
            "最基础的趋势跟踪策略，适合趋势明确的市场，震荡市中易产生假信号。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("short_period", "number", 5, "短期均线周期", min=2, max=30),
            StrategyParameter("long_period", "number", 20, "长期均线周期", min=10, max=120),
            StrategyParameter("ma_type", "number", 1, "均线类型(0=SMA,1=EMA)", min=0, max=1),
        ],
    )

    params = (
        ("short_period", 5),
        ("long_period", 20),
        ("ma_type", 1),
    )

    def __init__(self):
        super().__init__()
        ma_cls = bt.indicators.EMA if self.p.ma_type == 1 else bt.indicators.SMA
        self.short_ma = ma_cls(self.data.close, period=self.p.short_period)
        self.long_ma = ma_cls(self.data.close, period=self.p.long_period)
        self.crossover = bt.indicators.CrossOver(self.short_ma, self.long_ma)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"均线金叉: MA{self.p.short_period}={self.short_ma[0]:.2f} > "
                        f"MA{self.p.long_period}={self.long_ma[0]:.2f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"均线死叉: MA{self.p.short_period}={self.short_ma[0]:.2f} < "
                    f"MA{self.p.long_period}={self.long_ma[0]:.2f}",
                )
