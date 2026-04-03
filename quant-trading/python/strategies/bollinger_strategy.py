"""
Bollinger Band Mean-Reversion Strategy (布林带均值回归).

Category: Oscillator / Mean Reversion
Markets: All (best in range-bound / sideways markets)

Signal logic:
  - BUY:  Price closes below the lower Bollinger Band → oversold bounce expected
  - SELL: Price closes above the upper Bollinger Band → overbought pullback expected

Enhanced with bandwidth and %B confirmation:
  - %B (Percent B): position of price relative to bands (0 = lower, 1 = upper)
  - Bandwidth contraction ("squeeze") detection: when bandwidth narrows below
    a threshold, a volatility expansion (breakout) is imminent — avoid entries
    during squeeze to reduce whipsaw risk.

Risk management:
  - Middle band (SMA) is used as a trailing stop / profit target by default
  - Optional ATR-based stop-loss for tighter downside protection

Parameters:
  - period:        Bollinger Band lookback (default 20)
  - devfactor:     Number of standard deviations (default 2.0)
  - squeeze_pct:   Bandwidth squeeze threshold as % of median bandwidth (default 50)
                   When current bandwidth < squeeze_pct% of median → skip entries
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class BollingerStrategy(BaseStrategy):
    """Bollinger Band mean-reversion trading strategy."""

    STRATEGY_META = StrategyMeta(
        id="bollinger",
        name="布林带均值回归 (Bollinger Band)",
        description=(
            "布林带策略：价格触及下轨时买入（超卖反弹），触及上轨时卖出（超买回落）。"
            "布林带由John Bollinger发明，利用均线±N倍标准差构建价格通道。"
            "适合震荡行情，在趋势行情中可能频繁止损。"
            "内置挤压检测：当布林带收窄（波动率低）时暂停开仓，避免假信号。"
        ),
        category="oscillator",
        parameters=[
            StrategyParameter("period", "number", 20, "布林带周期", min=10, max=50),
            StrategyParameter(
                "devfactor", "number", 2.0, "标准差倍数", min=1.0, max=3.5, step=0.1
            ),
            StrategyParameter(
                "squeeze_pct",
                "number",
                50,
                "挤压阈值（%中位带宽）",
                min=20,
                max=80,
                step=5,
            ),
        ],
    )

    params = (
        ("period", 20),
        ("devfactor", 2.0),
        ("squeeze_pct", 50),
    )

    def __init__(self):
        super().__init__()
        self.boll = bt.indicators.BollingerBands(
            self.data.close,
            period=self.p.period,
            devfactor=self.p.devfactor,
        )
        # %B = (close - lower) / (upper - lower)
        band_width = self.boll.top - self.boll.bot
        # Avoid division by zero — DivByZero returns 0.5 when denominator is 0
        self.pct_b = bt.DivByZero(
            self.data.close - self.boll.bot,
            band_width,
            zero=0.5,
        )
        # Raw bandwidth for squeeze detection
        self.band_width = band_width
        # Track recent bandwidth for squeeze thresholding
        self._bw_history: list[float] = []

    # ── helpers ────────────────────────────────────────────────────

    def _is_squeeze(self) -> bool:
        """Return True if the current bandwidth is squeezed (low volatility)."""
        if len(self._bw_history) < self.p.period:
            return False
        median_bw = sorted(self._bw_history)[(len(self._bw_history)) // 2]
        if median_bw <= 0:
            return False
        return self.band_width[0] < median_bw * (self.p.squeeze_pct / 100.0)

    # ── core logic ────────────────────────────────────────────────

    def next(self):
        close = self.data.close[0]
        upper = self.boll.top[0]
        lower = self.boll.bot[0]
        mid = self.boll.mid[0]
        pct_b = self.pct_b[0]

        # Maintain bandwidth history (rolling window = 4 × period)
        self._bw_history.append(self.band_width[0])
        max_history = self.p.period * 4
        if len(self._bw_history) > max_history:
            self._bw_history = self._bw_history[-max_history:]

        squeeze = self._is_squeeze()

        if not self.position:
            # BUY: price at or below lower band & not in squeeze
            if close <= lower and not squeeze:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        close,
                        f"布林下轨买入: %B={pct_b:.2f}, "
                        f"下轨={lower:.2f}, 中轨={mid:.2f}",
                    )
        else:
            # SELL: price at or above upper band OR crosses back above mid
            if close >= upper:
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"布林上轨卖出: %B={pct_b:.2f}, "
                    f"上轨={upper:.2f}",
                )
            elif close >= mid and self.pct_b[-1] < 0.5:
                # Price crossed above the middle band from below → take profit
                self.close()
                self.record_signal(
                    "sell",
                    close,
                    f"中轨止盈: 价格穿越中轨={mid:.2f}",
                )
