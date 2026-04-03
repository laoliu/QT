"""
Turtle Trading Strategy (海龟交易法).

Category: Trend Following
Markets: All

The legendary strategy from Richard Dennis & William Eckhardt (1983).
One of the most rigorously backtested trend-following systems ever created.

Core concepts:
  - Entry: Buy when price breaks N-day high (Donchian channel breakout)
  - Exit: Sell when price breaks N-day low
  - Position sizing: ATR-based (1 unit = 1% equity / ATR)
  - Pyramiding: Add up to max_units positions as trend continues

System 1 (short-term):
  - Entry: 20-day breakout
  - Exit: 10-day breakout in opposite direction
  - Skip entry if previous breakout was winner (anti-trend filter)

System 2 (long-term):
  - Entry: 55-day breakout
  - Exit: 20-day breakout in opposite direction
  - Never skip

This implementation uses System 1 by default with ATR-based position sizing.

Parameters:
  - entry_period: Donchian channel period for entry (default 20)
  - exit_period:  Donchian channel period for exit (default 10)
  - atr_period:   ATR calculation period (default 20)
  - risk_pct:     Risk per unit as fraction of equity (default 0.01 = 1%)
  - max_units:    Maximum number of units (pyramid levels, default 4)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class TurtleStrategy(BaseStrategy):
    """Turtle Trading System (System 1)."""

    STRATEGY_META = StrategyMeta(
        id="turtle",
        name="海龟交易法 (Turtle Trading)",
        description=(
            "经典海龟交易法：价格突破N日最高价买入，跌破N日最低价卖出。"
            "使用ATR（平均真实波幅）进行仓位管理，每个单位风险为总资产的1%。"
            "最多加仓4次（金字塔加仓）。由Richard Dennis和William Eckhardt于1983年发明，"
            "是历史上最著名的趋势跟踪系统之一。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("entry_period", "number", 20, "突破周期（入场）", min=10, max=120),
            StrategyParameter("exit_period", "number", 10, "突破周期（出场）", min=5, max=60),
            StrategyParameter("atr_period", "number", 20, "ATR周期", min=10, max=50),
            StrategyParameter("risk_pct", "number", 0.01, "每单位风险比例", min=0.005, max=0.05, step=0.005),
            StrategyParameter("max_units", "number", 4, "最大加仓次数", min=1, max=8),
        ],
    )

    params = (
        ("entry_period", 20),
        ("exit_period", 10),
        ("atr_period", 20),
        ("risk_pct", 0.01),
        ("max_units", 4),
    )

    def __init__(self):
        super().__init__()

        # Donchian channel for entry
        self.entry_high = bt.indicators.Highest(
            self.data.high, period=self.p.entry_period
        )
        self.entry_low = bt.indicators.Lowest(
            self.data.low, period=self.p.entry_period
        )

        # Donchian channel for exit
        self.exit_high = bt.indicators.Highest(
            self.data.high, period=self.p.exit_period
        )
        self.exit_low = bt.indicators.Lowest(
            self.data.low, period=self.p.exit_period
        )

        # ATR for position sizing and stop-loss
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)

        # Tracking variables
        self._units = 0           # Current number of units
        self._last_entry_price = 0  # Last entry price for pyramiding
        self._stop_price = 0      # Dynamic stop-loss

    def next(self):
        atr_val = self.atr[0]
        if atr_val <= 0:
            return

        close = self.data.close[0]

        if not self.position:
            # ── No position: look for breakout entry ──
            self._units = 0

            # Buy when close breaks above entry_period high
            if close > self.entry_high[-1]:
                unit_size = self._calc_unit_size(atr_val)
                if unit_size > 0:
                    self.buy(size=unit_size)
                    self._units = 1
                    self._last_entry_price = close
                    self._stop_price = close - 2 * atr_val
                    self.record_signal(
                        "buy",
                        close,
                        f"海龟突破入场: 价格{close:.2f} > {self.p.entry_period}日高点{self.entry_high[-1]:.2f}, "
                        f"ATR={atr_val:.2f}, 止损={self._stop_price:.2f}",
                    )

        else:
            # ── Have position: manage pyramiding, stop-loss, exit ──

            # 1. Stop-loss check (highest priority)
            if close < self._stop_price:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"止损出场: 价格{close:.2f} < 止损价{self._stop_price:.2f}",
                )
                self._units = 0
                return

            # 2. Exit on exit_period low breakout
            if close < self.exit_low[-1]:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"通道出场: 价格{close:.2f} < {self.p.exit_period}日低点{self.exit_low[-1]:.2f}",
                )
                self._units = 0
                return

            # 3. Pyramiding: add units every 0.5 ATR above last entry
            if self._units < self.p.max_units:
                if close >= self._last_entry_price + 0.5 * atr_val:
                    unit_size = self._calc_unit_size(atr_val)
                    if unit_size > 0:
                        self.buy(size=unit_size)
                        self._units += 1
                        self._last_entry_price = close
                        # Move stop-loss up: 2 ATR below newest entry
                        self._stop_price = close - 2 * atr_val
                        self.record_signal(
                            "buy",
                            close,
                            f"金字塔加仓(第{self._units}层): 价格{close:.2f}, "
                            f"新止损={self._stop_price:.2f}",
                        )

    def _calc_unit_size(self, atr_val: float) -> int:
        """Calculate Turtle unit size: 1% of equity / ATR."""
        equity = self.broker.getvalue()
        dollar_vol = atr_val  # Per share ATR
        if dollar_vol <= 0:
            return 0
        unit = int(equity * self.p.risk_pct / dollar_vol)
        # Round to lot of 100 for A-shares, keep raw for global stocks
        if unit >= 100:
            unit = (unit // 100) * 100
        return max(unit, 0)
