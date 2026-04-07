"""
Ichimoku Cloud Strategy (一目均衡表).

Category: Trend Following
Markets: All (A-shares, US, HK, global)

Signal logic:
  - BUY:  Price above cloud + Tenkan-sen crosses above Kijun-sen (TK cross above cloud)
  - SELL: Price below cloud OR Tenkan-sen crosses below Kijun-sen

The Ichimoku Cloud (一目均衡表) is a comprehensive indicator developed by
Goichi Hosoda that defines support/resistance, trend direction, and momentum
all in one view.

Parameters:
  - tenkan_period:  Tenkan-sen (转换线) period (default 9)
  - kijun_period:   Kijun-sen (基准线) period (default 26)
  - senkou_period:  Senkou Span B (先行带B) period (default 52)
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
            "一目均衡表策略：价格位于云层之上且转换线上穿基准线时买入，"
            "价格跌破云层下方或转换线下穿基准线时卖出。"
            "一目均衡表由日本记者细田悟一发明，是综合判断趋势方向、"
            "支撑阻力和动量的经典指标，适合中长线趋势交易。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("tenkan_period", "number", 9, "转换线(Tenkan-sen)周期", min=2, max=50),
            StrategyParameter("kijun_period", "number", 26, "基准线(Kijun-sen)周期", min=5, max=100),
            StrategyParameter("senkou_period", "number", 52, "先行带B(Senkou Span B)周期", min=10, max=200),
        ],
    )

    params = (
        ("tenkan_period", 9),
        ("kijun_period", 26),
        ("senkou_period", 52),
    )

    def __init__(self):
        super().__init__()
        # Ichimoku Cloud indicator
        self.ichi = bt.indicators.Ichimoku(
            self.data,
            tenkan=self.p.tenkan_period,
            kijun=self.p.kijun_period,
            senkou=self.p.senkou_period,
        )
        # Tenkan-sen / Kijun-sen crossover
        self.tk_cross = bt.indicators.CrossOver(self.ichi.tenkan_sen, self.ichi.kijun_sen)

    def _price_above_cloud(self) -> bool:
        """Return True when close is above both Senkou Span A and Senkou Span B."""
        return self.data.close[0] > max(self.ichi.senkou_span_a[0], self.ichi.senkou_span_b[0])

    def _price_below_cloud(self) -> bool:
        """Return True when close is below both Senkou Span A and Senkou Span B."""
        return self.data.close[0] < min(self.ichi.senkou_span_a[0], self.ichi.senkou_span_b[0])

    def next(self):
        if not self.position:
            # No position — look for TK golden cross above the cloud
            if self.tk_cross > 0 and self._price_above_cloud():
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"一目均衡表买入: 转换线({self.ichi.tenkan_sen[0]:.2f})上穿"
                        f"基准线({self.ichi.kijun_sen[0]:.2f}), 价格在云层之上",
                    )
        else:
            # Have position — exit on price below cloud OR TK death cross
            if self._price_below_cloud():
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"一目均衡表卖出: 价格({self.data.close[0]:.2f})跌破云层"
                    f"(Span A={self.ichi.senkou_span_a[0]:.2f}, "
                    f"Span B={self.ichi.senkou_span_b[0]:.2f})",
                )
            elif self.tk_cross < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"一目均衡表卖出: 转换线({self.ichi.tenkan_sen[0]:.2f})下穿"
                    f"基准线({self.ichi.kijun_sen[0]:.2f})",
                )
