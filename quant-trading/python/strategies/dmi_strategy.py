"""
DMI (Directional Movement Index) Strategy (趋向指标).

Category: Trend
Markets: All

Uses +DI/-DI direction with ADX filter but focuses on DI crossover signals
rather than ADX thresholds.

Signal logic:
  - BUY:  +DI crosses above -DI (bullish directional movement)
  - SELL: -DI crosses above +DI (bearish directional movement)

Parameters:
  - period: DI calculation period (default 14)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DMIStrategy(BaseStrategy):
    """Directional Movement Index strategy."""

    STRATEGY_META = StrategyMeta(
        id="dmi",
        name="趋向指标 (DMI)",
        description=(
            "DMI趋向指标策略：+DI上穿-DI买入，-DI上穿+DI卖出。"
            "由Welles Wilder发明，通过方向运动指标衡量趋势方向。"
            "+DI衡量上升动力，-DI衡量下降动力，交叉产生买卖信号。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 14, "DI计算周期", min=7, max=30),
        ],
    )

    params = (("period", 14),)

    def __init__(self):
        super().__init__()
        self.plus_di = bt.indicators.PlusDI(self.data, period=self.p.period)
        self.minus_di = bt.indicators.MinusDI(self.data, period=self.p.period)
        self.crossover = bt.indicators.CrossOver(self.plus_di, self.minus_di)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"DMI金叉: +DI={self.plus_di[0]:.1f} > "
                        f"-DI={self.minus_di[0]:.1f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"DMI死叉: +DI={self.plus_di[0]:.1f} < "
                    f"-DI={self.minus_di[0]:.1f}",
                )
