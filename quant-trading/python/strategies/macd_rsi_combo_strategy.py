"""
MACD + RSI Combo Strategy (MACD+RSI组合).

Category: Composite (Basic)
Markets: All

Combines MACD trend signal with RSI oversold/overbought filter.

Signal logic:
  - BUY:  MACD golden cross + RSI below oversold threshold (double confirmation)
  - SELL: MACD death cross OR RSI above overbought threshold

Parameters:
  - macd_fast:     MACD fast period (default 12)
  - macd_slow:     MACD slow period (default 26)
  - macd_signal:   MACD signal period (default 9)
  - rsi_period:    RSI period (default 14)
  - rsi_oversold:  RSI oversold threshold (default 40)
  - rsi_overbought: RSI overbought threshold (default 70)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MACDRSIComboStrategy(BaseStrategy):
    """MACD + RSI combined strategy."""

    STRATEGY_META = StrategyMeta(
        id="macd-rsi-combo",
        name="MACD+RSI组合 (MACD+RSI Combo)",
        description=(
            "MACD+RSI组合策略：MACD金叉+RSI超卖区双重确认买入，"
            "MACD死叉或RSI超买时卖出。MACD提供趋势方向，RSI过滤虚假信号。"
            "比单一指标更可靠，减少在震荡市中的假信号。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("macd_fast", "number", 12, "MACD快速周期", min=5, max=30),
            StrategyParameter("macd_slow", "number", 26, "MACD慢速周期", min=15, max=60),
            StrategyParameter("macd_signal", "number", 9, "MACD信号周期", min=3, max=15),
            StrategyParameter("rsi_period", "number", 14, "RSI周期", min=5, max=30),
            StrategyParameter("rsi_oversold", "number", 40, "RSI超卖阈值", min=20, max=50),
            StrategyParameter("rsi_overbought", "number", 70, "RSI超买阈值", min=60, max=85),
        ],
    )

    params = (
        ("macd_fast", 12),
        ("macd_slow", 26),
        ("macd_signal", 9),
        ("rsi_period", 14),
        ("rsi_oversold", 40),
        ("rsi_overbought", 70),
    )

    def __init__(self):
        super().__init__()
        self.macd = bt.indicators.MACD(
            self.data.close,
            period_me1=self.p.macd_fast,
            period_me2=self.p.macd_slow,
            period_signal=self.p.macd_signal,
        )
        self.macd_cross = bt.indicators.CrossOver(self.macd.macd, self.macd.signal)
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)

    def next(self):
        rsi_val = self.rsi[0]

        if not self.position:
            if self.macd_cross > 0 and rsi_val < self.p.rsi_oversold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"MACD+RSI买入: MACD金叉, RSI={rsi_val:.1f}<{self.p.rsi_oversold}",
                    )
        else:
            if self.macd_cross < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"MACD死叉卖出: RSI={rsi_val:.1f}",
                )
            elif rsi_val > self.p.rsi_overbought:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"RSI超买卖出: RSI={rsi_val:.1f}>{self.p.rsi_overbought}",
                )
