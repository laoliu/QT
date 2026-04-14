"""
Three Soldiers / Three Crows Strategy (三兵三鸦).

Category: Pattern Recognition
Markets: All

Signal logic:
  - BUY:  Three white soldiers (3 consecutive bullish candles with higher closes)
  - SELL: Three black crows (3 consecutive bearish candles with lower closes)

Parameters:
  - min_body_pct: Minimum body as % of range for each candle (default 50%)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ThreeSoldiersStrategy(BaseStrategy):
    """Three White Soldiers / Three Black Crows strategy."""

    STRATEGY_META = StrategyMeta(
        id="three-soldiers",
        name="三兵三鸦 (Three Soldiers/Crows)",
        description=(
            "三兵三鸦策略：三连阳兵买入，三连阴鸦卖出。"
            "三白兵：连续3根阳线且收盘价递增，强烈看涨信号。"
            "三黑鸦：连续3根阴线且收盘价递减，强烈看跌信号。"
            "要求每根K线实体饱满（体量占比>50%）。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("min_body_pct", "number", 50, "最小实体占比(%)", min=30, max=80, step=5),
        ],
    )

    params = (("min_body_pct", 50),)

    def _is_solid_bullish(self, offset: int) -> bool:
        body = self.data.close[offset] - self.data.open[offset]
        full_range = self.data.high[offset] - self.data.low[offset]
        if full_range <= 0 or body <= 0:
            return False
        return (body / full_range * 100) >= self.p.min_body_pct

    def _is_solid_bearish(self, offset: int) -> bool:
        body = self.data.open[offset] - self.data.close[offset]
        full_range = self.data.high[offset] - self.data.low[offset]
        if full_range <= 0 or body <= 0:
            return False
        return (body / full_range * 100) >= self.p.min_body_pct

    def _is_three_soldiers(self) -> bool:
        for i in (-2, -1, 0):
            if not self._is_solid_bullish(i):
                return False
        return (
            self.data.close[0] > self.data.close[-1] > self.data.close[-2]
            and self.data.open[-1] > self.data.open[-2]
            and self.data.open[0] > self.data.open[-1]
        )

    def _is_three_crows(self) -> bool:
        for i in (-2, -1, 0):
            if not self._is_solid_bearish(i):
                return False
        return (
            self.data.close[0] < self.data.close[-1] < self.data.close[-2]
            and self.data.open[-1] < self.data.open[-2]
            and self.data.open[0] < self.data.open[-1]
        )

    def next(self):
        if not self.position:
            if self._is_three_soldiers():
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        "三白兵买入: 连续3根实体阳线递增",
                    )
        else:
            if self._is_three_crows():
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    "三黑鸦卖出: 连续3根实体阴线递减",
                )
