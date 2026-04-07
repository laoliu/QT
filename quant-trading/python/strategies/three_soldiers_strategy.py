"""
Three White Soldiers / Three Black Crows Strategy (三兵三鸦).

Category: Pattern / Candlestick
Markets: All

Detects three consecutive bullish/bearish candles as continuation/reversal signals.

Signal logic:
  - BUY:  Three White Soldiers (three consecutive rising bullish candles)
  - SELL: Three Black Crows (three consecutive falling bearish candles)

Parameters:
  - min_body_pct: Min candle body as % of range (default 60%)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ThreeSoldiersStrategy(BaseStrategy):
    """Three White Soldiers / Three Black Crows pattern strategy."""

    STRATEGY_META = StrategyMeta(
        id="three-soldiers",
        name="三兵三鸦 (Three Soldiers/Crows)",
        description=(
            "三兵三鸦形态策略：三根连续上涨大阳线（三白兵）买入，"
            "三根连续下跌大阴线（三黑鸦）卖出。"
            "强烈的趋势延续/反转信号，要求每根K线实体较大。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("min_body_pct", "number", 60, "最小实体占比(%)", min=40, max=80, step=5),
        ],
    )

    params = (
        ("min_body_pct", 60),
    )

    def __init__(self):
        super().__init__()

    def _is_strong_bullish(self, idx):
        o = self.data.open[idx]
        c = self.data.close[idx]
        h = self.data.high[idx]
        l = self.data.low[idx]
        rng = h - l
        if rng <= 0:
            return False
        body = c - o
        return body > 0 and (body / rng * 100) >= self.p.min_body_pct

    def _is_strong_bearish(self, idx):
        o = self.data.open[idx]
        c = self.data.close[idx]
        h = self.data.high[idx]
        l = self.data.low[idx]
        rng = h - l
        if rng <= 0:
            return False
        body = o - c
        return body > 0 and (body / rng * 100) >= self.p.min_body_pct

    def _three_white_soldiers(self):
        if len(self) < 3:
            return False
        for i in range(3):
            if not self._is_strong_bullish(-2 + i):
                return False
        return (self.data.close[-1] > self.data.close[-2]
                and self.data.close[0] > self.data.close[-1])

    def _three_black_crows(self):
        if len(self) < 3:
            return False
        for i in range(3):
            if not self._is_strong_bearish(-2 + i):
                return False
        return (self.data.close[-1] < self.data.close[-2]
                and self.data.close[0] < self.data.close[-1])

    def next(self):
        if not self.position:
            if self._three_white_soldiers():
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", self.data.close[0],
                        "三白兵买入: 连续三根大阳线",
                    )
        else:
            if self._three_black_crows():
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    "三黑鸦卖出: 连续三根大阴线",
                )
