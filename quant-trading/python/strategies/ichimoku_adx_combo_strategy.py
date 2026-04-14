"""
Ichimoku + ADX Combo Strategy (一目均衡表+ADX组合).

Category: Composite (Advanced)
Markets: All

Combines Ichimoku Cloud for trend direction and support/resistance
with ADX for trend strength confirmation.

Signal logic:
  - BUY:  Price above cloud + Tenkan > Kijun + ADX > threshold
  - SELL: Price below cloud OR ADX drops below threshold

Parameters:
  - tenkan_period:  Tenkan-sen period (default 9)
  - kijun_period:   Kijun-sen period (default 26)
  - adx_period:     ADX period (default 14)
  - adx_threshold:  Minimum ADX for trend confirmation (default 25)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class IchimokuADXComboStrategy(BaseStrategy):
    """Ichimoku Cloud + ADX combined strategy."""

    STRATEGY_META = StrategyMeta(
        id="ichimoku-adx-combo",
        name="一目均衡表+ADX组合 (Ichimoku+ADX)",
        description=(
            "一目均衡表+ADX组合策略：价格在云层上方+转换线>基准线+ADX确认趋势时买入。"
            "一目均衡表提供趋势方向和支撑阻力，ADX过滤弱趋势。"
            "多重条件过滤使信号更可靠，适合中长期趋势交易。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("tenkan_period", "number", 9, "转换线周期", min=5, max=20),
            StrategyParameter("kijun_period", "number", 26, "基准线周期", min=15, max=40),
            StrategyParameter("adx_period", "number", 14, "ADX周期", min=7, max=30),
            StrategyParameter("adx_threshold", "number", 25, "ADX确认阈值", min=15, max=40),
        ],
    )

    params = (
        ("tenkan_period", 9),
        ("kijun_period", 26),
        ("adx_period", 14),
        ("adx_threshold", 25),
    )

    def __init__(self):
        super().__init__()
        self.ichi = bt.indicators.Ichimoku(
            self.data,
            tenkan=self.p.tenkan_period,
            kijun=self.p.kijun_period,
        )
        self.adx = bt.indicators.ADX(self.data, period=self.p.adx_period)

    def next(self):
        close = self.data.close[0]
        tenkan = self.ichi.tenkan_sen[0]
        kijun = self.ichi.kijun_sen[0]
        senkou_a = self.ichi.senkou_span_a[0]
        senkou_b = self.ichi.senkou_span_b[0]
        adx_val = self.adx[0]

        cloud_top = max(senkou_a, senkou_b)
        cloud_bottom = min(senkou_a, senkou_b)

        if not self.position:
            if (
                close > cloud_top
                and tenkan > kijun
                and adx_val > self.p.adx_threshold
            ):
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"一目+ADX买入: 云上+转换>{kijun:.2f}, ADX={adx_val:.1f}",
                    )
        else:
            if close < cloud_bottom or adx_val < self.p.adx_threshold:
                self.close()
                reason = (
                    f"跌破云层{cloud_bottom:.2f}"
                    if close < cloud_bottom
                    else f"ADX走弱={adx_val:.1f}"
                )
                self.record_signal(
                    "sell",
                    close,
                    f"一目+ADX卖出: {reason}",
                )
