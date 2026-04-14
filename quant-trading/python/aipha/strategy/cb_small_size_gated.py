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
    top_k: int = 5; rebalance_days: int = 1; max_issue_size: float = 20000.0
    regime_lookback: int = 10; bear_threshold: float = -0.03; recovery_threshold: float = 0.0
    cooldown_bars: int = 3; use_distress_signal: bool = False; distress_price: float = 100.0
    distress_ratio: float = 0.30; use_volume_signal: bool = False; volume_lookback: int = 20
    volume_floor_pct: float = 0.50

class SmallSizeGatedStrategy(CBRotationStrategy):
    name = "small_size_gated"
    def __init__(self, config: SmallSizeGatedConfig | None = None, **kw: object):
        super().__init__(config=config or SmallSizeGatedConfig(), **kw)
        self._index_history: deque[float] = deque(maxlen=200)
        self._risk_off: bool = False; self._cooldown: int = 0
    @property
    def config(self) -> SmallSizeGatedConfig: return self._config  # type: ignore
    def _update_regime(self) -> None:
        cfg = self.config
        if len(self._index_history) < cfg.regime_lookback: return
        idx = list(self._index_history)
        ret = (idx[-1] - idx[-cfg.regime_lookback]) / idx[-cfg.regime_lookback] if idx[-cfg.regime_lookback] > 0 else 0
        if ret < cfg.bear_threshold:
            self._risk_off = True; self._cooldown = cfg.cooldown_bars
        elif self._risk_off and ret > cfg.recovery_threshold:
            if self._cooldown > 0: self._cooldown -= 1
            else: self._risk_off = False
    def is_risk_off(self) -> bool: return self._risk_off
    def get_market_change_rate(self) -> float | None:
        if len(self._index_history) < self.config.regime_lookback: return None
        idx = list(self._index_history)
        return (idx[-1] - idx[-self.config.regime_lookback]) / idx[-self.config.regime_lookback] if idx[-self.config.regime_lookback] > 0 else 0
    def _on_bar_update_state(self, ctx: StrategyContext) -> None:
        idx_val = getattr(ctx, "index_close", None)
        if idx_val: self._index_history.append(idx_val)
        self._update_regime()
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if self._risk_off: return []
        size = {c.code: getattr(c, "issue_size", 1e9) or 1e9 for c in candidates}
        return sorted(size, key=size.get)[:self.config.top_k]  # type: ignore
