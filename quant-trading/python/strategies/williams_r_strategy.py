"""
Williams %R Strategy (威廉指标).

Category: Oscillator
Markets: All

Signal logic:
  - BUY:  Williams %R drops below -80 (oversold)
  - SELL: Williams %R rises above -20 (overbought)

Parameters:
  - period:     Lookback period (default 14)
  - overbought: Overbought level (default -20)
  - oversold:   Oversold level (default -80)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class WilliamsRStrategy(BaseStrategy):
    """Williams %R oscillator strategy."""

    STRATEGY_META = StrategyMeta(
        id="williams-r",
        name="威廉指标 (Williams %R)",
        description=(
            "威廉%R指标策略：%R低于-80（超卖）买入，高于-20（超买）卖出。"
            "由Larry Williams发明，衡量收盘价相对于过去N日价格区间的位置。"
            "与KDJ类似但更灵敏，适合短线交易。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 14, "计算周期", min=5, max=30),
            StrategyParameter("overbought", "number", -20, "超买阈值", min=-30, max=-10),
            StrategyParameter("oversold", "number", -80, "超卖阈值", min=-90, max=-70),
        ],
    )

    params = (
        ("period", 14),
        ("overbought", -20),
        ("oversold", -80),
    )

    def __init__(self):
        super().__init__()
        self.wr = bt.indicators.WilliamsR(self.data, period=self.p.period)

    def next(self):
        wr_val = self.wr[0]

        if not self.position:
            if wr_val < self.p.oversold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"威廉超卖: %R={wr_val:.1f} < {self.p.oversold}",
                    )
        else:
            if wr_val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"威廉超买: %R={wr_val:.1f} > {self.p.overbought}",
                )
