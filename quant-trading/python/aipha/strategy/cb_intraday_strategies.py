"""可转债日内策略（VWAP偏离 + 隔夜缺口衰减）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_intraday_base import CBIntradayBase, CBIntradayConfig, IntradaySignal, IntradayState
logger = logging.getLogger(__name__)

@dataclass
class VWAPDeviationConfig(CBIntradayConfig):
    entry_threshold: float = -0.008
    exit_threshold: float = -0.001
    min_volume_bars: int = 30
    max_deviation: float = -0.03

class CBVWAPDeviation(CBIntradayBase):
    name = "cb_vwap_deviation"
    def __init__(self, config: VWAPDeviationConfig | None = None):
        super().__init__(config=config or VWAPDeviationConfig())

    def on_minute_bar(self, bar: object, state: IntradayState, ctx: StrategyContext) -> IntradaySignal:
        close = getattr(bar, "close", 0.0)
        vwap = state.vwap
        cfg: VWAPDeviationConfig = self.config  # type: ignore
        if vwap <= 0 or state.cumulative_volume < cfg.min_volume_bars:
            return IntradaySignal.HOLD
        dev = (close - vwap) / vwap
        if dev < cfg.max_deviation:
            return IntradaySignal.HOLD
        if state.qty == 0:
            if dev <= cfg.entry_threshold:
                return IntradaySignal.BUY
        else:
            if dev >= cfg.exit_threshold:
                return IntradaySignal.SELL
        return IntradaySignal.HOLD

@dataclass
class OvernightGapConfig(CBIntradayConfig):
    gap_threshold: float = 0.015
    decay_target_pct: float = 0.5
    entry_delay_bars: int = 5
    stop_loss_pct: float = 0.01

class CBOvernightGapDecay(CBIntradayBase):
    name = "cb_overnight_gap_decay"
    def __init__(self, config: OvernightGapConfig | None = None):
        super().__init__(config=config or OvernightGapConfig())
        self._open_price: dict[str, float] = {}
        self._bar_count_today: dict[str, int] = {}

    def on_minute_bar(self, bar: object, state: IntradayState, ctx: StrategyContext) -> IntradaySignal:
        symbol = state.symbol
        close = getattr(bar, "close", 0.0)
        cfg: OvernightGapConfig = self.config  # type: ignore

        prev_close = self._prev_close.get(symbol, 0.0)
        open_price = self._open_price.get(symbol, 0.0)

        # Increment bar count
        bar_count = self._bar_count_today.get(symbol, 0) + 1
        self._bar_count_today[symbol] = bar_count

        if prev_close <= 0 or open_price <= 0:
            return IntradaySignal.HOLD

        # Check gap significance
        gap = (open_price - prev_close) / prev_close
        if abs(gap) < cfg.gap_threshold:
            return IntradaySignal.HOLD

        # Has position → check for exit (gap decay filled)
        if state.qty > 0:
            gap_size = abs(prev_close - open_price)
            if gap_size > 0:
                fill_pct = (close - open_price) / gap_size
                if fill_pct >= cfg.decay_target_pct:
                    return IntradaySignal.SELL
            return IntradaySignal.HOLD

        # No position → check for entry after delay
        if bar_count >= cfg.entry_delay_bars:
            return IntradaySignal.BUY

        return IntradaySignal.HOLD
