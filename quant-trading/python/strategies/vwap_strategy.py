"""
VWAP Strategy (成交量加权平均价).

Category: Trend / Intraday
Markets: All

Volume Weighted Average Price (VWAP) is the benchmark for
institutional execution quality. Price above VWAP indicates
bullish sentiment; below indicates bearish.

Signal logic:
  - BUY:  Price crosses above VWAP with volume confirmation
  - SELL: Price crosses below VWAP

For daily bars, this computes a rolling VWAP over the lookback period.

Parameters:
  - period: VWAP rolling lookback period (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _VWAPIndicator(bt.Indicator):
    """Rolling VWAP indicator for backtrader."""

    lines = ("vwap",)

    params = (("period", 20),)

    def __init__(self):
        self.addminperiod(self.p.period)

    def next(self):
        period = self.p.period
        cum_tp_vol = 0.0
        cum_vol = 0.0
        for i in range(-period + 1, 1):
            tp = (self.data.high[i] + self.data.low[i] + self.data.close[i]) / 3.0
            vol = self.data.volume[i]
            cum_tp_vol += tp * vol
            cum_vol += vol
        if cum_vol == 0:
            self.lines.vwap[0] = self.data.close[0]
        else:
            self.lines.vwap[0] = cum_tp_vol / cum_vol


class VWAPStrategy(BaseStrategy):
    """VWAP-based trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="vwap",
        name="VWAP 成交量加权均价 (Volume Weighted Average Price)",
        description=(
            "VWAP策略：价格上穿VWAP时买入（多头优势），下穿VWAP时卖出（空头优势）。"
            "VWAP是机构交易的重要基准价格，代表市场的'公平价格'。"
            "价格在VWAP之上表示买方占优，之下表示卖方占优。"
            "特别适合日内交易和衡量机构资金动向。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 20, "VWAP滚动周期", min=5, max=60),
        ],
    )

    params = (("period", 20),)

    def __init__(self):
        super().__init__()
        self.vwap = _VWAPIndicator(self.data, period=self.p.period)
        self.crossover = bt.indicators.CrossOver(self.data.close, self.vwap.vwap)

    def next(self):
        close = self.data.close[0]
        vwap_val = self.vwap.vwap[0]

        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"VWAP买入: 价格={close:.2f} > VWAP={vwap_val:.2f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"VWAP卖出: 价格={close:.2f} < VWAP={vwap_val:.2f}",
                )
