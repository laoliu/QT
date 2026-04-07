"""
DPO (Detrended Price Oscillator) Strategy (去趋势价格震荡).

Category: Oscillator / Indicator
Markets: All

DPO removes the trend component to identify cycles.

Signal logic:
  - BUY:  DPO crosses above zero from below (cycle bottom)
  - SELL: DPO crosses below zero from above (cycle top)

Parameters:
  - period: DPO period (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DPOStrategy(BaseStrategy):
    """Detrended Price Oscillator cycle strategy."""

    STRATEGY_META = StrategyMeta(
        id="dpo",
        name="去趋势震荡DPO (DPO)",
        description=(
            "DPO去趋势价格震荡策略：去除趋势后识别价格周期。"
            "DPO上穿零线买入（周期底部），下穿零线卖出（周期顶部）。"
            "适合识别价格的周期性波动。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 20, "DPO周期", min=10, max=50),
        ],
    )

    params = (
        ("period", 20),
    )

    def __init__(self):
        super().__init__()
        self.dpo = bt.indicators.DetrendedPriceOscillator(
            self.data.close, period=self.p.period
        )

    def next(self):
        dpo_val = self.dpo[0]

        if not self.position:
            if dpo_val > 0 and (len(self) < 2 or self.dpo[-1] <= 0):
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"DPO上穿零线: DPO={dpo_val:.2f}",
                    )
        else:
            if dpo_val < 0 and self.dpo[-1] >= 0:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"DPO下穿零线: DPO={dpo_val:.2f}",
                )
