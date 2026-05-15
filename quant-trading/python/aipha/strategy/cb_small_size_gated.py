"""可转债小盘门控策略（带市场状态风控）"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class SmallSizeGatedConfig(CBRotationConfig):
    top_k: int = 5; rebalance_days: int = 1; max_price: float = 150.0
    max_issue_size: float = 20000.0; min_issue_size: float = 0.0; min_turnover: float = 1.0
    regime_lookback: int = 10; bear_threshold: float = -0.03; recovery_threshold: float = 0.0
    cooldown_bars: int = 3; use_distress_signal: bool = False; distress_price: float = 100.0
    distress_ratio: float = 0.30; use_volume_signal: bool = False; volume_lookback: int = 20
    volume_floor_pct: float = 0.50

class SmallSizeGatedStrategy(CBRotationStrategy):
    name = "small_size_gated"
    def __init__(self, config: SmallSizeGatedConfig | None = None, **kw: object):
        super().__init__(config=config or SmallSizeGatedConfig(), **kw)
        self._market_prices: deque[float] = deque(maxlen=200)
        self._market_volumes: deque[float] = deque(maxlen=200)
        self._risk_off: bool = False
        self._cooldown_remaining: int = 0
        self._risk_off_count: int = 0

    @property
    def _is_risk_off(self) -> bool:
        return self._risk_off

    @_is_risk_off.setter
    def _is_risk_off(self, value: bool) -> None:
        self._risk_off = value

    @property
    def config(self) -> SmallSizeGatedConfig: return self._config  # type: ignore

    def on_start(self) -> None:
        self._market_prices.clear()
        self._market_volumes.clear()
        self._risk_off = False
        self._cooldown_remaining = 0
        self._risk_off_count = 0

    def _update_regime(self) -> None:
        cfg = self.config
        # Volume signal check
        if cfg.use_volume_signal and len(self._market_volumes) > cfg.volume_lookback:
            vols = list(self._market_volumes)
            recent = vols[-1]
            avg = sum(vols[-cfg.volume_lookback - 1:-1]) / cfg.volume_lookback
            if avg > 0 and recent < avg * cfg.volume_floor_pct:
                self._risk_off = True
                self._risk_off_count += 1
                return
        if len(self._market_prices) < cfg.regime_lookback:
            return
        idx = list(self._market_prices)
        base = idx[-cfg.regime_lookback]
        ret = (idx[-1] - base) / base if base > 0 else 0
        if ret < cfg.bear_threshold:
            if not self._risk_off:
                self._cooldown_remaining = cfg.cooldown_bars
            self._risk_off = True
            self._risk_off_count += 1
        elif self._risk_off:
            if self._cooldown_remaining > 0:
                self._cooldown_remaining -= 1
            elif ret > cfg.recovery_threshold:
                self._risk_off = False
                self._risk_off_count = 0

    def is_risk_off(self) -> bool:
        return self._risk_off

    def get_market_change_rate(self) -> float | None:
        try:
            if len(self._market_prices) < self.config.regime_lookback:
                return None
            idx = list(self._market_prices)
            base = idx[-self.config.regime_lookback]
            return (idx[-1] - base) / base if base > 0 else 0.0
        except Exception:
            return None

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        idx_val = getattr(context, "index_close", None)
        if not idx_val and bars:
            closes = [b.close for b in bars.values() if b.close > 0]
            if closes:
                idx_val = sum(closes) / len(closes)
        if idx_val:
            self._market_prices.append(idx_val)
        self._update_regime()

    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if self._risk_off:
            return []
        sized = [(c.code, getattr(c, "issue_size", 1e9) or 1e9) for c in candidates]
        sized.sort(key=lambda x: (x[1], x[0]))
        return [s[0] for s in sized[:self.config.top_k]]
