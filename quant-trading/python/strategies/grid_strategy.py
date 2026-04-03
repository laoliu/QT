"""
Grid Trading Strategy (网格交易).

Category: Range / Mean-Reversion
Markets: All (works best in range-bound / sideways markets)

A systematic strategy that places buy/sell orders at fixed price intervals
(grids) around the current price, profiting from price oscillations.

Core concepts:
  - Divide the price range into evenly spaced grid levels
  - When price drops to a grid level → buy a fixed portion
  - When price rises to the next grid level → sell that portion
  - Each grid "trip" captures the grid spacing as profit
  - No directional bet — profits accumulate from oscillation

Advantages:
  - Works well in sideways/range-bound markets
  - Fully mechanical — no prediction of direction needed
  - Each grid transaction captures a small, repeatable profit
  - Naturally dollar-cost-averages on the way down

Risks:
  - Poor in strong trends (buys all the way down in a crash)
  - Capital inefficient — lots of cash needed for lower grids
  - Transaction costs can eat into profits with small grid spacing

Parameters:
  - grid_pct:           Grid spacing as percentage (default 2.0%)
  - grid_count:         Number of grids above and below center (default 10)
  - position_per_grid:  Fraction of total equity per grid level (default 0.08)
  - center_price:       Grid center price, 0 = use first close (default 0)
"""

from __future__ import annotations

import sys

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class GridStrategy(BaseStrategy):
    """Grid Trading: systematic buy-low-sell-high at fixed intervals."""

    STRATEGY_META = StrategyMeta(
        id="grid",
        name="网格交易 (Grid Trading)",
        description=(
            "网格交易策略：在当前价格上下设置等距的网格线，"
            "价格每下跌一格买入固定仓位，每上涨一格卖出对应仓位。"
            "通过反复低买高卖在震荡行情中持续获利，无需预测方向。"
            "适用于波动率适中的横盘震荡行情，趋势行情中表现较差。"
        ),
        category="range",
        parameters=[
            StrategyParameter("grid_pct", "number", 2.0, "网格间距（%）", min=0.5, max=10.0, step=0.5),
            StrategyParameter("grid_count", "number", 10, "单侧网格数", min=3, max=30),
            StrategyParameter(
                "position_per_grid", "number", 0.08, "每格仓位比例", min=0.02, max=0.20, step=0.01
            ),
            StrategyParameter("center_price", "number", 0, "网格中心价（0=自动）", min=0, max=999999),
        ],
    )

    params = (
        ("grid_pct", 2.0),
        ("grid_count", 10),
        ("position_per_grid", 0.08),
        ("center_price", 0),
    )

    def __init__(self):
        super().__init__()
        self._grids_built = False
        self._grid_levels: list[float] = []
        # Track which grid levels have been "filled" (bought at)
        self._filled: dict[int, int] = {}  # grid_index → shares held
        self._last_grid_index = -1

    def _build_grids(self, center: float):
        """Build grid levels symmetrically around the center price."""
        pct = self.p.grid_pct / 100.0
        levels = []
        for i in range(-self.p.grid_count, self.p.grid_count + 1):
            level = center * (1 + i * pct)
            if level > 0:
                levels.append(level)
        self._grid_levels = sorted(levels)
        self._grids_built = True
        self._filled = {}

        print(
            f"Grid built: center={center:.2f}, "
            f"range=[{self._grid_levels[0]:.2f}, {self._grid_levels[-1]:.2f}], "
            f"levels={len(self._grid_levels)}",
            file=sys.stderr,
        )

    def _find_grid_index(self, price: float) -> int:
        """Find the grid index that the current price is closest to (below)."""
        for i in range(len(self._grid_levels) - 1, -1, -1):
            if price >= self._grid_levels[i]:
                return i
        return 0

    def next(self):
        close = self.data.close[0]

        # Build grids on first bar (or if center_price was explicitly set)
        if not self._grids_built:
            center = self.p.center_price if self.p.center_price > 0 else close
            self._build_grids(center)
            self._last_grid_index = self._find_grid_index(close)
            return

        current_index = self._find_grid_index(close)

        if current_index == self._last_grid_index:
            return  # Price hasn't crossed any grid line

        # ── Price moved DOWN through grid levels → BUY ──
        if current_index < self._last_grid_index:
            for gi in range(self._last_grid_index - 1, current_index - 1, -1):
                if gi < 0 or gi >= len(self._grid_levels):
                    continue
                if gi in self._filled:
                    continue  # Already filled at this grid

                grid_price = self._grid_levels[gi]
                equity = self.broker.getvalue()
                size = int(equity * self.p.position_per_grid / close)
                if size >= 100:
                    size = (size // 100) * 100
                if size <= 0:
                    continue

                # Check if we have enough cash
                cost = size * close
                if cost > self.broker.getcash():
                    continue

                self.buy(size=size)
                self._filled[gi] = size
                self.record_signal(
                    "buy",
                    close,
                    f"网格买入: 触及第{gi}层网格 ¥{grid_price:.2f}, "
                    f"买入{size}股@{close:.2f}",
                )

        # ── Price moved UP through grid levels → SELL ──
        elif current_index > self._last_grid_index:
            for gi in range(self._last_grid_index + 1, current_index + 1):
                if gi < 0 or gi >= len(self._grid_levels):
                    continue

                # Sell the position bought at grid (gi - 1) or the closest filled grid below
                sell_grid = None
                for check in range(gi - 1, -1, -1):
                    if check in self._filled:
                        sell_grid = check
                        break

                if sell_grid is None:
                    continue  # Nothing to sell

                size = self._filled[sell_grid]
                # Make sure we have enough shares
                pos = self.getposition(self.data).size
                size = min(size, pos)
                if size <= 0:
                    continue

                self.sell(size=size)
                grid_price = self._grid_levels[gi]
                buy_price = self._grid_levels[sell_grid]
                profit_pct = (grid_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
                del self._filled[sell_grid]
                self.record_signal(
                    "sell",
                    close,
                    f"网格卖出: 触及第{gi}层网格 ¥{grid_price:.2f}, "
                    f"卖出{size}股@{close:.2f}, "
                    f"网格利润≈{profit_pct:.1f}%",
                )

        self._last_grid_index = current_index
