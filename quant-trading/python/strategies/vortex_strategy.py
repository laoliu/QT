"""
Vortex Indicator Strategy (漩涡指标).

Category: Trend Following
Markets: All

Uses the Vortex Indicator (VI) to detect trend direction via
positive and negative vortex movement.

Signal logic:
  - BUY:  VI+ crosses above VI- (uptrend begins)
  - SELL: VI- crosses above VI+ (downtrend begins)

Parameters:
  - period: Vortex calculation period (default 14)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class _VortexIndicator(bt.Indicator):
    """Vortex Indicator (VI+ / VI-).

    VI+ = Sum(|High - Low(-1)|, N) / Sum(TrueRange, N)
    VI- = Sum(|Low - High(-1)|, N) / Sum(TrueRange, N)
    """

    lines = ("vi_plus", "vi_minus")
    params = (("period", 14),)

    def __init__(self):
        # Vortex movement
        vm_plus = abs(self.data.high - self.data.low(-1))
        vm_minus = abs(self.data.low - self.data.high(-1))

        # True range
        tr = bt.indicators.TrueRange(self.data)

        # Sums over period
        sum_vm_plus = bt.indicators.SumN(vm_plus, period=self.p.period)
        sum_vm_minus = bt.indicators.SumN(vm_minus, period=self.p.period)
        sum_tr = bt.indicators.SumN(tr, period=self.p.period)

        self.lines.vi_plus = sum_vm_plus / sum_tr
        self.lines.vi_minus = sum_vm_minus / sum_tr


class VortexStrategy(BaseStrategy):
    """Vortex indicator trend-following strategy."""

    STRATEGY_META = StrategyMeta(
        id="vortex",
        name="漩涡指标 (Vortex)",
        description=(
            "漩涡指标策略：VI+上穿VI-时买入，VI-上穿VI+时卖出。"
            "漩涡指标由Etienne Botes和Douglas Siepman提出，灵感来源于水流漩涡运动。"
            "VI+衡量正向趋势力量，VI-衡量负向趋势力量。"
            "两线交叉提示趋势方向转变，适合趋势跟踪交易。"
        ),
        category="trend",
        parameters=[
            StrategyParameter("period", "number", 14, "漩涡指标计算周期", min=7, max=30),
        ],
    )

    params = (("period", 14),)

    def __init__(self):
        super().__init__()
        self.vortex = _VortexIndicator(self.data, period=self.p.period)
        self.cross = bt.indicators.CrossOver(
            self.vortex.vi_plus, self.vortex.vi_minus
        )

    def next(self):
        vi_plus = self.vortex.vi_plus[0]
        vi_minus = self.vortex.vi_minus[0]

        if not self.position:
            if self.cross > 0:
                size = self.calc_position_size_pct(self.data.close[0])
                if size > 0:
                    self.buy(size=size)
                    self.record_signal(
                        "buy",
                        self.data.close[0],
                        f"漩涡买入: VI+={vi_plus:.3f} 上穿 VI-={vi_minus:.3f}",
                    )
        else:
            if self.cross < 0:
                self.close()
                self.record_signal(
                    "sell",
                    self.data.close[0],
                    f"漩涡卖出: VI-={vi_minus:.3f} 上穿 VI+={vi_plus:.3f}",
                )
