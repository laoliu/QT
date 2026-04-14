"""
Momentum Strategy (动量策略).

Category: Trend / Momentum
Markets: All

Signal logic:
  - BUY:  N-day momentum turns positive and is accelerating
  - SELL: N-day momentum turns negative

Parameters:
  - period:     Momentum period (default 12)
  - ma_period:  Momentum smoothing period (default 6)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MomentumStrategy(BaseStrategy):
    """Momentum trend strategy."""

    STRATEGY_META = StrategyMeta(
        id="momentum",
        name="动量策略 (Momentum)",
        description=(
            "动量策略：N日动量转正且加速时买入，转负时卖出。"
            "动量 = 当前价格 - N日前价格，衡量价格变化的速率。"
            "适合追踪强势股和趋势加速行情。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 12, "动量周期", min=5, max=30),
            StrategyParameter("ma_period", "number", 6, "动量平滑周期", min=3, max=15),
        ],
    )

    params = (
        ("period", 12),
        ("ma_period", 6),
    )

    def __init__(self):
        super().__init__()
        self.mom = bt.indicators.Momentum(self.data.close, period=self.p.period)
        self.mom_ma = bt.indicators.SMA(self.mom, period=self.p.ma_period)

    def next(self):
        mom_val = self.mom[0]
        mom_ma = self.mom_ma[0]

        if not self.position:
            if mom_val > 0 and mom_val > mom_ma:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"动量买入: 动量={mom_val:.2f} > 均值={mom_ma:.2f}",
                    )
        else:
            if mom_val < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"动量卖出: 动量={mom_val:.2f} < 0",
                )
