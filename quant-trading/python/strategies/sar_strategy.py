"""
Parabolic SAR Strategy (抛物线转向指标).

Category: Trend Following
Markets: All

Developed by Welles Wilder, Parabolic SAR (Stop and Reverse) provides
potential entry and exit points by plotting dots above or below price.

Signal logic:
  - BUY:  SAR flips from above price to below (bullish reversal)
  - SELL: SAR flips from below price to above (bearish reversal)

The SAR value accelerates toward price, tightening the trailing stop.

Parameters:
  - af_start: Acceleration Factor starting value (default 0.02)
  - af_step:  AF increment per new extreme point (default 0.02)
  - af_max:   Maximum AF value (default 0.20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class SARStrategy(BaseStrategy):
    """Parabolic SAR trend-following strategy."""

    STRATEGY_META = StrategyMeta(
        id="sar",
        name="抛物线SAR (Parabolic SAR)",
        description=(
            "抛物线SAR策略：SAR从价格上方翻转到下方时买入（趋势反转向上），"
            "SAR从价格下方翻转到上方时卖出（趋势反转向下）。"
            "由Welles Wilder发明，是一个自动调节止损位的趋势跟踪系统。"
            "SAR点会随趋势加速向价格靠近，提供动态止损位。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("af_start", "number", 0.02, "加速因子初始值", min=0.01, max=0.05, step=0.01),
            StrategyParameter("af_step", "number", 0.02, "加速因子步进", min=0.01, max=0.05, step=0.01),
            StrategyParameter("af_max", "number", 0.20, "加速因子上限", min=0.10, max=0.40, step=0.05),
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
            af=self.p.af_start,
            afmax=self.p.af_max,
        )
        self._prev_sar_below = None

    def next(self):
        close = self.data.close[0]
        sar_val = self.sar[0]
        sar_below = close > sar_val  # SAR is below price = bullish

        if not self.position:
            # Buy: SAR flips from above to below price
            if self._prev_sar_below is not None and not self._prev_sar_below and sar_below:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"SAR反转买入: SAR={sar_val:.2f} < 价格={close:.2f}",
                    )
        else:
            # Sell: SAR flips from below to above price
            if self._prev_sar_below is not None and self._prev_sar_below and not sar_below:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"SAR反转卖出: SAR={sar_val:.2f} > 价格={close:.2f}",
                )

        self._prev_sar_below = sar_below
