"""
Stochastic Oscillator Strategy (随机振荡器).

Category: Oscillator
Markets: All

Signal logic:
  - BUY:  %K crosses above %D in oversold zone
  - SELL: %K crosses below %D in overbought zone

Parameters:
  - k_period:    %K period (default 14)
  - d_period:    %D smoothing period (default 3)
  - overbought:  Overbought threshold (default 80)
  - oversold:    Oversold threshold (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class StochasticStrategy(BaseStrategy):
    """Stochastic oscillator strategy."""

    STRATEGY_META = StrategyMeta(
        id="stochastic",
        name="随机振荡器 (Stochastic Oscillator)",
        description=(
            "随机振荡器策略：%K在超卖区上穿%D买入，在超买区下穿%D卖出。"
            "由George Lane发明，是最经典的超买超卖指标之一。"
            "适合震荡行情中的高抛低吸操作。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("k_period", "number", 14, "%K周期", min=5, max=30),
            StrategyParameter("d_period", "number", 3, "%D平滑周期", min=2, max=10),
            StrategyParameter("overbought", "number", 80, "超买阈值", min=70, max=90),
            StrategyParameter("oversold", "number", 20, "超卖阈值", min=10, max=30),
        ],
    )

    params = (
        ("k_period", 14),
        ("d_period", 3),
        ("overbought", 80),
        ("oversold", 20),
    )

    def __init__(self):
        super().__init__()
        self.stoch = bt.indicators.Stochastic(
            self.data,
            period=self.p.k_period,
            period_dfast=self.p.d_period,
        )
        self.crossover = bt.indicators.CrossOver(
            self.stoch.percK, self.stoch.percD
        )

    def next(self):
        k_val = self.stoch.percK[0]
        d_val = self.stoch.percD[0]

        if not self.position:
            if self.crossover > 0 and k_val < self.p.oversold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"随机指标金叉: %K={k_val:.1f} > %D={d_val:.1f} (超卖区)",
                    )
        else:
            if self.crossover < 0 and k_val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"随机指标死叉: %K={k_val:.1f} < %D={d_val:.1f} (超买区)",
                )
