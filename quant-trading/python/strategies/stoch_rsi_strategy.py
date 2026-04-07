"""
Stochastic RSI Strategy (随机RSI).

Category: Oscillator / Indicator
Markets: All

StochRSI applies the stochastic formula to RSI values, producing
an even more sensitive oscillator.

Signal logic:
  - BUY:  StochRSI crosses above oversold level (default 0.2)
  - SELL: StochRSI crosses below overbought level (default 0.8)

Parameters:
  - rsi_period:    RSI period (default 14)
  - stoch_period:  Stochastic period applied to RSI (default 14)
  - k_smooth:      K line smoothing (default 3)
  - overbought:    Overbought level (default 0.8)
  - oversold:      Oversold level (default 0.2)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _StochRSIIndicator(bt.Indicator):
    """Stochastic RSI indicator."""

    lines = ("stochrsi", "signal")

    params = (
        ("rsi_period", 14),
        ("stoch_period", 14),
        ("k_smooth", 3),
    )

    def __init__(self):
        rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        rsi_highest = bt.indicators.Highest(rsi, period=self.p.stoch_period)
        rsi_lowest = bt.indicators.Lowest(rsi, period=self.p.stoch_period)
        rsi_range = rsi_highest - rsi_lowest
        raw = bt.DivByZero(rsi - rsi_lowest, rsi_range, zero=0.5)
        self.lines.stochrsi = bt.indicators.SMA(raw, period=self.p.k_smooth)
        self.lines.signal = bt.indicators.SMA(self.lines.stochrsi, period=self.p.k_smooth)


class StochRSIStrategy(BaseStrategy):
    """Stochastic RSI strategy."""

    STRATEGY_META = StrategyMeta(
        id="stoch-rsi",
        name="随机RSI (Stochastic RSI)",
        description=(
            "随机RSI策略：对RSI值再做随机指标处理，得到更灵敏的振荡器。"
            "StochRSI上穿超卖线买入，下穿超买线卖出。"
            "比RSI更早发出信号，但假信号也更多，适合配合其他指标使用。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("rsi_period", "number", 14, "RSI周期", min=7, max=30),
            StrategyParameter("stoch_period", "number", 14, "随机周期", min=7, max=30),
            StrategyParameter("k_smooth", "number", 3, "K平滑周期", min=2, max=7),
            StrategyParameter("overbought", "number", 0.8, "超买阈值", min=0.7, max=0.95, step=0.05),
            StrategyParameter("oversold", "number", 0.2, "超卖阈值", min=0.05, max=0.3, step=0.05),
        ],
    )

    params = (
        ("rsi_period", 14),
        ("stoch_period", 14),
        ("k_smooth", 3),
        ("overbought", 0.8),
        ("oversold", 0.2),
    )

    def __init__(self):
        super().__init__()
        self.stochrsi = _StochRSIIndicator(
            self.data,
            rsi_period=self.p.rsi_period,
            stoch_period=self.p.stoch_period,
            k_smooth=self.p.k_smooth,
        )

    def next(self):
        val = self.stochrsi.stochrsi[0]

        if not self.position:
            if val < self.p.oversold and (len(self) < 2 or self.stochrsi.stochrsi[-1] >= self.p.oversold):
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"StochRSI超卖: {val:.2f} < {self.p.oversold}",
                    )
        else:
            if val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"StochRSI超买: {val:.2f} > {self.p.overbought}",
                )
