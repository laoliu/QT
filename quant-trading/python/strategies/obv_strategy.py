"""
OBV (On-Balance Volume) Strategy (能量潮).

Category: Indicator (Volume)
Markets: All

Signal logic:
  - BUY:  OBV breaks above its N-day moving average (volume supports uptrend)
  - SELL: OBV breaks below its N-day moving average

Parameters:
  - obv_ma_period: OBV moving average period (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class OBVStrategy(BaseStrategy):
    """On-Balance Volume trend strategy."""

    STRATEGY_META = StrategyMeta(
        id="obv",
        name="能量潮 (OBV)",
        description=(
            "OBV能量潮策略：OBV突破均线买入，跌破均线卖出。"
            "由Joe Granville发明，通过累积成交量判断资金流向。"
            "价涨量增则OBV上升（多头积累），价跌量增则OBV下降（空头分配）。"
            "OBV领先于价格变化，适合确认趋势的可靠性。"
        ),
        category="indicator",
        parameters=[
            StrategyParameter("obv_ma_period", "number", 20, "OBV均线周期", min=5, max=60),
        ],
    )

    params = (
        ("obv_ma_period", 20),
    )

    def __init__(self):
        super().__init__()
        self.obv = bt.indicators.OBV(self.data)
        self.obv_ma = bt.indicators.SMA(self.obv, period=self.p.obv_ma_period)
        self.crossover = bt.indicators.CrossOver(self.obv, self.obv_ma)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"OBV突破均线: OBV={self.obv[0]:.0f} > "
                        f"MA={self.obv_ma[0]:.0f}",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"OBV跌破均线: OBV={self.obv[0]:.0f} < "
                    f"MA={self.obv_ma[0]:.0f}",
                )
