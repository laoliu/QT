"""
CMF (Chaikin Money Flow) Strategy (蔡金资金流).

Category: Indicator (Volume)
Markets: All

Signal logic:
  - BUY:  CMF crosses above zero (net buying pressure)
  - SELL: CMF crosses below zero (net selling pressure)

Parameters:
  - period: CMF calculation period (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _CMFIndicator(bt.Indicator):
    """Chaikin Money Flow indicator.

    CLV = ((Close - Low) - (High - Close)) / (High - Low)
    CMF = Sum(CLV × Volume, N) / Sum(Volume, N)
    """

    lines = ("cmf",)
    params = (("period", 20),)

    def __init__(self):
        hl_diff = self.data.high - self.data.low
        clv = bt.If(
            hl_diff > 0,
            ((self.data.close - self.data.low) - (self.data.high - self.data.close)) / hl_diff,
            0.0,
        )
        clv_vol = clv * self.data.volume
        self.lines.cmf = bt.indicators.SumN(clv_vol, period=self.p.period) / bt.DivByZero(
            bt.indicators.SumN(self.data.volume, period=self.p.period), 1.0
        )


class CMFStrategy(BaseStrategy):
    """Chaikin Money Flow strategy."""

    STRATEGY_META = StrategyMeta(
        id="cmf",
        name="蔡金资金流 (Chaikin Money Flow)",
        description=(
            "蔡金资金流策略：CMF上穿零轴买入（资金净流入），下穿零轴卖出（资金净流出）。"
            "由Marc Chaikin发明，衡量一段时间内的资金流向。"
            "CMF>0表示多头占优，CMF<0表示空头占优。适合确认趋势的持续性。"
        ),
        category="indicator",
        parameters=[
            StrategyParameter("period", "number", 20, "CMF计算周期", min=10, max=40),
        ],
    )

    params = (("period", 20),)

    def __init__(self):
        super().__init__()
        self.cmf = _CMFIndicator(self.data, period=self.p.period)

    def next(self):
        cmf_val = self.cmf.cmf[0]
        prev_cmf = self.cmf.cmf[-1] if len(self.cmf.cmf) > 1 else 0

        if not self.position:
            if cmf_val > 0 and prev_cmf <= 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"CMF转正买入: CMF={cmf_val:.4f} (资金净流入)",
                    )
        else:
            if cmf_val < 0 and prev_cmf >= 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"CMF转负卖出: CMF={cmf_val:.4f} (资金净流出)",
                )
