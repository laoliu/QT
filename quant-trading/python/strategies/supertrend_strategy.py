"""
Supertrend Strategy (超级趋势).

Category: Trend Following
Markets: All

Supertrend is a trend-following indicator based on ATR (Average True Range).
It plots a trailing stop line that flips between support and resistance.

Calculation:
  - Upper Band = (High + Low) / 2 + Multiplier * ATR
  - Lower Band = (High + Low) / 2 - Multiplier * ATR
  - When price closes above the upper band, direction flips to UP (bullish)
  - When price closes below the lower band, direction flips to DOWN (bearish)

Signal logic:
  - BUY:  Close > Supertrend (direction changes to up / bullish)
  - SELL: Close < Supertrend (direction changes to down / bearish)

Parameters:
  - atr_period:  ATR calculation period (default 10)
  - multiplier:  ATR multiplier for band width (default 3.0)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _SupertrendIndicator(bt.Indicator):
    """Custom Supertrend indicator based on ATR.

    Lines:
      - supertrend: the trailing stop value (support in uptrend, resistance in downtrend)
      - direction:  1.0 for uptrend (bullish), -1.0 for downtrend (bearish)
    """

    lines = ("supertrend", "direction")

    params = (
        ("atr_period", 10),
        ("multiplier", 3.0),
    )

    def __init__(self):
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        # HL2 = (High + Low) / 2
        self.hl2 = (self.data.high + self.data.low) / 2.0

    def nextstart(self):
        """Initialize on the first bar with enough data."""
        hl2 = self.hl2[0]
        atr_val = self.atr[0]
        upper = hl2 + self.p.multiplier * atr_val
        lower = hl2 - self.p.multiplier * atr_val

        # Start in uptrend
        self.lines.supertrend[0] = lower
        self.lines.direction[0] = 1.0
        self._prev_upper = upper
        self._prev_lower = lower

    def next(self):
        hl2 = self.hl2[0]
        atr_val = self.atr[0]
        close = self.data.close[0]
        prev_close = self.data.close[-1]

        # Raw bands
        upper = hl2 + self.p.multiplier * atr_val
        lower = hl2 - self.p.multiplier * atr_val

        # Tighten bands: keep the tighter (more favorable) band
        if lower > self._prev_lower:
            lower = lower
        elif prev_close > self._prev_lower:
            lower = lower
        else:
            lower = self._prev_lower

        if upper < self._prev_upper:
            upper = upper
        elif prev_close < self._prev_upper:
            upper = upper
        else:
            upper = self._prev_upper

        # Determine direction
        prev_dir = self.lines.direction[-1]
        if prev_dir == 1.0:
            # Was uptrend
            if close < lower:
                # Flip to downtrend
                self.lines.direction[0] = -1.0
                self.lines.supertrend[0] = upper
            else:
                self.lines.direction[0] = 1.0
                self.lines.supertrend[0] = lower
        else:
            # Was downtrend
            if close > upper:
                # Flip to uptrend
                self.lines.direction[0] = 1.0
                self.lines.supertrend[0] = lower
            else:
                self.lines.direction[0] = -1.0
                self.lines.supertrend[0] = upper

        # Save for next bar
        self._prev_upper = upper
        self._prev_lower = lower


class SupertrendStrategy(BaseStrategy):
    """Supertrend trend-following trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="supertrend",
        name="超级趋势 (Supertrend)",
        description=(
            "超级趋势策略：基于ATR（平均真实波幅）构建动态支撑/阻力线。"
            "价格在超级趋势线上方时为上升趋势（买入），下方时为下降趋势（卖出）。"
            "参数简单，信号明确，适合中长期趋势跟踪。"
            "ATR倍数越大，信号越少但越可靠；倍数越小，信号越灵敏但假信号越多。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("atr_period", "number", 10, "ATR计算周期", min=5, max=30),
            StrategyParameter(
                "multiplier", "number", 3.0, "ATR倍数", min=1.0, max=5.0, step=0.1
            ),
        ],
    )

    params = (
        ("atr_period", 10),
        ("multiplier", 3.0),
    )

    def __init__(self):
        super().__init__()
        self.supertrend = _SupertrendIndicator(
            self.data,
            atr_period=self.p.atr_period,
            multiplier=self.p.multiplier,
        )

    def next(self):
        close = self.data.close[0]
        st_val = self.supertrend.supertrend[0]
        direction = self.supertrend.direction[0]
        prev_direction = self.supertrend.direction[-1]

        if not self.position:
            # BUY: direction flips to uptrend
            if direction == 1.0 and prev_direction == -1.0:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"超级趋势买入: 价格={close:.2f} > 趋势线={st_val:.2f}, "
                        f"方向翻转为上升趋势",
                    )
        else:
            # SELL: direction flips to downtrend
            if direction == -1.0 and prev_direction == 1.0:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"超级趋势卖出: 价格={close:.2f} < 趋势线={st_val:.2f}, "
                    f"方向翻转为下降趋势",
                )
