"""
A-Share Mean-Reversion Strategy (A股均值回归 / BIAS乖离率).

Category: Oscillator / Mean Reversion
Markets: A-shares (primary), adaptable to other markets

A strategy specifically tuned for the Chinese A-share market's
characteristics: T+1 settlement, ±10% daily limit (±20% for ChiNext/STAR),
and high retail participation leading to frequent mean-reversion patterns.

Signal logic:
  - BUY:  BIAS drops below a negative threshold (deep oversold), confirmed by
          volume spike (panic selling exhaustion) → reversal entry
  - SELL: BIAS rises above a positive threshold (overbought) OR position held
          for max_hold_days (time stop) to avoid T+1 traps

BIAS formula:
  BIAS = (Close - MA(N)) / MA(N) × 100%

Enhanced features:
  - Volume anomaly detection: sudden volume surge (> vol_mult × avg) confirms
    selling climax or buying frenzy
  - T+1 awareness: entry bar is tracked so the position is not closed on the
    same bar (backtrader can do this but real A-share market cannot)
  - Holding period stop: force-exit after max_hold_days to cap drawdown

Parameters:
  - ma_period:       Moving average period for BIAS calculation (default 20)
  - bias_buy:        BIAS threshold for buy signal, negative (default -6.0%)
  - bias_sell:       BIAS threshold for sell signal, positive (default 4.0%)
  - vol_mult:        Volume spike multiplier vs. 20-day average (default 2.0)
  - max_hold_days:   Maximum holding period in trading days (default 10)
"""

from __future__ import annotations

import backtrader as bt

from .base import BaseStrategy, StrategyMeta, StrategyParameter


class MeanReversionCNStrategy(BaseStrategy):
    """A-share mean-reversion strategy with BIAS + volume confirmation."""

    STRATEGY_META = StrategyMeta(
        id="mean-reversion-cn",
        name="A股均值回归 (Mean Reversion CN)",
        description=(
            "A股乖离率均值回归策略：当BIAS（乖离率）低于买入阈值且出现放量确认时买入，"
            "BIAS高于卖出阈值或持仓超过最大天数时卖出。"
            "רΪA股市场设计，考虑T+1交割制度和涨跌停板特征。"
            "适用于散户情绪驱动的超跌反弹行情。"
        ),
        category="oscillator",
        markets=["cn"],
        parameters=[
            StrategyParameter("ma_period", "number", 20, "BIAS均线周期", min=5, max=60),
            StrategyParameter(
                "bias_buy", "number", -6.0, "买入乖离率阈值（%）", min=-15.0, max=-2.0, step=0.5
            ),
            StrategyParameter(
                "bias_sell", "number", 4.0, "卖出乖离率阈值（%）", min=1.0, max=10.0, step=0.5
            ),
            StrategyParameter(
                "vol_mult", "number", 2.0, "放量倍数（相对20日均量）", min=1.2, max=5.0, step=0.1
            ),
            StrategyParameter(
                "max_hold_days", "number", 10, "最大持仓天数", min=2, max=30
            ),
        ],
    )

    params = (
        ("ma_period", 20),
        ("bias_buy", -6.0),
        ("bias_sell", 4.0),
        ("vol_mult", 2.0),
        ("max_hold_days", 10),
    )

    def __init__(self):
        super().__init__()
        # Moving average for BIAS calculation
        self.sma = bt.indicators.SMA(self.data.close, period=self.p.ma_period)
        # Volume moving average (20-day regardless of MA period)
        self.vol_sma = bt.indicators.SMA(self.data.volume, period=20)
        # Entry bar number for T+1 tracking and max-hold stop
        self._entry_bar: int = -1

    # ── helpers ────────────────────────────────────────────────────

    def _bias(self) -> float:
        """Compute current BIAS (乖离率) in percent."""
        ma = self.sma[0]
        if ma <= 0:
            return 0.0
        return (self.data.close[0] - ma) / ma * 100.0

    def _is_volume_spike(self) -> bool:
        """Return True if today's volume exceeds vol_mult × 20-day average."""
        avg = self.vol_sma[0]
        if avg <= 0:
            return False
        return self.data.volume[0] >= avg * self.p.vol_mult

    def _holding_days(self) -> int:
        """Return number of bars held since entry."""
        if self._entry_bar < 0:
            return 0
        return len(self) - self._entry_bar

    # ── core logic ────────────────────────────────────────────────

    def next(self):
        close = self.data.close[0]
        bias = self._bias()
        vol_spike = self._is_volume_spike()

        if not self.position:
            # ── BUY condition ──
            # BIAS deeply negative (oversold) + volume spike (panic exhaustion)
            if bias <= self.p.bias_buy and vol_spike:
                size = self.calc_position_size_pct(close)
                if size > 0:
                    self.buy(size=size)
                    self._entry_bar = len(self)
                    vol_ratio = (
                        self.data.volume[0] / self.vol_sma[0]
                        if self.vol_sma[0] > 0
                        else 0
                    )
                    self.record_signal(
                        "buy",
                        close,
                        f"均值回归买入: BIAS={bias:.2f}% (阈值{self.p.bias_buy}%), "
                        f"量比={vol_ratio:.1f}x",
                    )
        else:
            # T+1 guard: cannot sell on the same bar as entry
            if len(self) <= self._entry_bar:
                return

            hold = self._holding_days()

            # ── SELL conditions (any one triggers) ──
            # 1. BIAS reaches overbought threshold
            if bias >= self.p.bias_sell:
                self.close()
                self._entry_bar = -1
                self.record_signal(
                    "sell",
                    close,
                    f"乖离率止盈: BIAS={bias:.2f}% (阈值{self.p.bias_sell}%), "
                    f"持仓{hold}天",
                )
                return

            # 2. Max holding period exceeded (time stop)
            if hold >= self.p.max_hold_days:
                self.close()
                self._entry_bar = -1
                self.record_signal(
                    "sell",
                    close,
                    f"时间止损: 持仓{hold}天 >= 最大{self.p.max_hold_days}天, "
                    f"BIAS={bias:.2f}%",
                )
