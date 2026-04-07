"""
Chaikin Money Flow Strategy (蔡金资金流).

Category: Volume / Indicator
Markets: All

CMF measures the amount of money flow volume over a period.

Signal logic:
  - BUY:  CMF crosses above +0.05 (accumulation)
  - SELL: CMF crosses below -0.05 (distribution)

Parameters:
  - period:    CMF period (default 20)
  - buy_level: CMF level for buy signal (default 0.05)
  - sell_level: CMF level for sell signal (default -0.05)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _CMFIndicator(bt.Indicator):
    """Chaikin Money Flow indicator."""

    lines = ("cmf",)
    params = (("period", 20),)

    def __init__(self):
        self.addminperiod(self.p.period)
        self._clv_vol = []
        self._vol = []

    def next(self):
        high = self.data.high[0]
        low = self.data.low[0]
        close = self.data.close[0]
        vol = self.data.volume[0]

        hl_range = high - low
        if hl_range > 0:
            clv = ((close - low) - (high - close)) / hl_range
        else:
            clv = 0.0

        self._clv_vol.append(clv * vol)
        self._vol.append(vol)

        if len(self._clv_vol) > self.p.period:
            self._clv_vol = self._clv_vol[-self.p.period:]
            self._vol = self._vol[-self.p.period:]

        total_vol = sum(self._vol)
        if total_vol > 0:
            self.lines.cmf[0] = sum(self._clv_vol) / total_vol
        else:
            self.lines.cmf[0] = 0.0


class ChaikinStrategy(BaseStrategy):
    """Chaikin Money Flow accumulation/distribution strategy."""

    STRATEGY_META = StrategyMeta(
        id="chaikin",
        name="蔡金资金流 (Chaikin MF)",
        description=(
            "蔡金资金流策略：CMF>0.05表示资金持续流入（吸筹），买入；"
            "CMF<-0.05表示资金持续流出（派发），卖出。"
            "由Marc Chaikin发明，是衡量机构资金动向的重要指标。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 20, "CMF周期", min=10, max=40),
            StrategyParameter("buy_level", "number", 0.05, "买入阈值", min=0.01, max=0.15, step=0.01),
            StrategyParameter("sell_level", "number", -0.05, "卖出阈值", min=-0.15, max=-0.01, step=0.01),
        ],
    )

    params = (
        ("period", 20),
        ("buy_level", 0.05),
        ("sell_level", -0.05),
    )

    def __init__(self):
        super().__init__()
        self.cmf = _CMFIndicator(self.data, period=self.p.period)

    def next(self):
        cmf_val = self.cmf.cmf[0]

        if not self.position:
            if cmf_val > self.p.buy_level:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"蔡金吸筹: CMF={cmf_val:.3f} > {self.p.buy_level}",
                    )
        else:
            if cmf_val < self.p.sell_level:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"蔡金派发: CMF={cmf_val:.3f} < {self.p.sell_level}",
                )
