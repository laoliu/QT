"""
R-Breaker Strategy (R-Breaker突破反转).

Category: Composite (Advanced)
Markets: All

A well-known CTA strategy that computes 6 pivot levels from yesterday's
OHLC and uses them for both breakout and reversal signals.

Pivot levels:
  - Pivot = (High + Low + Close) / 3
  - Break Buy = High + 2 × (Pivot - Low)     — breakout long
  - Setup Sell = Pivot + (High - Low)          — reversal short setup
  - Setup Buy = Pivot - (High - Low)           — reversal long setup
  - Break Sell = Low - 2 × (High - Pivot)      — breakout short

Signal logic:
  - BUY:  Price > Break Buy (trend breakout) OR reversal buy at Setup Buy
  - SELL: Price < Break Sell (trend breakout) OR reversal sell at Setup Sell

Parameters:
  - mode: "breakout" (default), "reversal", or "both"
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class RBreakerStrategy(BaseStrategy):
    """R-Breaker pivot level strategy."""

    STRATEGY_META = StrategyMeta(
        id="r-breaker",
        name="R-Breaker突破反转 (R-Breaker)",
        description=(
            "R-Breaker策略：经典CTA日内策略，根据昨日OHLC计算6个枢轴位。"
            "突破模式：价格突破突破买入线做多。"
            "反转模式：价格触及观察卖出线后回落做反转。"
            "曾入选Futures Truth杂志最赚钱交易策略排名。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("mode", "string", "breakout", "模式(breakout/reversal/both)"),
        ],
    )

    params = (("mode", "breakout"),)

    def __init__(self):
        super().__init__()
        self._levels: dict[str, float] = {}

    def _calc_levels(self):
        h = self.data.high[-1]
        l = self.data.low[-1]
        c = self.data.close[-1]
        pivot = (h + l + c) / 3.0
        self._levels = {
            "break_buy": h + 2 * (pivot - l),
            "setup_sell": pivot + (h - l),
            "setup_buy": pivot - (h - l),
            "break_sell": l - 2 * (h - pivot),
            "pivot": pivot,
        }

    def next(self):
        self._calc_levels()
        close = self.data.close[0]
        lvl = self._levels
        mode = self.p.mode

        if not self.position:
            # Breakout buy
            if mode in ("breakout", "both") and close > lvl["break_buy"]:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"R-Breaker突破买入: 价格{close:.2f} > "
                        f"突破位{lvl['break_buy']:.2f}",
                    )
            # Reversal buy
            elif mode in ("reversal", "both") and close <= lvl["setup_buy"]:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"R-Breaker反转买入: 价格{close:.2f} <= "
                        f"观察买入位{lvl['setup_buy']:.2f}",
                    )
        else:
            if close < lvl["break_sell"]:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"R-Breaker突破卖出: 价格{close:.2f} < "
                    f"突破位{lvl['break_sell']:.2f}",
                )
            elif close >= lvl["setup_sell"]:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"R-Breaker反转卖出: 价格{close:.2f} >= "
                    f"观察卖出位{lvl['setup_sell']:.2f}",
                )
