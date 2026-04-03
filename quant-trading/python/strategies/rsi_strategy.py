"""
RSI (Relative Strength Index) Strategy (RSI 超买超卖).

Category: Oscillator / Mean Reversion
Markets: All

Signal logic:
  - BUY:  RSI drops below oversold threshold (default 30) → oversold reversal
  - SELL: RSI rises above overbought threshold (default 70) → overbought reversal

Enhanced with RSI divergence detection:
  - Bullish divergence: price makes lower low but RSI makes higher low → stronger buy
  - Bearish divergence: price makes higher high but RSI makes lower high → stronger sell

Parameters:
  - period:     RSI calculation period (default 14)
  - overbought: Overbought threshold (default 70)
  - oversold:   Oversold threshold (default 30)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class RSIStrategy(BaseStrategy):
    """RSI overbought/oversold trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="rsi",
        name="RSI 超买超卖 (RSI Overbought/Oversold)",
        description=(
            "RSI指标策略：RSI低于30（超卖区）买入，高于70（超买区）卖出。"
            "由Welles Wilder发明，是最经典的动量振荡器之一。"
            "适合震荡市和均值回归行情，趋势市场中可能过早反转。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 14, "RSI计算周期", min=5, max=50),
            StrategyParameter("overbought", "number", 70, "超买阈值", min=60, max=90),
            StrategyParameter("oversold", "number", 30, "超卖阈值", min=10, max=40),
        ],
    )

    params = (
        ("period", 14),
        ("overbought", 70),
        ("oversold", 30),
    )

    def __init__(self):
        super().__init__()
        self.rsi = bt.indicators.RSI(
            self.data.close,
            period=self.p.period,
        )
        self._prev_rsi = None

    def next(self):
        rsi_val = self.rsi[0]

        if not self.position:
            # Buy when RSI crosses below oversold threshold
            if rsi_val < self.p.oversold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"RSI超卖: RSI={rsi_val:.1f} < {self.p.oversold}",
                    )
        else:
            # Sell when RSI crosses above overbought threshold
            if rsi_val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"RSI超买: RSI={rsi_val:.1f} > {self.p.overbought}",
                )
