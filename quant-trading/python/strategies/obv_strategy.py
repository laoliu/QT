"""
OBV (On Balance Volume) Strategy (能量潮指标).

Category: Volume / Trend Confirmation
Markets: All

Developed by Joseph Granville, OBV is a cumulative volume-based
indicator that uses volume flow to predict price changes.

Core idea: volume precedes price — if OBV is rising while price
is flat, a breakout is likely.

Signal logic:
  - BUY:  OBV crosses above its moving average (volume supports uptrend)
  - SELL: OBV crosses below its moving average (volume supports downtrend)

Parameters:
  - obv_ma_period: OBV moving average period (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _OBVIndicator(bt.Indicator):
    """On Balance Volume indicator for backtrader."""

    lines = ("obv",)

    def __init__(self):
        self.addminperiod(2)

    def next(self):
        if len(self) == 1:
            self.lines.obv[0] = self.data.volume[0]
            return
        if self.data.close[0] > self.data.close[-1]:
            self.lines.obv[0] = self.lines.obv[-1] + self.data.volume[0]
        elif self.data.close[0] < self.data.close[-1]:
            self.lines.obv[0] = self.lines.obv[-1] - self.data.volume[0]
        else:
            self.lines.obv[0] = self.lines.obv[-1]


class OBVStrategy(BaseStrategy):
    """OBV volume-trend strategy."""

    STRATEGY_META = StrategyMeta(
        id="obv",
        name="OBV 能量潮 (On Balance Volume)",
        description=(
            "OBV能量潮策略：OBV上穿其均线时买入（量价配合上涨），"
            "OBV下穿其均线时卖出（量能萎缩下跌）。"
            "由Joseph Granville发明，核心思想是'量在价先'——"
            "成交量的变化往往领先于价格变化。"
            "适合确认趋势和发现量价背离信号。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("obv_ma_period", "number", 20, "OBV均线周期", min=5, max=60),
        ],
    )

    params = (("obv_ma_period", 20),)

    def __init__(self):
        super().__init__()
        self.obv = _OBVIndicator(self.data)
        self.obv_ma = bt.indicators.SMA(self.obv.obv, period=self.p.obv_ma_period)
        self.crossover = bt.indicators.CrossOver(self.obv.obv, self.obv_ma)

    def next(self):
        obv_val = self.obv.obv[0]
        obv_ma_val = self.obv_ma[0]

        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"OBV金叉: OBV={obv_val:.0f} > MA={obv_ma_val:.0f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"OBV死叉: OBV={obv_val:.0f} < MA={obv_ma_val:.0f}",
                )
