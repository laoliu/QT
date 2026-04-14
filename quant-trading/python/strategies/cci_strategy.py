"""
CCI (Commodity Channel Index) Strategy (CCI顺势指标).

Category: Trend
Markets: All

Signal logic:
  - BUY:  CCI crosses above +100 (strong uptrend confirmed)
  - SELL: CCI crosses below 0 from above (momentum fading)

Parameters:
  - period:      CCI calculation period (default 20)
  - upper_level: Buy threshold (default 100)
  - lower_level: Sell threshold (default -100)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class CCIStrategy(BaseStrategy):
    """CCI Commodity Channel Index strategy."""

    STRATEGY_META = StrategyMeta(
        id="cci",
        name="CCI顺势指标 (CCI Trend)",
        description=(
            "CCI顺势指标策略：CCI突破+100买入，跌破0卖出。"
            "由Donald Lambert发明，衡量价格偏离均值的程度。"
            "CCI>100表示强势上涨，CCI<-100表示强势下跌。"
            "适合捕捉趋势启动和趋势衰竭信号。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 20, "CCI计算周期", min=10, max=40),
            StrategyParameter("upper_level", "number", 100, "买入阈值", min=50, max=200),
            StrategyParameter("lower_level", "number", -100, "强空阈值", min=-200, max=-50),
        ],
    )

    params = (
        ("period", 20),
        ("upper_level", 100),
        ("lower_level", -100),
    )

    def __init__(self):
        super().__init__()
        self.cci = bt.indicators.CCI(self.data, period=self.p.period)

    def next(self):
        cci_val = self.cci[0]
        prev_cci = self.cci[-1] if len(self.cci) > 1 else 0

        if not self.position:
            if cci_val > self.p.upper_level and prev_cci <= self.p.upper_level:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"CCI突破买入: CCI={cci_val:.1f} > {self.p.upper_level}",
                    )
        else:
            if cci_val < 0 and prev_cci >= 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"CCI转负卖出: CCI={cci_val:.1f} < 0",
                )
            elif cci_val < self.p.lower_level:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"CCI强空卖出: CCI={cci_val:.1f} < {self.p.lower_level}",
                )
