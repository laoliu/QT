"""
Heikin-Ashi Trend Strategy (平均K线趋势).

Category: Composite / Advanced
Markets: All

Uses Heikin-Ashi candles (smoothed candlesticks) to identify trend direction.

Signal logic:
  - BUY:  HA candle turns bullish (no lower shadow = strong uptrend)
  - SELL: HA candle turns bearish (no upper shadow = strong downtrend)

Parameters:
  - confirm_bars: Consecutive HA bars for confirmation (default 3)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class HeikinAshiStrategy(BaseStrategy):
    """Heikin-Ashi smoothed candlestick trend strategy."""

    STRATEGY_META = StrategyMeta(
        id="heikin-ashi",
        name="平均K线 (Heikin-Ashi)",
        description=(
            "平均K线趋势策略：Heikin-Ashi对K线进行平均化处理，滤除噪音。"
            "连续N根HA阳线（无下影线为强势）买入，连续N根HA阴线卖出。"
            "趋势更直观，减少日K线的随机波动干扰。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("confirm_bars", "number", 3, "确认K线数", min=2, max=5),
        ],
    )

    params = (
        ("confirm_bars", 3),
    )

    def __init__(self):
        super().__init__()
        self._ha_close_history = []
        self._ha_open_history = []

    def _ha_values(self):
        """Compute Heikin-Ashi OHLC for current bar."""
        o = self.data.open[0]
        h = self.data.high[0]
        l = self.data.low[0]
        c = self.data.close[0]

        ha_close = (o + h + l + c) / 4.0

        if self._ha_open_history:
            ha_open = (self._ha_open_history[-1] + self._ha_close_history[-1]) / 2.0
        else:
            ha_open = (o + c) / 2.0

        ha_high = max(h, ha_open, ha_close)
        ha_low = min(l, ha_open, ha_close)

        return ha_open, ha_high, ha_low, ha_close

    def next(self):
        ha_open, ha_high, ha_low, ha_close = self._ha_values()
        self._ha_open_history.append(ha_open)
        self._ha_close_history.append(ha_close)

        if len(self._ha_close_history) > 100:
            self._ha_open_history = self._ha_open_history[-100:]
            self._ha_close_history = self._ha_close_history[-100:]

        n = self.p.confirm_bars
        if len(self._ha_close_history) < n:
            return

        bullish_count = 0
        bearish_count = 0
        for i in range(n):
            idx = -(i + 1)
            if idx < -len(self._ha_close_history):
                break
            hc = self._ha_close_history[idx]
            ho = self._ha_open_history[idx]
            if hc > ho:
                bullish_count += 1
            elif hc < ho:
                bearish_count += 1

        if not self.position:
            if bullish_count == n:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"HA多头: 连续{n}根HA阳线",
                    )
        else:
            if bearish_count == n:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"HA空头: 连续{n}根HA阴线",
                )
