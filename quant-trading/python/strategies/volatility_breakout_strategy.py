"""
Volatility Breakout Strategy (波动率突破).

Category: Composite / Advanced
Markets: All

Based on Larry Williams' volatility expansion concept.
Enters when price moves beyond a range expansion threshold.

Signal logic:
  - BUY:  Today's open + K × yesterday's range; price exceeds this level
  - SELL: Trailing ATR stop or time-based exit

Parameters:
  - k_factor:      Range expansion multiplier (default 0.6)
  - atr_period:    ATR period for stop (default 14)
  - stop_mult:     ATR multiplier for trailing stop (default 2.0)
  - max_hold_days: Maximum holding period (default 5)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class VolatilityBreakoutStrategy(BaseStrategy):
    """Volatility expansion breakout strategy."""

    STRATEGY_META = StrategyMeta(
        id="volatility-breakout",
        name="波动率突破 (Volatility Breakout)",
        description=(
            "波动率突破策略：基于Larry Williams的波动率扩张概念。"
            "当价格超过'开盘价+K×昨日振幅'时买入，配合ATR追踪止损。"
            "适合短线爆发行情，在高波动市场中表现优异。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("k_factor", "number", 0.6, "K因子(振幅倍数)", min=0.2, max=1.0, step=0.1),
            StrategyParameter("atr_period", "number", 14, "ATR止损周期", min=7, max=30),
            StrategyParameter("stop_mult", "number", 2.0, "止损ATR倍数", min=1.0, max=4.0, step=0.5),
            StrategyParameter("max_hold_days", "number", 5, "最大持仓天数", min=1, max=20),
        ],
    )

    params = (
        ("k_factor", 0.6),
        ("atr_period", 14),
        ("stop_mult", 2.0),
        ("max_hold_days", 5),
    )

    def __init__(self):
        super().__init__()
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self._entry_bar = -1
        self._stop_price = 0.0

    def next(self):
        if len(self) < 2:
            return

        close = self.data.close[0]
        prev_range = self.data.high[-1] - self.data.low[-1]
        breakout_level = self.data.open[0] + self.p.k_factor * prev_range

        if not self.position:
            if close > breakout_level and prev_range > 0:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self._entry_bar = len(self)
                    self._stop_price = close - self.p.stop_mult * self.atr[0]
                    self.record_signal(
                        "buy", close,
                        f"波动率突破: 价格{close:.2f} > 突破位{breakout_level:.2f}",
                    )
        else:
            new_stop = close - self.p.stop_mult * self.atr[0]
            if new_stop > self._stop_price:
                self._stop_price = new_stop

            hold = len(self) - self._entry_bar

            if close < self._stop_price:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"追踪止损: 价格{close:.2f} < 止损{self._stop_price:.2f}",
                )
            elif hold >= self.p.max_hold_days:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"时间止损: 持仓{hold}天",
                )
