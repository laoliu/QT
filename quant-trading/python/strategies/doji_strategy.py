"""
Doji Pattern Strategy (十字星形态).

Category: Pattern Recognition
Markets: All

Signal logic:
  - BUY:  Doji at bottom of downtrend (indecision after selling)
  - SELL: Doji at top of uptrend (indecision after buying)

Parameters:
  - body_pct:     Max body size as % of full range (default 10%)
  - trend_period: Period to determine trend context (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class DojiStrategy(BaseStrategy):
    """Doji candlestick pattern strategy."""

    STRATEGY_META = StrategyMeta(
        id="doji",
        name="十字星形态 (Doji Pattern)",
        description=(
            "十字星策略：下跌末端十字星买入，上涨末端十字星卖出。"
            "十字星表示开盘价≈收盘价，多空力量均衡，预示趋势可能反转。"
            "需要结合前后K线确认信号有效性。"
        ),
        category="pattern",
        parameters=[
            StrategyParameter("body_pct", "number", 10, "实体/振幅最大比例(%)", min=3, max=20, step=1),
            StrategyParameter("trend_period", "number", 10, "趋势判断周期", min=5, max=30),
        ],
    )

    params = (
        ("body_pct", 10),
        ("trend_period", 10),
    )

    def __init__(self):
        super().__init__()
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.trend_period)
        self._prev_doji = False

    def _is_doji(self) -> bool:
        body = abs(self.data.close[0] - self.data.open[0])
        full_range = self.data.high[0] - self.data.low[0]
        if full_range <= 0:
            return False
        return (body / full_range) * 100 <= self.p.body_pct

    def next(self):
        is_doji = self._is_doji()

        if not self.position:
            # Buy: doji in downtrend, confirmed by next bar closing up
            if self._prev_doji and self.data.close[0] < self.sma[0]:
                if self.data.close[0] > self.data.close[-1]:
                    size = self.calc_position_size_pct(self.data.close[0])
                    if size > 0:
                        self.buy(size=size)
                        self.record_signal(
                            "buy",
                            self.data.close[0],
                            "十字星底部反转: 下跌趋势中十字星后阳线确认",
                        )
        else:
            if self._prev_doji and self.data.close[0] > self.sma[0]:
                if self.data.close[0] < self.data.close[-1]:
                    self.close()
                    self.record_signal(
                        "sell",
                        self.data.close[0],
                        "十字星顶部反转: 上涨趋势中十字星后阴线确认",
                    )

        self._prev_doji = is_doji
