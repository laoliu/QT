"""
Hammer / Shooting Star Strategy (锤子线/射击之星).

Category: Pattern Recognition
Markets: All

Signal logic:
  - BUY:  Hammer pattern at bottom (small body, long lower shadow)
  - SELL: Shooting star at top (small body, long upper shadow)

Parameters:
  - shadow_ratio:  Min shadow-to-body ratio (default 2.0)
  - trend_period:  Period to determine trend context (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class HammerStrategy(BaseStrategy):
    """Hammer / Shooting Star candlestick strategy."""

    STRATEGY_META = StrategyMeta(
        id="hammer",
        name="锤子线/射击之星 (Hammer/Shooting Star)",
        description=(
            "锤子线/射击之星策略：底部锤子线买入，顶部射击之星卖出。"
            "锤子线：下跌末端出现，小实体+长下影线，预示反转向上。"
            "射击之星：上涨末端出现，小实体+长上影线，预示反转向下。"
            "经典单根K线反转形态。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("shadow_ratio", "number", 2.0, "影线/实体最小比值", min=1.5, max=4.0, step=0.5),
            StrategyParameter("trend_period", "number", 10, "趋势判断周期", min=5, max=30),
        ],
    )

    params = (
        ("shadow_ratio", 2.0),
        ("trend_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)

    def _body_size(self) -> float:
        return abs(self.data.close[0] - self.data.open[0])

    def _is_hammer(self) -> bool:
        """Hammer: small body at top, long lower shadow."""
        body = self._body_size()
        if body <= 0:
            return False
        upper_body = max(self.data.close[0], self.data.open[0])
        lower_body = min(self.data.close[0], self.data.open[0])
        lower_shadow = lower_body - self.data.low[0]
        upper_shadow = self.data.high[0] - upper_body
        # Long lower shadow, short upper shadow
        return (
            lower_shadow >= body * self.p.shadow_ratio
            and upper_shadow < body
            and self.data.close[0] < self.sma[0]  # downtrend context
        )

    def _is_shooting_star(self) -> bool:
        """Shooting star: small body at bottom, long upper shadow."""
        body = self._body_size()
        if body <= 0:
            return False
        upper_body = max(self.data.close[0], self.data.open[0])
        lower_body = min(self.data.close[0], self.data.open[0])
        upper_shadow = self.data.high[0] - upper_body
        lower_shadow = lower_body - self.data.low[0]
        return (
            upper_shadow >= body * self.p.shadow_ratio
            and lower_shadow < body
            and self.data.close[0] > self.sma[0]  # uptrend context
        )

    def next(self):
        if not self.position:
            if self._is_hammer():
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        "锤子线买入: 下影线长，底部反转信号",
                    )
        else:
            if self._is_shooting_star():
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    "射击之星卖出: 上影线长，顶部反转信号",
                )
