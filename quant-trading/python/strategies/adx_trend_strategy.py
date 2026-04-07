"""
ADX Trend Strength Strategy (ADX 趋势强度).

Category: Trend Following (CTA)
Markets: All

Uses ADX (Average Directional Index) to measure trend strength,
combined with +DI/-DI for direction.

Signal logic:
  - BUY:  ADX > threshold AND +DI > -DI (strong uptrend)
  - SELL: ADX > threshold AND -DI > +DI (strong downtrend) OR ADX drops below exit_threshold

Parameters:
  - adx_period:      ADX calculation period (default 14)
  - adx_threshold:   Minimum ADX for trend entry (default 25)
  - exit_threshold:  ADX below this → exit (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ADXTrendStrategy(BaseStrategy):
    """ADX-based trend strength strategy."""

    STRATEGY_META = StrategyMeta(
        id="adx-trend",
        name="ADX趋势强度 (ADX Trend)",
        description=(
            "ADX趋势策略：ADX高于阈值表示趋势强劲，结合+DI/-DI方向线判断多空方向。"
            "ADX>25且+DI>-DI时买入，ADX走弱或-DI>+DI时卖出。"
            "适合过滤震荡市的假信号，仅在趋势明确时交易。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("adx_period", "number", 14, "ADX周期", min=7, max=30),
            StrategyParameter("adx_threshold", "number", 25, "ADX入场阈值", min=15, max=40),
            StrategyParameter("exit_threshold", "number", 20, "ADX退场阈值", min=10, max=30),
        ],
    )

    params = (
        ("adx_period", 14),
        ("adx_threshold", 25),
        ("exit_threshold", 20),
    )

    def __init__(self):
        super().__init__()
        self.adx = bt.indicators.AverageDirectionalMovementIndex(
            self.data, period=self.p.adx_period
        )
        self.plus_di = bt.indicators.PlusDirectionalIndicator(
            self.data, period=self.p.adx_period
        )
        self.minus_di = bt.indicators.MinusDirectionalIndicator(
            self.data, period=self.p.adx_period
        )

    def next(self):
        adx_val = self.adx[0]
        plus = self.plus_di[0]
        minus = self.minus_di[0]

        if not self.position:
            if adx_val > self.p.adx_threshold and plus > minus:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"ADX趋势买入: ADX={adx_val:.1f}, +DI={plus:.1f} > -DI={minus:.1f}",
                    )
        else:
            if adx_val < self.p.exit_threshold or minus > plus:
                self.close()
                reason = (
                    f"ADX趋势减弱: ADX={adx_val:.1f}"
                    if adx_val < self.p.exit_threshold
                    else f"方向反转: -DI={minus:.1f} > +DI={plus:.1f}"
                )
                self.record_signal("sell", self.data.close[0], reason)
