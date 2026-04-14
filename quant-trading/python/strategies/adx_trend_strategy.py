"""
ADX Trend Strength Strategy (ADX趋势强度).

Category: Trend Following
Markets: All

Uses the Average Directional Index (ADX) to confirm trend strength,
combined with +DI/-DI crossovers for direction.

Signal logic:
  - BUY:  +DI crosses above -DI AND ADX > threshold (strong uptrend)
  - SELL: -DI crosses above +DI OR ADX drops below threshold

Parameters:
  - period:        ADX/DI calculation period (default 14)
  - adx_threshold: Minimum ADX value for trend confirmation (default 25)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ADXTrendStrategy(BaseStrategy):
    """ADX trend strength trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="adx-trend",
        name="ADX趋势强度 (ADX Trend)",
        description=(
            "ADX趋势强度策略：+DI上穿-DI且ADX大于阈值时买入，反之卖出。"
            "ADX由Welles Wilder发明，用于衡量趋势强度（不区分方向）。"
            "ADX>25表示趋势明确，<20表示震荡。结合DI方向确定多空。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 14, "ADX/DI计算周期", min=7, max=30),
            StrategyParameter("adx_threshold", "number", 25, "ADX趋势确认阈值", min=15, max=40),
        ],
    )

    params = (
        ("period", 14),
        ("adx_threshold", 25),
    )

    def __init__(self):
        super().__init__()
        self.adx = bt.indicators.ADX(self.data, period=self.p.period)
        self.plus_di = bt.indicators.PlusDI(self.data, period=self.p.period)
        self.minus_di = bt.indicators.MinusDI(self.data, period=self.p.period)
        self.di_cross = bt.indicators.CrossOver(self.plus_di, self.minus_di)

    def next(self):
        adx_val = self.adx[0]
        plus_di = self.plus_di[0]
        minus_di = self.minus_di[0]

        if not self.position:
            if self.di_cross > 0 and adx_val > self.p.adx_threshold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"ADX趋势买入: +DI={plus_di:.1f} > -DI={minus_di:.1f}, "
                        f"ADX={adx_val:.1f}",
                    )
        else:
            if self.di_cross < 0 or adx_val < self.p.adx_threshold:
                self.close()
                reason = "DI死叉" if self.di_cross < 0 else f"ADX走弱={adx_val:.1f}"
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"ADX趋势卖出: {reason}, +DI={plus_di:.1f}, -DI={minus_di:.1f}",
                )
