"""
KDJ + MACD Combo Strategy (KDJ+MACD组合).

Category: Composite (Basic)
Markets: All (especially A-shares)

Combines KDJ oversold/overbought signals with MACD trend confirmation.

Signal logic:
  - BUY:  KDJ golden cross in oversold zone + MACD histogram positive or rising
  - SELL: KDJ death cross in overbought zone OR MACD death cross

Parameters:
  - k_period:    KDJ K period (default 9)
  - d_period:    KDJ D period (default 3)
  - overbought:  KDJ overbought (default 80)
  - oversold:    KDJ oversold (default 20)
  - macd_fast:   MACD fast period (default 12)
  - macd_slow:   MACD slow period (default 26)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _KDJForCombo(bt.Indicator):
    """KDJ indicator for combo strategy."""

    lines = ("k", "d", "j")
    params = (("k_period", 9), ("d_period", 3))

    def __init__(self):
        highest = bt.indicators.Highest(self.data.high, period=self.p.k_period)
        lowest = bt.indicators.Lowest(self.data.low, period=self.p.k_period)
        hl_diff = highest - lowest
        rsv = bt.If(hl_diff > 0, (self.data.close - lowest) / hl_diff * 100, 50.0)
        self.lines.k = bt.indicators.SMA(rsv, period=self.p.d_period)
        self.lines.d = bt.indicators.SMA(self.lines.k, period=self.p.d_period)
        self.lines.j = 3.0 * self.lines.k - 2.0 * self.lines.d


class KDJMACDComboStrategy(BaseStrategy):
    """KDJ + MACD combined strategy."""

    STRATEGY_META = StrategyMeta(
        id="kdj-macd-combo",
        name="KDJ+MACD组合 (KDJ+MACD Combo)",
        description=(
            "KDJ+MACD组合策略：KDJ超卖金叉+MACD柱状图为正/上升时买入，"
            "KDJ超买死叉或MACD死叉时卖出。"
            "KDJ提供精确的超买超卖信号，MACD确认趋势方向，适合A股短线操作。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("k_period", "number", 9, "KDJ K周期", min=5, max=20),
            StrategyParameter("d_period", "number", 3, "KDJ D周期", min=2, max=10),
            StrategyParameter("overbought", "number", 80, "KDJ超买阈值", min=70, max=90),
            StrategyParameter("oversold", "number", 20, "KDJ超卖阈值", min=10, max=30),
            StrategyParameter("macd_fast", "number", 12, "MACD快速周期", min=5, max=20),
            StrategyParameter("macd_slow", "number", 26, "MACD慢速周期", min=15, max=50),
        ],
    )

    params = (
        ("k_period", 9),
        ("d_period", 3),
        ("overbought", 80),
        ("oversold", 20),
        ("macd_fast", 12),
        ("macd_slow", 26),
    )

    def __init__(self):
        super().__init__()
        self.kdj = _KDJForCombo(
            self.data, k_period=self.p.k_period, d_period=self.p.d_period,
        )
        self.kdj_cross = bt.indicators.CrossOver(self.kdj.k, self.kdj.d)
        self.macd = bt.indicators.MACD(
            self.data.close,
            period_me1=self.p.macd_fast,
            period_me2=self.p.macd_slow,
        )
        self.macd_cross = bt.indicators.CrossOver(self.macd.macd, self.macd.signal)

    def next(self):
        k_val = self.kdj.k[0]
        macd_hist = self.macd.macd[0] - self.macd.signal[0]

        if not self.position:
            if self.kdj_cross > 0 and k_val < self.p.oversold and macd_hist >= 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"KDJ+MACD买入: K={k_val:.1f}(超卖金叉), "
                        f"MACD柱={macd_hist:.4f}",
                    )
        else:
            if self.kdj_cross < 0 and k_val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"KDJ超买死叉卖出: K={k_val:.1f}",
                )
            elif self.macd_cross < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"MACD死叉卖出: MACD柱={macd_hist:.4f}",
                )
