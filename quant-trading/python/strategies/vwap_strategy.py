"""
Volume Weighted Average Price (VWAP) Strategy (成交量加权均价).

Category: Indicator
Markets: All (especially popular for institutional execution)

Signal logic:
  - BUY:  Price crosses above VWAP (bullish, above average cost)
  - SELL: Price crosses below VWAP (bearish, below average cost)

Parameters:
  - period: VWAP period (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _VWAPIndicator(bt.Indicator):
    """Simplified VWAP using rolling sum."""

    lines = ("vwap",)
    params = (("period", 20),)

    def __init__(self):
        typical = (self.data.high + self.data.low + self.data.close) / 3.0
        tp_vol = typical * self.data.volume
        self.lines.vwap = bt.indicators.SumN(tp_vol, period=self.p.period) / bt.DivByZero(
            bt.indicators.SumN(self.data.volume, period=self.p.period), 1.0,
        )


class VWAPStrategy(BaseStrategy):
    """VWAP crossover strategy."""

    STRATEGY_META = StrategyMeta(
        id="vwap",
        name="成交量加权均价 (VWAP)",
        description=(
            "VWAP策略：价格上穿VWAP买入，下穿VWAP卖出。"
            "VWAP是机构交易者最重要的基准价格指标。"
            "价格在VWAP之上表示多头占优（买方强于平均成本），反之空头占优。"
        ),
        category="indicator",
        parameters=[
            StrategyParameter("period", "number", 20, "VWAP周期", min=5, max=60),
        ],
    )

    params = (("period", 20),)

    def __init__(self):
        super().__init__()
        self.vwap = _VWAPIndicator(self.data, period=self.p.period)

    def next(self):
        close = self.data.close[0]
        vwap_val = self.vwap.vwap[0]
        prev_close = self.data.close[-1]
        prev_vwap = self.vwap.vwap[-1] if len(self.vwap.vwap) > 1 else vwap_val

        if not self.position:
            if close > vwap_val and prev_close <= prev_vwap:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"VWAP突破买入: 价格{close:.2f} > VWAP={vwap_val:.2f}",
                    )
        else:
            if close < vwap_val and prev_close >= prev_vwap:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"VWAP跌破卖出: 价格{close:.2f} < VWAP={vwap_val:.2f}",
                )
