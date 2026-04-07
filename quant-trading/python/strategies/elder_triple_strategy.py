"""
Elder Triple Screen Strategy (艾尔德三重滤网).

Category: Composite / Advanced
Markets: All

Alexander Elder's Triple Screen system uses three timeframes:
1. Tide (long-term trend via MACD histogram)
2. Wave (medium-term reversal via Force Index / Stochastic)
3. Ripple (short-term entry via trailing stop)

Simplified for single-timeframe backtrader:
  - Screen 1: Long MA direction (trend filter)
  - Screen 2: RSI for pullback entry
  - Screen 3: Price trigger (breakout of recent high)

Parameters:
  - trend_period:  Long MA period for trend (default 50)
  - rsi_period:    RSI period (default 14)
  - rsi_entry:     RSI pullback entry level (default 40)
  - breakout_bars: Recent high lookback for entry trigger (default 5)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class ElderTripleStrategy(BaseStrategy):
    """Elder Triple Screen strategy."""

    STRATEGY_META = StrategyMeta(
        id="elder-triple",
        name="三重滤网 (Elder Triple Screen)",
        description=(
            "艾尔德三重滤网策略：第一层长期趋势过滤（50日均线方向），"
            "第二层中期回调确认（RSI回调至超卖区），"
            "第三层短期突破入场（突破近N日高点）。三重确认大幅提高胜率。"
            "由Alexander Elder发明的经典多时间框架策略。"
        ),
        category="composite",
        parameters=[
            StrategyParameter("trend_period", "number", 50, "趋势均线周期", min=20, max=100),
            StrategyParameter("rsi_period", "number", 14, "RSI周期", min=7, max=30),
            StrategyParameter("rsi_entry", "number", 40, "RSI入场阈值", min=25, max=50),
            StrategyParameter("breakout_bars", "number", 5, "突破回看周期", min=2, max=10),
        ],
    )

    params = (
        ("trend_period", 50),
        ("rsi_period", 14),
        ("rsi_entry", 40),
        ("breakout_bars", 5),
    )

    def __init__(self):
        super().__init__()
        self.trend_ma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.recent_high = bt.indicators.Highest(
            self.data.high, period=self.p.breakout_bars
        )
        self._ready_to_enter = False

    def next(self):
        close = self.data.close[0]
        trend_up = close > self.trend_ma[0]
        rsi_val = self.rsi[0]

        if not self.position:
            # Screen 1: Trend must be up
            if not trend_up:
                self._ready_to_enter = False
                return

            # Screen 2: RSI pullback to entry zone
            if rsi_val < self.p.rsi_entry:
                self._ready_to_enter = True

            # Screen 3: Price breakout trigger
            if self._ready_to_enter and close > self.recent_high[-1]:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self._ready_to_enter = False
                    self.record_signal(
                        "buy", close,
                        f"三重滤网: 趋势↑ + RSI回调 + 突破{self.recent_high[-1]:.2f}",
                    )
        else:
            # Exit: trend reversal or RSI overbought
            if not trend_up or rsi_val > 75:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"三重滤网退出: 趋势{'↓' if not trend_up else '↑'}, RSI={rsi_val:.1f}",
                )
