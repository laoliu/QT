"""
VWAP Strategy (成交量加权均价).

Category: Indicator / Intraday
Markets: All

VWAP (Volume Weighted Average Price) is the average price weighted by volume.
Institutional benchmark for execution quality.

Signal logic:
  - BUY:  Price crosses above VWAP (institutional buying support)
  - SELL: Price crosses below VWAP (institutional selling pressure)

Parameters:
  - period: VWAP calculation period (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _VWAPIndicator(bt.Indicator):
    """Rolling VWAP indicator."""

    lines = ("vwap",)
    params = (("period", 20),)

    def __init__(self):
        self.addminperiod(self.p.period)
        self._tp_vol = []
        self._vol = []

    def next(self):
        tp = (self.data.high[0] + self.data.low[0] + self.data.close[0]) / 3.0
        vol = self.data.volume[0]
        self._tp_vol.append(tp * vol)
        self._vol.append(vol)

        if len(self._tp_vol) > self.p.period:
            self._tp_vol = self._tp_vol[-self.p.period:]
            self._vol = self._vol[-self.p.period:]

        total_vol = sum(self._vol)
        if total_vol > 0:
            self.lines.vwap[0] = sum(self._tp_vol) / total_vol
        else:
            self.lines.vwap[0] = self.data.close[0]


class VWAPStrategy(BaseStrategy):
    """VWAP crossover strategy."""

    STRATEGY_META = StrategyMeta(
        id="vwap",
        name="成交量加权均价 (VWAP)",
        description=(
            "VWAP策略：价格上穿VWAP时买入（获得成交量支撑），"
            "价格下穿VWAP时卖出。VWAP是机构投资者衡量执行质量的基准价格，"
            "是日内交易中最重要的参考线之一。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 20, "VWAP周期", min=5, max=60),
        ],
    )

    params = (
        ("period", 20),
    )

    def __init__(self):
        super().__init__()
        self.vwap = _VWAPIndicator(self.data, period=self.p.period)

    def next(self):
        close = self.data.close[0]
        vwap_val = self.vwap.vwap[0]

        if not self.position:
            if close > vwap_val and (len(self) < 2 or self.data.close[-1] <= self.vwap.vwap[-1]):
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"VWAP上穿: 价格{close:.2f} > VWAP={vwap_val:.2f}",
                    )
        else:
            if close < vwap_val:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"VWAP下穿: 价格{close:.2f} < VWAP={vwap_val:.2f}",
                )
