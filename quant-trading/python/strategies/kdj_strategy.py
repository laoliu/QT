"""
KDJ Stochastic Oscillator Strategy (KDJ 随机指标).

Category: Oscillator
Markets: All (especially popular in Chinese A-share market)

The KDJ indicator is an enhanced version of the Stochastic Oscillator,
widely used in Asian markets. It consists of three lines:
  - K line: Fast stochastic (smoothed RSV)
  - D line: Slow stochastic (SMA of K)
  - J line: 3K - 2D (amplifies divergence between K and D)

Signal logic:
  - BUY:  K crosses above D in oversold zone (K < oversold threshold)
           OR J line drops below 0 (extreme oversold)
  - SELL: K crosses below D in overbought zone (K > overbought threshold)
           OR J line rises above 100 (extreme overbought)

Parameters:
  - k_period:    K line period (default 9)
  - d_period:    D line smoothing period (default 3)
  - j_smooth:    J line smoothing (default 3)
  - overbought:  Overbought threshold (default 80)
  - oversold:    Oversold threshold (default 20)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _KDJIndicator(bt.Indicator):
    """Custom KDJ indicator for backtrader.

    KDJ is not built into backtrader, so we implement it here.

    RSV = (Close - Lowest_Low(N)) / (Highest_High(N) - Lowest_Low(N)) * 100
    K = SMA(RSV, M1)   (usually EWM alpha=1/3)
    D = SMA(K, M2)
    J = 3 * K - 2 * D
    """

    lines = ("k", "d", "j")

    params = (
        ("k_period", 9),
        ("d_period", 3),
        ("j_smooth", 3),
    )

    def __init__(self):
        # Highest high and lowest low over k_period
        self.highest = bt.indicators.Highest(self.data.high, period=self.p.k_period)
        self.lowest = bt.indicators.Lowest(self.data.low, period=self.p.k_period)

        # RSV (Raw Stochastic Value)
        hl_diff = self.highest - self.lowest
        # Avoid division by zero
        self.rsv = bt.If(hl_diff > 0, (self.data.close - self.lowest) / hl_diff * 100, 50.0)

        # K line: smoothed RSV
        self.lines.k = bt.indicators.SMA(self.rsv, period=self.p.d_period)

        # D line: smoothed K
        self.lines.d = bt.indicators.SMA(self.lines.k, period=self.p.j_smooth)

        # J line: 3K - 2D (amplified divergence)
        self.lines.j = 3.0 * self.lines.k - 2.0 * self.lines.d


class KDJStrategy(BaseStrategy):
    """KDJ stochastic oscillator trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="kdj",
        name="KDJ 随机指标 (KDJ Stochastic)",
        description=(
            "KDJ随机指标策略：K线在超卖区上穿D线买入，在超买区下穿D线卖出。"
            "J线低于0为极度超卖（强买入），高于100为极度超买（强卖出）。"
            "KDJ是亚洲市场最流行的技术指标之一，特别适合A股短线交易。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("k_period", "number", 9, "K线周期", min=5, max=30),
            StrategyParameter("d_period", "number", 3, "D线平滑周期", min=2, max=10),
            StrategyParameter("j_smooth", "number", 3, "J线平滑周期", min=2, max=10),
            StrategyParameter("overbought", "number", 80, "超买阈值", min=60, max=95),
            StrategyParameter("oversold", "number", 20, "超卖阈值", min=5, max=40),
        ],
    )

    params = (
        ("k_period", 9),
        ("d_period", 3),
        ("j_smooth", 3),
        ("overbought", 80),
        ("oversold", 20),
    )

    def __init__(self):
        super().__init__()
        self.kdj = _KDJIndicator(
            self.data,
            k_period=self.p.k_period,
            d_period=self.p.d_period,
            j_smooth=self.p.j_smooth,
        )
        # K/D crossover
        self.crossover = bt.indicators.CrossOver(self.kdj.k, self.kdj.d)

    def next(self):
        k_val = self.kdj.k[0]
        d_val = self.kdj.d[0]
        j_val = self.kdj.j[0]

        if not self.position:
            # Buy signals:
            # 1. K crosses above D in oversold zone
            # 2. J line drops below 0 (extreme oversold)
            if self.crossover > 0 and k_val < self.p.oversold:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"KDJ金叉(超卖区): K={k_val:.1f}, D={d_val:.1f}, J={j_val:.1f}",
                    )
            elif j_val < 0:
                size = self.calc_position_size_pct(self.data.close[0], pct=0.5)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"J线极度超卖: J={j_val:.1f} < 0",
                    )
        else:
            # Sell signals:
            # 1. K crosses below D in overbought zone
            # 2. J line rises above 100 (extreme overbought)
            if self.crossover < 0 and k_val > self.p.overbought:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"KDJ死叉(超买区): K={k_val:.1f}, D={d_val:.1f}, J={j_val:.1f}",
                )
            elif j_val > 100:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"J线极度超买: J={j_val:.1f} > 100",
                )
