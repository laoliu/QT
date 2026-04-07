"""
Williams %R Strategy (威廉指标).

Category: Oscillator
Markets: All

Developed by Larry Williams, Williams %R is a momentum oscillator
that measures overbought/oversold levels, similar to RSI but inverted.

Range: -100 to 0
  - Near 0: Overbought (close near period high)
  - Near -100: Oversold (close near period low)

Signal logic:
  - BUY:  %R crosses above oversold threshold (default -80)
  - SELL: %R crosses below overbought threshold (default -20)

Parameters:
  - period:     Lookback period (default 14)
  - overbought: Overbought threshold (default -20)
  - oversold:   Oversold threshold (default -80)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class WilliamsRStrategy(BaseStrategy):
    """Williams %R oscillator trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="williams_r",
        name="威廉指标 (Williams %R)",
        description=(
            "威廉%R策略：%R从超卖区（<-80）回升时买入，从超买区（>-20）回落时卖出。"
            "由Larry Williams发明，衡量收盘价相对于周期内高低区间的位置。"
            "范围为-100到0，是经典的超买超卖动量振荡器。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 14, "计算周期", min=5, max=50),
            StrategyParameter("overbought", "number", -20, "超买阈值", min=-30, max=-5),
            StrategyParameter("oversold", "number", -80, "超卖阈值", min=-95, max=-70),
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
        self._prev_wr = None

    def next(self):
        wr_val = self.wr[0]

        if not self.position:
            # Buy: %R crosses above oversold
            if self._prev_wr is not None:
                if self._prev_wr < self.p.oversold and wr_val >= self.p.oversold:
                    size = self.calc_position_size_pct(self.data.close[0])
                    if size > 0:
                        self.buy(size=size)
                        self.record_signal(
                            "buy",
                            self.data.close[0],
                            f"威廉超卖回升: %R={wr_val:.1f} 穿越 {self.p.oversold}",
                        )
        else:
            # Sell: %R crosses below overbought
            if self._prev_wr is not None:
                if self._prev_wr > self.p.overbought and wr_val <= self.p.overbought:
                    self.close()
                    self.record_signal(
                        "sell",
                        self.data.close[0],
                        f"威廉超买回落: %R={wr_val:.1f} 跌破 {self.p.overbought}",
                    )

        self._prev_wr = wr_val
