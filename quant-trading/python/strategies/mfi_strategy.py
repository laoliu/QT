"""
MFI (Money Flow Index) Strategy (资金流量指标).

Category: Oscillator / Volume
Markets: All

MFI is a volume-weighted RSI, measuring buying/selling pressure.

Signal logic:
  - BUY:  MFI drops below 20 (oversold, heavy selling exhaustion)
  - SELL: MFI rises above 80 (overbought, buying climax)

Parameters:
  - period:     MFI period (default 14)
  - overbought: Overbought threshold (default 80)
  - oversold:   Oversold threshold (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _MFIIndicator(bt.Indicator):
    """Money Flow Index indicator."""

    lines = ("mfi",)
    params = (("period", 14),)

    def __init__(self):
        self.addminperiod(self.p.period + 1)
        self._typical_prices = []
        self._volumes = []

    def next(self):
        tp = (self.data.high[0] + self.data.low[0] + self.data.close[0]) / 3.0
        self._typical_prices.append(tp)
        self._volumes.append(self.data.volume[0])

        if len(self._typical_prices) < self.p.period + 1:
            self.lines.mfi[0] = 50.0
            return

        pos_flow = 0.0
        neg_flow = 0.0
        for i in range(-self.p.period, 0):
            idx = len(self._typical_prices) + i
            prev_idx = idx - 1
            if prev_idx < 0:
                continue
            mf = self._typical_prices[idx] * self._volumes[idx]
            if self._typical_prices[idx] > self._typical_prices[prev_idx]:
                pos_flow += mf
            else:
                neg_flow += mf

        if neg_flow == 0:
            self.lines.mfi[0] = 100.0
        else:
            ratio = pos_flow / neg_flow
            self.lines.mfi[0] = 100.0 - (100.0 / (1.0 + ratio))

        max_history = self.p.period * 3
        if len(self._typical_prices) > max_history:
            self._typical_prices = self._typical_prices[-max_history:]
            self._volumes = self._volumes[-max_history:]


class MFIStrategy(BaseStrategy):
    """Money Flow Index overbought/oversold strategy."""

    STRATEGY_META = StrategyMeta(
        id="mfi",
        name="资金流量MFI (Money Flow Index)",
        description=(
            "MFI资金流量策略：MFI低于20（资金流出耗尽，超卖）时买入，"
            "MFI高于80（资金大量涌入，超买）时卖出。"
            "MFI被称为'量化版RSI'，结合了价格和成交量信息。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 14, "MFI周期", min=7, max=30),
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
                        "buy", self.data.close[0],
                        f"MFI超卖: MFI={mfi_val:.1f} < {self.p.oversold}",
                    )
        else:
            if mfi_val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"MFI超买: MFI={mfi_val:.1f} > {self.p.overbought}",
                )
