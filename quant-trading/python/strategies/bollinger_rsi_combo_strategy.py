"""
Bollinger + RSI Combo Strategy (布林带+RSI组合).

Category: Composite (Basic)
Markets: All

Combines Bollinger Band mean-reversion with RSI confirmation.

Signal logic:
  - BUY:  Price at/below lower Bollinger band + RSI oversold
  - SELL: Price at/above upper Bollinger band + RSI overbought OR mid-band exit

Parameters:
  - bb_period:      Bollinger period (default 20)
  - bb_dev:         Bollinger deviation (default 2.0)
  - rsi_period:     RSI period (default 14)
  - rsi_oversold:   RSI oversold threshold (default 30)
  - rsi_overbought: RSI overbought threshold (default 70)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class BollingerRSIComboStrategy(BaseStrategy):
    """Bollinger Band + RSI combined strategy."""

    STRATEGY_META = StrategyMeta(
        id="bollinger-rsi-combo",
        name="布林带+RSI组合 (Bollinger+RSI Combo)",
        description=(
            "布林带+RSI组合策略：价格触及布林下轨且RSI超卖时买入，"
            "触及上轨且RSI超买时卖出。双重确认减少假信号。"
            "价格到达极端位+动量指标确认=高概率反转信号。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("bb_period", "number", 20, "布林带周期", min=10, max=50),
            StrategyParameter("bb_dev", "number", 2.0, "标准差倍数", min=1.0, max=3.0, step=0.5),
            StrategyParameter("rsi_period", "number", 14, "RSI周期", min=5, max=30),
            StrategyParameter("rsi_oversold", "number", 30, "RSI超卖阈值", min=15, max=40),
            StrategyParameter("rsi_overbought", "number", 70, "RSI超买阈值", min=60, max=85),
        ],
    )

    params = (
        ("bb_period", 20),
        ("bb_dev", 2.0),
        ("rsi_period", 14),
        ("rsi_oversold", 30),
        ("rsi_overbought", 70),
    )

    def __init__(self):
        super().__init__()
        self.boll = bt.indicators.BollingerBands(
            self.data.close, period=self.p.bb_period, devfactor=self.p.bb_dev,
        )
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)

    def next(self):
        close = self.data.close[0]
        rsi_val = self.rsi[0]
        upper = self.boll.top[0]
        lower = self.boll.bot[0]
        mid = self.boll.mid[0]

        if not self.position:
            if close <= lower and rsi_val < self.p.rsi_oversold:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"布林+RSI买入: 价格{close:.2f}<=下轨{lower:.2f}, "
                        f"RSI={rsi_val:.1f}",
                    )
        else:
            if close >= upper and rsi_val > self.p.rsi_overbought:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"布林+RSI卖出: 价格{close:.2f}>=上轨{upper:.2f}, "
                    f"RSI={rsi_val:.1f}",
                )
            elif close >= mid and self.data.close[-1] < self.boll.mid[-1]:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"中轨止盈: 价格穿越中轨{mid:.2f}",
                )
