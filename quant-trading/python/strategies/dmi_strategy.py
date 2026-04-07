"""
DMI (Directional Movement Index) Strategy (方向运动指标).

Category: Trend Following (CTA)
Markets: All

Uses +DI/-DI crossover with ADX filter.

Signal logic:
  - BUY:  +DI crosses above -DI AND ADX rising
  - SELL: -DI crosses above +DI OR ADX falling from high level

Parameters:
  - period:         DMI period (default 14)
  - adx_threshold:  Min ADX to confirm trend (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DMIStrategy(BaseStrategy):
    """Directional Movement Index strategy."""

    STRATEGY_META = StrategyMeta(
        id="dmi",
        name="方向运动DMI (DMI)",
        description=(
            "DMI方向运动策略：+DI上穿-DI且ADX上升确认趋势时买入，"
            "-DI上穿+DI时卖出。结合ADX过滤震荡市假信号。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 14, "DMI周期", min=7, max=30),
            StrategyParameter("adx_threshold", "number", 20, "ADX确认阈值", min=15, max=35),
        ],
    )

    params = (
        ("period", 14),
        ("adx_threshold", 20),
    )

    def __init__(self):
        super().__init__()
        self.plus_di = bt.indicators.PlusDirectionalIndicator(
            self.data, period=self.p.period
        )
        self.minus_di = bt.indicators.MinusDirectionalIndicator(
            self.data, period=self.p.period
        )
        self.adx = bt.indicators.AverageDirectionalMovementIndex(
            self.data, period=self.p.period
        )
        self.di_cross = bt.indicators.CrossOver(self.plus_di, self.minus_di)

    def next(self):
        adx_val = self.adx[0]

        if not self.position:
            if self.di_cross > 0 and adx_val > self.p.adx_threshold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"DMI金叉: +DI={self.plus_di[0]:.1f} > -DI={self.minus_di[0]:.1f}, ADX={adx_val:.1f}",
                    )
        else:
            if self.di_cross < 0:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"DMI死叉: -DI={self.minus_di[0]:.1f} > +DI={self.plus_di[0]:.1f}",
                )
