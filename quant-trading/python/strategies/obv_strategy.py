"""
OBV (On-Balance Volume) Strategy (能量潮).

Category: Volume / Indicator
Markets: All

OBV accumulates volume based on price direction, revealing buying/selling pressure.

Signal logic:
  - BUY:  OBV breaks above its N-period moving average (volume-confirmed uptrend)
  - SELL: OBV breaks below its N-period moving average (volume-confirmed downtrend)

Parameters:
  - obv_ma_period: Moving average period for OBV signal (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class OBVStrategy(BaseStrategy):
    """On-Balance Volume trend confirmation strategy."""

    STRATEGY_META = StrategyMeta(
        id="obv",
        name="能量潮 (OBV)",
        description=(
            "OBV能量潮策略：OBV上穿其均线时买入（量能确认上涨），"
            "OBV下穿均线时卖出。OBV通过累积成交量揭示资金流向，"
            "是量价分析的核心指标，由Joe Granville发明。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("obv_ma_period", "number", 20, "OBV均线周期", min=10, max=60),
        ],
    )

    params = (
        ("obv_ma_period", 20),
    )

    def __init__(self):
        super().__init__()
        self.obv = bt.indicators.OnBalanceVolume(self.data)
        self.obv_ma = bt.indicators.SMA(self.obv, period=self.p.obv_ma_period)
        self.crossover = bt.indicators.CrossOver(self.obv, self.obv_ma)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"OBV金叉: OBV={self.obv[0]:.0f} > MA={self.obv_ma[0]:.0f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"OBV死叉: OBV={self.obv[0]:.0f} < MA={self.obv_ma[0]:.0f}",
                )
