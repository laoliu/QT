"""
CCI (Commodity Channel Index) Strategy (顺势指标).

Category: Oscillator / Indicator
Markets: All

CCI measures the deviation of price from its statistical mean.

Signal logic:
  - BUY:  CCI crosses above +100 (strong upward momentum)
  - SELL: CCI crosses below -100 (strong downward momentum) or drops below 0

Parameters:
  - period:          CCI period (default 20)
  - upper_threshold: Upper entry threshold (default 100)
  - lower_threshold: Lower exit threshold (default -100)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class CCIStrategy(BaseStrategy):
    """CCI momentum strategy."""

    STRATEGY_META = StrategyMeta(
        id="cci",
        name="顺势指标 (CCI)",
        description=(
            "CCI顺势指标策略：CCI上穿+100时表示强势动量买入，"
            "CCI下穿-100或跌破0轴时卖出。"
            "由Donald Lambert发明，衡量价格偏离统计均值的程度。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 20, "CCI周期", min=10, max=50),
            StrategyParameter("upper_threshold", "number", 100, "买入阈值", min=50, max=200),
            StrategyParameter("lower_threshold", "number", -100, "卖出阈值", min=-200, max=-50),
        ],
    )

    params = (
        ("period", 20),
        ("upper_threshold", 100),
        ("lower_threshold", -100),
    )

    def __init__(self):
        super().__init__()
        self.cci = bt.indicators.CommodityChannelIndex(
            self.data, period=self.p.period
        )

    def next(self):
        cci_val = self.cci[0]

        if not self.position:
            if cci_val > self.p.upper_threshold and (len(self) < 2 or self.cci[-1] <= self.p.upper_threshold):
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"CCI突破: CCI={cci_val:.1f} > {self.p.upper_threshold}",
                    )
        else:
            if cci_val < 0:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"CCI回落: CCI={cci_val:.1f} < 0",
                )
