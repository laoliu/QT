"""
Ichimoku Cloud Strategy (一目均衡表).

Category: Trend Following (CTA)
Markets: All

The Ichimoku Kinko Hyo system provides support/resistance, trend direction,
and momentum in one glance.

Signal logic:
  - BUY:  Price above cloud AND Tenkan crosses above Kijun
  - SELL: Price below cloud AND Tenkan crosses below Kijun

Parameters:
  - tenkan_period:  Tenkan-sen (conversion line) period (default 9)
  - kijun_period:   Kijun-sen (base line) period (default 26)
  - senkou_period:  Senkou Span B period (default 52)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class IchimokuStrategy(BaseStrategy):
    """Ichimoku Cloud trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="ichimoku",
        name="一目均衡表 (Ichimoku Cloud)",
        description=(
            "一目均衡表策略：价格在云层之上且转换线上穿基准线时买入，"
            "价格在云层之下且转换线下穿基准线时卖出。"
            "由日本记者一目山人发明，一图即可把握趋势、支撑阻力和动量。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("tenkan_period", "number", 9, "转换线周期", min=5, max=20),
            StrategyParameter("kijun_period", "number", 26, "基准线周期", min=15, max=60),
            StrategyParameter("senkou_period", "number", 52, "先行带B周期", min=30, max=120),
        ],
    )

    params = (
        ("tenkan_period", 9),
        ("kijun_period", 26),
        ("senkou_period", 52),
    )

    def __init__(self):
        super().__init__()
        self.ichi = bt.indicators.Ichimoku(
            self.data,
            tenkan=self.p.tenkan_period,
            kijun=self.p.kijun_period,
            senkou=self.p.senkou_period,
        )
        self.crossover = bt.indicators.CrossOver(self.ichi.tenkan_sen, self.ichi.kijun_sen)

    def next(self):
        close = self.data.close[0]
        senkou_a = self.ichi.senkou_span_a[0]
        senkou_b = self.ichi.senkou_span_b[0]
        cloud_top = max(senkou_a, senkou_b)
        cloud_bot = min(senkou_a, senkou_b)

        if not self.position:
            if close > cloud_top and self.crossover > 0:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy", close,
                        f"一目均衡买入: 价格{close:.2f}在云上(云顶{cloud_top:.2f}), "
                        f"转换线上穿基准线",
                    )
        else:
            if close < cloud_bot or self.crossover < 0:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"一目均衡卖出: 价格{close:.2f}, 云底{cloud_bot:.2f}",
                )
