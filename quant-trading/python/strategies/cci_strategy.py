"""
CCI (Commodity Channel Index) Strategy (商品通道指数).

Category: Oscillator
Markets: All

Developed by Donald Lambert, CCI measures the deviation of price
from its statistical mean. Originally designed for commodities,
it works well for stocks and indices.

Signal logic:
  - BUY:  CCI crosses above -100 (leaving oversold zone)
  - SELL: CCI crosses below +100 (leaving overbought zone)
  - Values > +100: Overbought / strong uptrend
  - Values < -100: Oversold / strong downtrend

Parameters:
  - period:     CCI calculation period (default 20)
  - overbought: Overbought threshold (default 100)
  - oversold:   Oversold threshold (default -100)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class CCIStrategy(BaseStrategy):
    """CCI oscillator trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="cci",
        name="CCI 商品通道指数 (Commodity Channel Index)",
        description=(
            "CCI策略：CCI从超卖区（<-100）回升时买入，从超买区（>+100）回落时卖出。"
            "CCI衡量价格偏离统计均值的程度，由Donald Lambert发明。"
            "适合捕捉趋势启动和超买超卖反转信号，在震荡和趋势市场均可使用。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 20, "CCI计算周期", min=10, max=50),
            StrategyParameter("overbought", "number", 100, "超买阈值", min=80, max=200),
            StrategyParameter("oversold", "number", -100, "超卖阈值", min=-200, max=-80),
        ],
    )

    params = (
        ("period", 20),
        ("overbought", 100),
        ("oversold", -100),
    )

    def __init__(self):
        super().__init__()
        self.cci = bt.indicators.CCI(self.data, period=self.p.period)
        self._prev_cci = None

    def next(self):
        cci_val = self.cci[0]

        if not self.position:
            # Buy: CCI crosses above oversold threshold (leaving oversold zone)
            if self._prev_cci is not None:
                if self._prev_cci < self.p.oversold and cci_val >= self.p.oversold:
                    size = self.calc_position_size_pct(self.data.close[0])
                    if size > 0:
                        self.buy(size=size)
                        self.record_signal(
                            "buy",
                            self.data.close[0],
                            f"CCI超卖回升: CCI={cci_val:.1f} 穿越 {self.p.oversold}",
                        )
        else:
            # Sell: CCI crosses below overbought threshold (leaving overbought zone)
            if self._prev_cci is not None:
                if self._prev_cci > self.p.overbought and cci_val <= self.p.overbought:
                    self.close()
                    self.record_signal(
                        "sell",
                        self.data.close[0],
                        f"CCI超买回落: CCI={cci_val:.1f} 跌破 {self.p.overbought}",
                    )

        self._prev_cci = cci_val
