"""
Double Bottom / Double Top Strategy (双底/双顶形态).

Category: Pattern / Chart Pattern
Markets: All

Detects W-bottom (double bottom) and M-top (double top) price patterns.

Signal logic:
  - BUY:  Double bottom confirmed (price breaks above neckline)
  - SELL: Double top confirmed (price breaks below neckline)

Parameters:
  - lookback:      Window to search for pattern (default 40)
  - tolerance_pct: Max % difference between two bottoms/tops (default 3.0%)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DoubleBottomStrategy(BaseStrategy):
    """Double Bottom / Double Top pattern recognition strategy."""

    STRATEGY_META = StrategyMeta(
        id="double-bottom",
        name="双底双顶 (Double Bottom/Top)",
        description=(
            "双底双顶形态策略：识别W底（双底）和M顶（双顶）形态。"
            "双底确认（突破颈线）买入，双顶确认（跌破颈线）卖出。"
            "经典的图表形态分析方法，需要足够的回看窗口。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("lookback", "number", 40, "回看窗口", min=20, max=80),
            StrategyParameter("tolerance_pct", "number", 3.0, "双底/顶容差(%)", min=1.0, max=5.0, step=0.5),
        ],
    )

    params = (
        ("lookback", 40),
        ("tolerance_pct", 3.0),
    )

    def __init__(self):
        super().__init__()
        self._last_buy_bar = -999

    def _find_double_bottom(self):
        if len(self) < self.p.lookback:
            return False, 0.0

        lows = [self.data.low[-i] for i in range(self.p.lookback)]
        closes = [self.data.close[-i] for i in range(self.p.lookback)]

        min1_idx = lows.index(min(lows))
        if min1_idx < 5 or min1_idx > self.p.lookback - 5:
            return False, 0.0

        search_range = [i for i in range(len(lows)) if abs(i - min1_idx) > 5]
        if not search_range:
            return False, 0.0

        min2_idx = min(search_range, key=lambda i: lows[i])
        tol = lows[min1_idx] * self.p.tolerance_pct / 100.0

        if abs(lows[min1_idx] - lows[min2_idx]) > tol:
            return False, 0.0

        left, right = min(min1_idx, min2_idx), max(min1_idx, min2_idx)
        neckline = max(closes[left:right + 1]) if right > left else closes[left]

        if self.data.close[0] > neckline:
            return True, neckline
        return False, 0.0

    def _find_double_top(self):
        if len(self) < self.p.lookback:
            return False, 0.0

        highs = [self.data.high[-i] for i in range(self.p.lookback)]
        closes = [self.data.close[-i] for i in range(self.p.lookback)]

        max1_idx = highs.index(max(highs))
        if max1_idx < 5 or max1_idx > self.p.lookback - 5:
            return False, 0.0

        search_range = [i for i in range(len(highs)) if abs(i - max1_idx) > 5]
        if not search_range:
            return False, 0.0

        max2_idx = max(search_range, key=lambda i: highs[i])
        tol = highs[max1_idx] * self.p.tolerance_pct / 100.0

        if abs(highs[max1_idx] - highs[max2_idx]) > tol:
            return False, 0.0

        left, right = min(max1_idx, max2_idx), max(max1_idx, max2_idx)
        neckline = min(closes[left:right + 1]) if right > left else closes[left]

        if self.data.close[0] < neckline:
            return True, neckline
        return False, 0.0

    def next(self):
        if not self.position:
            found, neckline = self._find_double_bottom()
            if found and len(self) - self._last_buy_bar > self.p.lookback:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self._last_buy_bar = len(self)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"双底突破: 颈线={neckline:.2f}, 价格{self.data.close[0]:.2f}",
                    )
        else:
            found, neckline = self._find_double_top()
            if found:
                self.close()
                self.record_signal(
                    "sell", self.data.close[0],
                    f"双顶跌破: 颈线={neckline:.2f}, 价格{self.data.close[0]:.2f}",
                )
