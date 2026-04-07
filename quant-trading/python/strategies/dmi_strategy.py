"""
DMI / ADX Strategy (趋向指标 / 平均趋向指数).

Category: Trend Following
Markets: All

The Directional Movement Index (DMI) was developed by Welles Wilder.
It consists of:
  - +DI (Plus Directional Indicator): measures upward trend strength
  - -DI (Minus Directional Indicator): measures downward trend strength
  - ADX (Average Directional Index): measures overall trend strength (0-100)

Signal logic:
  - BUY:  +DI crosses above -DI AND ADX > threshold (trending market)
  - SELL: -DI crosses above +DI OR ADX falls below threshold (trend weakening)

Parameters:
  - period:        DI/ADX calculation period (default 14)
  - adx_threshold: Minimum ADX for trend confirmation (default 25)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _ADXIndicator(bt.Indicator):
    """Custom ADX indicator with +DI, -DI, and ADX lines."""

    lines = ("plus_di", "minus_di", "adx")

    params = (("period", 14),)

    def __init__(self):
        # True Range
        tr1 = self.data.high - self.data.low
        tr2 = abs(self.data.high - self.data.close(-1))
        tr3 = abs(self.data.close(-1) - self.data.low)
        tr = bt.Max(tr1, bt.Max(tr2, tr3))

        # +DM and -DM
        up_move = self.data.high - self.data.high(-1)
        down_move = self.data.low(-1) - self.data.low

        plus_dm = bt.If(
            bt.And(up_move > down_move, up_move > 0), up_move, 0.0
        )
        minus_dm = bt.If(
            bt.And(down_move > up_move, down_move > 0), down_move, 0.0
        )

        # Wilder's smoothing (equivalent to EMA with alpha=1/period)
        smooth_tr = bt.indicators.SmoothedMovingAverage(tr, period=self.p.period)
        smooth_plus = bt.indicators.SmoothedMovingAverage(plus_dm, period=self.p.period)
        smooth_minus = bt.indicators.SmoothedMovingAverage(minus_dm, period=self.p.period)

        # +DI and -DI
        self.lines.plus_di = bt.DivByZero(smooth_plus * 100.0, smooth_tr, zero=0.0)
        self.lines.minus_di = bt.DivByZero(smooth_minus * 100.0, smooth_tr, zero=0.0)

        # DX
        di_sum = self.lines.plus_di + self.lines.minus_di
        di_diff = abs(self.lines.plus_di - self.lines.minus_di)
        dx = bt.DivByZero(di_diff * 100.0, di_sum, zero=0.0)

        # ADX = smoothed DX
        self.lines.adx = bt.indicators.SmoothedMovingAverage(dx, period=self.p.period)


class DMIStrategy(BaseStrategy):
    """DMI / ADX trend-following strategy."""

    STRATEGY_META = StrategyMeta(
        id="dmi",
        name="DMI 趋向指标 (DMI/ADX)",
        description=(
            "DMI趋向指标策略：+DI上穿-DI且ADX大于阈值时买入（确认趋势），"
            "-DI上穿+DI或ADX跌破阈值时卖出。"
            "ADX衡量趋势强度，高于25表示趋势明确，低于20表示无趋势。"
            "由Welles Wilder发明，适合捕捉中长期趋势行情。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 14, "DI/ADX计算周期", min=7, max=30),
            StrategyParameter("adx_threshold", "number", 25, "ADX趋势确认阈值", min=15, max=40),
        ],
    )

    params = (
        ("period", 14),
        ("adx_threshold", 25),
    )

    def __init__(self):
        super().__init__()
        self.adx_ind = _ADXIndicator(self.data, period=self.p.period)
        self.di_crossover = bt.indicators.CrossOver(
            self.adx_ind.plus_di, self.adx_ind.minus_di
        )

    def next(self):
        plus_di = self.adx_ind.plus_di[0]
        minus_di = self.adx_ind.minus_di[0]
        adx_val = self.adx_ind.adx[0]

        if not self.position:
            # Buy: +DI crosses above -DI with ADX confirming trend
            if self.di_crossover > 0 and adx_val > self.p.adx_threshold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"DMI买入: +DI={plus_di:.1f} > -DI={minus_di:.1f}, ADX={adx_val:.1f}",
                    )
        else:
            # Sell: -DI crosses above +DI or ADX weakening
            if self.di_crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"DMI死叉: -DI={minus_di:.1f} > +DI={plus_di:.1f}, ADX={adx_val:.1f}",
                )
            elif adx_val < self.p.adx_threshold * 0.7:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"趋势减弱: ADX={adx_val:.1f} < {self.p.adx_threshold * 0.7:.0f}",
                )
