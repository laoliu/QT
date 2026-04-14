"""
ROC (Rate of Change) Strategy (变化率).

Category: Oscillator / Momentum
Markets: All

Signal logic:
  - BUY:  ROC crosses above zero from below (momentum turning positive)
  - SELL: ROC crosses below zero from above (momentum turning negative)

Parameters:
  - period: ROC lookback period (default 12)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ROCStrategy(BaseStrategy):
    """Rate of Change momentum strategy."""

    STRATEGY_META = StrategyMeta(
        id="roc",
        name="变化率 (ROC)",
        description=(
            "ROC变化率策略：ROC上穿零轴买入（动能转正），下穿零轴卖出（动能转负）。"
            "ROC = (当前价 - N日前价) / N日前价 × 100%。"
            "简单直观的动量指标，适合趋势确认和动量交易。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 12, "ROC计算周期", min=5, max=30),
        ],
    )

    params = (("period", 12),)

    def __init__(self):
        super().__init__()
        self.roc = bt.indicators.ROC(self.data.close, period=self.p.period)

    def next(self):
        roc_val = self.roc[0]
        prev_roc = self.roc[-1] if len(self.roc) > 1 else 0

        if not self.position:
            if roc_val > 0 and prev_roc <= 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"ROC转正: ROC={roc_val:.2f}% (动能向上)",
                    )
        else:
            if roc_val < 0 and prev_roc >= 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"ROC转负: ROC={roc_val:.2f}% (动能向下)",
                )
