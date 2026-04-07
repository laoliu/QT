"""
MA + Volume Combo Strategy (均线+量能组合).

Category: Composite (Basic)
Markets: All

Combines moving average crossover with volume confirmation.

Signal logic:
  - BUY:  MA golden cross + volume above average (confirmed breakout)
  - SELL: MA death cross OR volume-less rise (divergence)

Parameters:
  - fast_period: Fast MA period (default 10)
  - slow_period: Slow MA period (default 30)
  - vol_mult:    Volume multiplier for confirmation (default 1.5)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MAVolumeComboStrategy(BaseStrategy):
    """Moving Average + Volume combined strategy."""

    STRATEGY_META = StrategyMeta(
        id="ma-volume-combo",
        name="均线+量能组合 (MA+Volume Combo)",
        description=(
            "均线+量能组合策略：均线金叉且放量确认买入，死叉卖出。"
            "量价配合是技术分析的核心原则之一。"
            "金叉+放量=资金推动的真突破，金叉+缩量=假突破。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("fast_period", "number", 10, "快速均线周期", min=3, max=30),
            StrategyParameter("slow_period", "number", 30, "慢速均线周期", min=15, max=120),
            StrategyParameter("vol_mult", "number", 1.5, "放量确认倍数", min=1.0, max=3.0, step=0.1),
        ],
    )

    params = (
        ("fast_period", 10),
        ("slow_period", 30),
        ("vol_mult", 1.5),
    )

    def __init__(self):
        super().__init__()
        self.fast_ma = bt.indicators.SMA(self.data.close, period=self.p.fast_period)
        self.slow_ma = bt.indicators.SMA(self.data.close, period=self.p.slow_period)
        self.crossover = bt.indicators.CrossOver(self.fast_ma, self.slow_ma)
        self.vol_sma = bt.indicators.SMA(self.data.volume, period=20)

    def next(self):
        vol_ratio = (
            self.data.volume[0] / self.vol_sma[0]
            if self.vol_sma[0] > 0
            else 0
        )

        if not self.position:
            if self.crossover > 0 and vol_ratio >= self.p.vol_mult:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"均线金叉+放量: 量比={vol_ratio:.1f}x",
                    )
        else:
            if self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"均线死叉卖出: 量比={vol_ratio:.1f}x",
                )
