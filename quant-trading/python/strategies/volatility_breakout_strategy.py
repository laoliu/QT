"""
Volatility Breakout Strategy (波动率突破).

Category: Composite (Advanced)
Markets: All

Larry Williams' volatility breakout concept: when today's range expands
beyond yesterday's range × multiplier, enter in the direction of expansion.

Signal logic:
  - BUY:  Open + k × (yesterday's High - yesterday's Low) is breached upward
  - SELL: Trailing stop or end-of-day exit

Parameters:
  - k_factor:   Volatility multiplier (default 0.6)
  - atr_period:  ATR period for stop (default 14)
  - stop_mult:   Stop-loss ATR multiplier (default 2.0)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class VolatilityBreakoutStrategy(BaseStrategy):
    """Volatility breakout trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="volatility-breakout",
        name="波动率突破 (Volatility Breakout)",
        description=(
            "波动率突破策略：当价格突破开盘价+k×昨日振幅时买入。"
            "由Larry Williams提出，核心思想是波动率扩张预示趋势启动。"
            "适合捕捉日内趋势行情，需配合止损严格执行。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("k_factor", "number", 0.6, "波动率乘数k", min=0.3, max=1.0, step=0.1),
            StrategyParameter("atr_period", "number", 14, "ATR周期", min=7, max=30),
            StrategyParameter("stop_mult", "number", 2.0, "止损ATR倍数", min=1.0, max=3.0, step=0.5),
        ],
    )

    params = (
        ("k_factor", 0.6),
        ("atr_period", 14),
        ("stop_mult", 2.0),
    )

    def __init__(self):
        super().__init__()
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self._stop_price = 0.0

    def next(self):
        close = self.data.close[0]
        prev_range = self.data.high[-1] - self.data.low[-1]
        breakout_level = self.data.open[0] + self.p.k_factor * prev_range

        if not self.position:
            if close > breakout_level and prev_range > 0:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self._stop_price = close - self.p.stop_mult * self.atr[0]
                    self.record_signal(
                        "buy",
                        close,
                        f"波动率突破买入: 价格{close:.2f} > "
                        f"突破位{breakout_level:.2f} (k={self.p.k_factor})",
                    )
        else:
            # Update trailing stop
            new_stop = close - self.p.stop_mult * self.atr[0]
            if new_stop > self._stop_price:
                self._stop_price = new_stop

            if close < self._stop_price:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"跟踪止损: 价格{close:.2f} < 止损{self._stop_price:.2f}",
                )
