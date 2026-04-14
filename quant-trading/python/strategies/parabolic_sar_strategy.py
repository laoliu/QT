"""
Parabolic SAR Strategy (抛物线SAR).

Category: Trend Following
Markets: All

Signal logic:
  - BUY:  SAR flips below price (bullish trend)
  - SELL: SAR flips above price (bearish trend)

Parameters:
  - af_start: Acceleration factor start (default 0.02)
  - af_step:  Acceleration factor increment (default 0.02)
  - af_max:   Maximum acceleration factor (default 0.20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ParabolicSARStrategy(BaseStrategy):
    """Parabolic SAR trend-following strategy."""

    STRATEGY_META = StrategyMeta(
        id="parabolic-sar",
        name="抛物线SAR (Parabolic SAR)",
        description=(
            "抛物线SAR策略：SAR点在价格下方时做多，翻转到上方时平仓。"
            "由Welles Wilder发明，SAR（Stop And Reverse）提供动态跟踪止损。"
            "加速因子随趋势延续逐步加大，使止损逐渐收紧。"
            "在强趋势中表现优异，震荡市中频繁翻转。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("af_start", "number", 0.02, "加速因子起始值", min=0.01, max=0.05, step=0.01),
            StrategyParameter("af_step", "number", 0.02, "加速因子步进", min=0.01, max=0.05, step=0.01),
            StrategyParameter("af_max", "number", 0.20, "加速因子最大值", min=0.10, max=0.40, step=0.02),
        ],
    )

    params = (
        ("af_start", 0.02),
        ("af_step", 0.02),
        ("af_max", 0.20),
    )

    def __init__(self):
        super().__init__()
        self.sar = bt.indicators.ParabolicSAR(
            self.data,
            period=2,
            af=self.p.af_start,
            afmax=self.p.af_max,
        )

    def next(self):
        close = self.data.close[0]
        sar_val = self.sar[0]

        if not self.position:
            if close > sar_val:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"SAR多头: 价格{close:.2f} > SAR={sar_val:.2f}",
                    )
        else:
            if close < sar_val:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"SAR翻转: 价格{close:.2f} < SAR={sar_val:.2f}",
                )
