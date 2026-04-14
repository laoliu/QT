"""
MFI (Money Flow Index) Strategy (资金流量指标).

Category: Oscillator (Volume-weighted)
Markets: All

MFI is essentially a volume-weighted RSI. It uses both price and volume
to measure buying and selling pressure.

Signal logic:
  - BUY:  MFI drops below 20 (oversold with volume confirmation)
  - SELL: MFI rises above 80 (overbought with volume confirmation)

Parameters:
  - period:     MFI calculation period (default 14)
  - overbought: Overbought threshold (default 80)
  - oversold:   Oversold threshold (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _MFIIndicator(bt.Indicator):
    """Custom MFI indicator.

    MFI = 100 - 100 / (1 + Money Flow Ratio)
    Money Flow Ratio = Positive Money Flow / Negative Money Flow
    Typical Price = (High + Low + Close) / 3
    Raw Money Flow = Typical Price × Volume
    """

    lines = ("mfi",)
    params = (("period", 14),)

    def __init__(self):
        self.typical = (self.data.high + self.data.low + self.data.close) / 3.0
        self.raw_flow = self.typical * self.data.volume
        self.addminperiod(self.p.period + 1)

    def next(self):
        pos_flow = 0.0
        neg_flow = 0.0
        for i in range(-self.p.period + 1, 1):
            if self.typical[i] > self.typical[i - 1]:
                pos_flow += self.raw_flow[i]
            else:
                neg_flow += self.raw_flow[i]
        if neg_flow == 0:
            self.lines.mfi[0] = 100.0
        else:
            ratio = pos_flow / neg_flow
            self.lines.mfi[0] = 100.0 - 100.0 / (1.0 + ratio)


class MFIStrategy(BaseStrategy):
    """Money Flow Index oscillator strategy."""

    STRATEGY_META = StrategyMeta(
        id="mfi",
        name="资金流量指标 (MFI)",
        description=(
            "MFI资金流量策略：MFI低于20（超卖）买入，高于80（超买）卖出。"
            "MFI是量价结合的RSI，被称为'成交量加权RSI'。"
            "相比RSI能更准确地反映资金进出方向，适合确认量价配合。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 14, "MFI计算周期", min=5, max=30),
            StrategyParameter("overbought", "number", 80, "超买阈值", min=70, max=90),
            StrategyParameter("oversold", "number", 20, "超卖阈值", min=10, max=30),
        ],
    )

    params = (
        ("period", 14),
        ("overbought", 80),
        ("oversold", 20),
    )

    def __init__(self):
        super().__init__()
        self.mfi = _MFIIndicator(self.data, period=self.p.period)

    def next(self):
        mfi_val = self.mfi.mfi[0]

        if not self.position:
            if mfi_val < self.p.oversold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"MFI超卖: MFI={mfi_val:.1f} < {self.p.oversold}",
                    )
        else:
            if mfi_val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"MFI超买: MFI={mfi_val:.1f} > {self.p.overbought}",
                )
