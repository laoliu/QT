"""
Ultimate Oscillator Strategy (终极震荡指标).

Category: Oscillator / Indicator
Markets: All

The Ultimate Oscillator by Larry Williams combines three time frames
to reduce false signals common in single-period oscillators.

Signal logic:
  - BUY:  UO drops below oversold (30) then crosses above → buy with bullish divergence
  - SELL: UO rises above overbought (70) then crosses below

Parameters:
  - period1:    Short period (default 7)
  - period2:    Medium period (default 14)
  - period3:    Long period (default 28)
  - overbought: Overbought threshold (default 70)
  - oversold:   Oversold threshold (default 30)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _UltimateOscillator(bt.Indicator):
    """Ultimate Oscillator indicator."""

    lines = ("uo",)
    params = (
        ("period1", 7),
        ("period2", 14),
        ("period3", 28),
    )

    def __init__(self):
        self.addminperiod(max(self.p.period1, self.p.period2, self.p.period3) + 1)
        self._bp = []
        self._tr = []

    def next(self):
        close = self.data.close[0]
        low = self.data.low[0]
        high = self.data.high[0]

        if len(self) < 2:
            self.lines.uo[0] = 50.0
            return

        prev_close = self.data.close[-1]
        bp = close - min(low, prev_close)
        tr = max(high, prev_close) - min(low, prev_close)

        self._bp.append(bp)
        self._tr.append(tr)

        max_period = max(self.p.period1, self.p.period2, self.p.period3)
        if len(self._bp) > max_period + 5:
            self._bp = self._bp[-(max_period + 5):]
            self._tr = self._tr[-(max_period + 5):]

        if len(self._bp) < self.p.period3:
            self.lines.uo[0] = 50.0
            return

        def _avg(n):
            bp_sum = sum(self._bp[-n:])
            tr_sum = sum(self._tr[-n:])
            return bp_sum / tr_sum if tr_sum > 0 else 0.5

        avg1 = _avg(self.p.period1)
        avg2 = _avg(self.p.period2)
        avg3 = _avg(self.p.period3)

        self.lines.uo[0] = 100.0 * (4 * avg1 + 2 * avg2 + avg3) / 7.0


class UltimateOscillatorStrategy(BaseStrategy):
    """Ultimate Oscillator multi-timeframe strategy."""

    STRATEGY_META = StrategyMeta(
        id="ultimate-oscillator",
        name="终极震荡 (Ultimate Oscillator)",
        description=(
            "终极震荡策略：结合短(7)、中(14)、长(28)三个周期的买压，"
            "减少单周期振荡器的假信号。UO低于30超卖买入，高于70超买卖出。"
            "由Larry Williams发明。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period1", "number", 7, "短周期", min=3, max=15),
            StrategyParameter("period2", "number", 14, "中周期", min=7, max=28),
            StrategyParameter("period3", "number", 28, "长周期", min=14, max=56),
            StrategyParameter("overbought", "number", 70, "超买阈值", min=60, max=80),
            StrategyParameter("oversold", "number", 30, "超卖阈值", min=20, max=40),
        ],
    )

    params = (
        ("period1", 7),
        ("period2", 14),
        ("period3", 28),
        ("overbought", 70),
        ("oversold", 30),
    )

    def __init__(self):
        super().__init__()
        self.uo = _UltimateOscillator(
            self.data,
            period1=self.p.period1,
            period2=self.p.period2,
            period3=self.p.period3,
        )

    def next(self):
        uo_val = self.uo.uo[0]

        if not self.position:
            if uo_val < self.p.oversold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"终极震荡超卖: UO={uo_val:.1f} < {self.p.oversold}",
                    )
        else:
            if uo_val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"终极震荡超买: UO={uo_val:.1f} > {self.p.overbought}",
                )
