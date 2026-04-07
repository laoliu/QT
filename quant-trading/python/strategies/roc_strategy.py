"""
ROC (Rate of Change) Strategy (变动率).

Category: Momentum / Indicator
Markets: All

ROC measures the percentage change of price over N periods.

Signal logic:
  - BUY:  ROC crosses above zero (momentum turning positive)
  - SELL: ROC crosses below zero (momentum turning negative)

Parameters:
  - period:    ROC period (default 12)
  - threshold: Optional threshold above zero for entry (default 0)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ROCStrategy(BaseStrategy):
    """Rate of Change momentum strategy."""

    STRATEGY_META = StrategyMeta(
        id="roc",
        name="变动率 (ROC)",
        description=(
            "ROC变动率策略：衡量价格在N个周期内的百分比变化。"
            "ROC上穿零线买入（动量转正），下穿零线卖出。"
            "简单有效的动量指标，是许多量化模型的基础因子。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 12, "ROC周期", min=5, max=30),
            StrategyParameter("threshold", "number", 0, "入场阈值", min=-5, max=10, step=0.5),
        ],
    )

    params = (
        ("period", 12),
        ("threshold", 0),
    )

    def __init__(self):
        super().__init__()
        self.roc = bt.indicators.RateOfChange100(
            self.data.close, period=self.p.period
        )

    def next(self):
        roc_val = self.roc[0]

        if not self.position:
            if roc_val > self.p.threshold and (len(self) < 2 or self.roc[-1] <= self.p.threshold):
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"ROC突破: ROC={roc_val:.2f}% > {self.p.threshold}",
                    )
        else:
            if roc_val < 0:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"ROC转负: ROC={roc_val:.2f}%",
                )
