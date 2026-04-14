"""
Trend + Mean-Reversion Regime Switch Strategy (趋势+均值回归切换).

Category: Composite (Advanced)
Markets: All

Automatically switches between trend-following and mean-reversion modes
based on ADX (trend strength indicator).

Signal logic:
  - When ADX > threshold → Trend mode (follow EMA crossover)
  - When ADX < threshold → Mean-reversion mode (RSI oversold/overbought)

Parameters:
  - adx_period:     ADX period (default 14)
  - adx_threshold:  Mode switch threshold (default 25)
  - ema_fast:       Trend mode fast EMA (default 10)
  - ema_slow:       Trend mode slow EMA (default 30)
  - rsi_period:     Reversion mode RSI period (default 14)
  - rsi_oversold:   RSI oversold level (default 30)
  - rsi_overbought: RSI overbought level (default 70)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class RegimeSwitchStrategy(BaseStrategy):
    """Trend / Mean-Reversion regime switching strategy."""

    STRATEGY_META = StrategyMeta(
        id="regime-switch",
        name="趋势+均值回归切换 (Regime Switch)",
        description=(
            "趋势+均值回归自适应切换策略：通过ADX判断市场状态，"
            "ADX高（趋势市）时使用EMA交叉跟踪趋势，"
            "ADX低（震荡市）时使用RSI超买超卖做均值回归。"
            "一个策略适应两种市场环境，减少单一策略的局限性。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("adx_period", "number", 14, "ADX周期", min=7, max=30),
            StrategyParameter("adx_threshold", "number", 25, "趋势/震荡切换阈值", min=15, max=35),
            StrategyParameter("ema_fast", "number", 10, "趋势模式快速EMA", min=3, max=20),
            StrategyParameter("ema_slow", "number", 30, "趋势模式慢速EMA", min=15, max=60),
            StrategyParameter("rsi_period", "number", 14, "RSI周期", min=5, max=30),
            StrategyParameter("rsi_oversold", "number", 30, "RSI超卖阈值", min=15, max=40),
            StrategyParameter("rsi_overbought", "number", 70, "RSI超买阈值", min=60, max=85),
        ],
    )

    params = (
        ("adx_period", 14),
        ("adx_threshold", 25),
        ("ema_fast", 10),
        ("ema_slow", 30),
        ("rsi_period", 14),
        ("rsi_oversold", 30),
        ("rsi_overbought", 70),
    )

    def __init__(self):
        super().__init__()
        self.adx = bt.indicators.ADX(self.data, period=self.p.adx_period)
        self.fast_ema = bt.indicators.EMA(self.data.close, period=self.p.ema_fast)
        self.slow_ema = bt.indicators.EMA(self.data.close, period=self.p.ema_slow)
        self.ema_cross = bt.indicators.CrossOver(self.fast_ema, self.slow_ema)
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)

    def _is_trend_mode(self) -> bool:
        return self.adx[0] > self.p.adx_threshold

    def next(self):
        trend_mode = self._is_trend_mode()
        adx_val = self.adx[0]
        rsi_val = self.rsi[0]

        if not self.position:
            if trend_mode:
                if self.ema_cross > 0:
                    size = self.calc_position_size_pct(self.data.close[0])
                    if size > 0:
                        self.buy(size=size)
                        self.record_signal(
                            "buy",
                            self.data.close[0],
                            f"[趋势模式]EMA金叉: ADX={adx_val:.1f}",
                        )
            else:
                if rsi_val < self.p.rsi_oversold:
                    size = self.calc_position_size_pct(self.data.close[0])
                    if size > 0:
                        self.buy(size=size)
                        self.record_signal(
                            "buy",
                            self.data.close[0],
                            f"[震荡模式]RSI超卖: RSI={rsi_val:.1f}, ADX={adx_val:.1f}",
                        )
        else:
            if trend_mode:
                if self.ema_cross < 0:
                    self.close()
                    self.record_signal(
                        "sell",
                        self.data.close[0],
                        f"[趋势模式]EMA死叉: ADX={adx_val:.1f}",
                    )
            else:
                if rsi_val > self.p.rsi_overbought:
                    self.close()
                    self.record_signal(
                        "sell",
                        self.data.close[0],
                        f"[震荡模式]RSI超买: RSI={rsi_val:.1f}, ADX={adx_val:.1f}",
                    )
