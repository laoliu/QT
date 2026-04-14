"""
ADL (Accumulation/Distribution Line) Strategy (聚散指标).

Category: Indicator (Volume)
Markets: All

Signal logic:
  - BUY:  ADL breaks above its moving average (accumulation)
  - SELL: ADL breaks below its moving average (distribution)

Parameters:
  - ma_period: ADL moving average period (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ADLStrategy(BaseStrategy):
    """Accumulation/Distribution Line strategy."""

    STRATEGY_META = StrategyMeta(
        id="adl",
        name="聚散指标 (ADL)",
        description=(
            "聚散指标策略：ADL上穿均线买入（资金聚集），下穿均线卖出（资金分散）。"
            "AD线通过收盘价在日内波幅中的位置和成交量计算资金流向。"
            "收盘偏高位+大成交量=资金聚集，收盘偏低位+大成交量=资金分散。"
        ),
        category="indicator",
        parameters=[
            StrategyParameter("ma_period", "number", 20, "ADL均线周期", min=5, max=60),
        ],
    )

    params = (("ma_period", 20),)

    def __init__(self):
        super().__init__()
        self.ad = bt.indicators.AccDeOsc(self.data)
        self.ad_ma = bt.indicators.SMA(self.ad, period=self.p.ma_period)
        self.crossover = bt.indicators.CrossOver(self.ad, self.ad_ma)

    def next(self):
        if not self.position:
            if self.crossover > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"ADL上穿均线: 资金聚集信号",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"ADL下穿均线: 资金分散信号",
                )
