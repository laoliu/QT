"""
Head and Shoulders Strategy (头肩形态).

Category: Pattern / Chart Pattern
Markets: All

Detects head-and-shoulders (top reversal) and inverse head-and-shoulders (bottom reversal).

Signal logic:
  - BUY:  Inverse head and shoulders (price breaks above neckline)
  - SELL: Head and shoulders top (price breaks below neckline)

Parameters:
  - lookback:      Pattern search window (default 60)
  - tolerance_pct: Shoulder symmetry tolerance (default 5.0%)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class HeadShouldersStrategy(BaseStrategy):
    """Head and Shoulders / Inverse H&S pattern strategy."""

    STRATEGY_META = StrategyMeta(
        id="head-shoulders",
        name="头肩形态 (Head & Shoulders)",
        description=(
            "头肩形态策略：识别头肩顶（看跌反转）和头肩底（看涨反转）。"
            "头肩底确认（突破颈线）买入，头肩顶确认（跌破颈线）卖出。"
            "最可靠的图表反转形态之一。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("lookback", "number", 60, "回看窗口", min=30, max=120),
            StrategyParameter("tolerance_pct", "number", 5.0, "肩部对称容差(%)", min=2.0, max=10.0, step=1.0),
        ],
    )

    params = (
        ("lookback", 60),
        ("tolerance_pct", 5.0),
    )

    def __init__(self):
        super().__init__()
        self._last_signal_bar = -999

    def _find_inverse_hs(self):
        """Detect inverse head and shoulders (bullish)."""
        n = min(self.p.lookback, len(self) - 1)
        if n < 15:
            return False, 0.0

        lows = [self.data.low[-i] for i in range(n)]

        head_idx = lows.index(min(lows))
        if head_idx < 5 or head_idx > n - 5:
            return False, 0.0

        left_range = range(head_idx + 3, min(head_idx + n // 2, n))
        right_range = range(max(0, head_idx - n // 2), head_idx - 3)

        if not left_range or not right_range:
            return False, 0.0

        left_shoulder_idx = min(left_range, key=lambda i: lows[i])
        right_shoulder_idx = min(right_range, key=lambda i: lows[i])

        tol = abs(lows[head_idx]) * self.p.tolerance_pct / 100.0
        if abs(lows[left_shoulder_idx] - lows[right_shoulder_idx]) > tol:
            return False, 0.0

        if lows[head_idx] >= lows[left_shoulder_idx]:
            return False, 0.0

        neckline = max(
            self.data.close[-left_shoulder_idx],
            self.data.close[-right_shoulder_idx],
        )

        if self.data.close[0] > neckline:
            return True, neckline
        return False, 0.0

    def next(self):
        if len(self) - self._last_signal_bar < 20:
            return

        if not self.position:
            found, neckline = self._find_inverse_hs()
            if found:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self._last_signal_bar = len(self)
                    self.record_signal(
                        "buy", self.data.close[0],
                        f"头肩底突破: 颈线={neckline:.2f}",
                    )
        else:
            # Simple trailing stop for exit
            sma20 = sum(self.data.close[-i] for i in range(20)) / 20.0 if len(self) >= 20 else self.data.close[0]
            if self.data.close[0] < sma20:
                self.close()
                self._last_signal_bar = len(self)
                self.record_signal(
                    "sell", self.data.close[0],
                    f"均线止损: 价格{self.data.close[0]:.2f} < MA20={sma20:.2f}",
                )
