"""
Parabolic SAR Strategy (抛物线止损转向).

Category: Trend Following (CTA)
Markets: All

Parabolic SAR (Stop and Reverse) by Welles Wilder provides a trailing
stop that accelerates as the trend develops.

Signal logic:
  - BUY:  SAR flips below price (trend reversal to bullish)
  - SELL: SAR flips above price (trend reversal to bearish)

Parameters:
  - af_start: Acceleration factor initial value (default 0.02)
  - af_max:   Maximum acceleration factor (default 0.20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ParabolicSARStrategy(BaseStrategy):
    """Parabolic SAR trend-reversal strategy."""

    STRATEGY_META = StrategyMeta(
        id="parabolic-sar",
        name="抛物线SAR (Parabolic SAR)",
        description=(
            "抛物线SAR策略：SAR点从价格上方翻转至下方时买入（趋势反转为多头），"
            "SAR点从下方翻转至上方时卖出。Welles Wilder发明的经典趋势跟踪止损系统。"
            "加速因子使止损线在趋势发展中逐步收紧，自动保护利润。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("af_start", "number", 0.02, "加速因子初始值", min=0.01, max=0.05, step=0.01),
            StrategyParameter("af_max", "number", 0.20, "加速因子最大值", min=0.10, max=0.40, step=0.05),
        ],
    )

    params = (
        ("af_start", 0.02),
        ("af_max", 0.20),
    )

    def __init__(self):
        super().__init__()
        self.sar = bt.indicators.ParabolicSAR(
            self.data,
            af=self.p.af_start,
            afmax=self.p.af_max,
        )

    def next(self):
        close = self.data.close[0]
        sar_val = self.sar[0]

        if not self.position:
            if close > sar_val and (len(self) < 2 or self.data.close[-1] <= self.sar[-1]):
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"SAR翻多: 价格{close:.2f} > SAR={sar_val:.2f}",
                    )
        else:
            if close < sar_val:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"SAR翻空: 价格{close:.2f} < SAR={sar_val:.2f}",
                )
