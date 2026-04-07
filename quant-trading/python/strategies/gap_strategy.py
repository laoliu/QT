"""
Gap Trading Strategy (跳空缺口策略).

Category: Pattern
Markets: All (especially A-shares with ±10% limits)

Trades based on gap-up/gap-down patterns.

Signal logic:
  - BUY:  Gap-up breakout (today's low > yesterday's high) with volume confirmation
  - SELL: Gap-down breakdown OR gap fill (price returns to pre-gap level)

Parameters:
  - min_gap_pct:    Minimum gap size as percentage (default 2.0%)
  - vol_mult:       Volume confirmation multiplier (default 1.5)
  - fill_exit:      Exit when gap fills (default True)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class GapStrategy(BaseStrategy):
    """Gap breakout trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="gap",
        name="跳空缺口 (Gap Trading)",
        description=(
            "跳空缺口策略：向上跳空缺口（今日低点>昨日高点）且放量确认时买入，"
            "缺口回补或反向跳空时卖出。跳空代表强烈的多空力量变化，"
            "缺口越大信号越强。适合A股涨停板次日高开的情景。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("min_gap_pct", "number", 2.0, "最小缺口比例(%)", min=0.5, max=5.0, step=0.5),
            StrategyParameter("vol_mult", "number", 1.5, "放量确认倍数", min=1.0, max=3.0, step=0.25),
            StrategyParameter("fill_exit", "boolean", True, "缺口回补退出"),
        ],
    )

    params = (
        ("min_gap_pct", 2.0),
        ("vol_mult", 1.5),
        ("fill_exit", True),
    )

    def __init__(self):
        super().__init__()
        self.vol_sma = bt.indicators.SMA(self.data.volume, period=20)
        self._gap_low = 0.0  # Gap zone lower bound

    def next(self):
        if len(self) < 2:
            return

        close = self.data.close[0]
        prev_high = self.data.high[-1]
        prev_close = self.data.close[-1]
        today_low = self.data.low[0]

        gap_up = (today_low - prev_high) / prev_close * 100 if prev_close > 0 else 0
        vol_ratio = self.data.volume[0] / self.vol_sma[0] if self.vol_sma[0] > 0 else 0

        if not self.position:
            if gap_up >= self.p.min_gap_pct and vol_ratio >= self.p.vol_mult:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self._gap_low = prev_high
                    self.record_signal(
                        "buy", close,
                        f"跳空突破买入: 缺口={gap_up:.1f}%, 量比={vol_ratio:.1f}x",
                    )
        else:
            gap_down = (self.data.high[0] - self.data.low[-1]) < 0
            if gap_down:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"反向跳空: 卖出",
                )
            elif self.p.fill_exit and close < self._gap_low:
                self.close()
                self.record_signal(
                    "sell", close,
                    f"缺口回补: 价格{close:.2f} < 缺口下沿{self._gap_low:.2f}",
                )
