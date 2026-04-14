"""可转债日内策略（VWAP偏离 + 隔夜缺口衰减）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_intraday_base import CBIntradayBase, CBIntradayConfig, IntradaySignal, IntradayState
logger = logging.getLogger(__name__)

@dataclass
class VWAPDeviationConfig(CBIntradayConfig):
    entry_threshold: float = -0.008; exit_threshold: float = -0.001
    min_volume_bars: int = 30; max_deviation: float = -0.03

class CBVWAPDeviation(CBIntradayBase):
    name = "cb_vwap_deviation"
    def __init__(self, config: VWAPDeviationConfig | None = None):
        super().__init__(config=config or VWAPDeviationConfig())
    def on_minute_bar(self, bar: object, state: IntradayState, ctx: StrategyContext) -> IntradaySignal:
        close = getattr(bar, "close", 0.0)
        vwap = getattr(state, "vwap", close)
        volume_bars = getattr(state, "bar_count", 0)
        if vwap <= 0 or volume_bars < self.config.min_volume_bars:  # type: ignore
            return IntradaySignal.HOLD
        dev = (close - vwap) / vwap
        cfg: VWAPDeviationConfig = self.config  # type: ignore
        if dev < cfg.max_deviation: return IntradaySignal.HOLD
        if not getattr(state, "has_position", False):
            if dev <= cfg.entry_threshold: return IntradaySignal.BUY
        else:
            if dev >= cfg.exit_threshold: return IntradaySignal.SELL
        return IntradaySignal.HOLD

@dataclass
class OvernightGapConfig(CBIntradayConfig):
    gap_threshold: float = 0.015; decay_target_pct: float = 0.5
    entry_delay_bars: int = 5; stop_loss_pct: float = 0.01

class CBOvernightGapDecay(CBIntradayBase):
    name = "cb_overnight_gap_decay"
    def __init__(self, config: OvernightGapConfig | None = None):
        super().__init__(config=config or OvernightGapConfig())
        self._gap_detected: bool = False; self._gap_high: float = 0.0; self._entry_price: float = 0.0
    def on_minute_bar(self, bar: object, state: IntradayState, ctx: StrategyContext) -> IntradaySignal:
        close = getattr(bar, "close", 0.0); open_ = getattr(bar, "open", 0.0)
        prev_close = getattr(state, "prev_close", 0.0); bar_idx = getattr(state, "bar_count", 0)
        cfg: OvernightGapConfig = self.config  # type: ignore
        if bar_idx == 0 and prev_close > 0:
            gap = (open_ - prev_close) / prev_close
            if gap >= cfg.gap_threshold:
                self._gap_detected = True; self._gap_high = open_
        if not self._gap_detected: return IntradaySignal.HOLD
        if not getattr(state, "has_position", False):
            if bar_idx >= cfg.entry_delay_bars:
                target = prev_close + (self._gap_high - prev_close) * cfg.decay_target_pct
                if close <= target: return IntradaySignal.BUY
        else:
            if self._entry_price > 0 and close < self._entry_price * (1 - cfg.stop_loss_pct):
                return IntradaySignal.SELL
        return IntradaySignal.HOLD
