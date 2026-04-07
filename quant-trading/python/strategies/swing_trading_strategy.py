"""
Swing Trading Strategy (波段交易).

Category: Composite / Advanced
Markets: All

Identifies swing highs/lows and trades the swings between them.

Signal logic:
  - BUY:  Price bounces from swing low with RSI oversold confirmation
  - SELL: Price reaches swing high or RSI overbought

Parameters:
  - swing_period:  Period for swing point detection (default 5)
  - rsi_period:    RSI period (default 14)
  - rsi_oversold:  RSI oversold level (default 35)
  - rsi_overbought: RSI overbought level (default 65)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class SwingTradingStrategy(BaseStrategy):
    """Swing high/low trading strategy with RSI confirmation."""

    STRATEGY_META = StrategyMeta(
        id="swing-trading",
        name="波段交易 (Swing Trading)",
        description=(
            "波段交易策略：识别价格的波段高点和低点，在低点处配合RSI超卖确认买入，"
            "在高点处配合RSI超买确认卖出。适合中短线操作，捕捉完整的波段行情。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("swing_period", "number", 5, "波段识别周期", min=3, max=10),
            StrategyParameter("rsi_period", "number", 14, "RSI周期", min=7, max=30),
            StrategyParameter("rsi_oversold", "number", 35, "RSI超卖阈值", min=20, max=45),
            StrategyParameter("rsi_overbought", "number", 65, "RSI超买阈值", min=55, max=80),
        ],
    )

    params = (
        ("swing_period", 5),
        ("rsi_period", 14),
        ("rsi_oversold", 35),
        ("rsi_overbought", 65),
    )

    def __init__(self):
        super().__init__()
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.swing_period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.swing_period)

    def next(self):
        close = self.data.close[0]
        rsi_val = self.rsi[0]

        if not self.position:
            is_swing_low = close <= self.lowest[0]
            if is_swing_low and rsi_val < self.p.rsi_oversold:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"波段低点: RSI={rsi_val:.1f}, 低点={self.lowest[0]:.2f}",
                    )
        else:
            is_swing_high = close >= self.highest[0]
            if is_swing_high or rsi_val > self.p.rsi_overbought:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"波段高点: RSI={rsi_val:.1f}, 高点={self.highest[0]:.2f}",
                )
