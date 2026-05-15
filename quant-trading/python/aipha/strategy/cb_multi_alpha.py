"""可转债多阿尔法策略（小盘+动量+换手突增）"""
from __future__ import annotations
import logging
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class CBMultiAlphaConfig(CBRotationConfig):
    top_k: int = 8; rebalance_days: int = 1; buffer_k: int = 3; max_price: float = 150.0
    max_issue_size: float = 20000.0; min_issue_size: float = 0.0; min_turnover: float = 10.0
    min_days_to_maturity: int = 60; exclude_st: bool = True; exclude_called: bool = True
    max_premium: float = 80.0; momentum_lookback: int = 10; surge_lookback: int = 20
    warmup_bars: int = 25; w_size: float = 0.30; w_momentum: float = 0.20; w_surge: float = 0.20
    w_premium: float = 0.15; w_price: float = 0.15; stop_loss_pct: float = 0.12
    min_positions: int = 3; lot_size: int = 10

class CBMultiAlphaStrategy(CBRotationStrategy):
    name = "cb_multi_alpha"
    def __init__(self, config: CBMultiAlphaConfig | None = None, **kw: object):
        super().__init__(config=config or CBMultiAlphaConfig(), **kw)
    @property
    def config(self) -> CBMultiAlphaConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        maxlen = max(self.config.momentum_lookback, self.config.surge_lookback) + 5
        for sym, bar in bars.items():
            close = getattr(bar, "close", None)
            meta = getattr(bar, "metadata", {}) or {}
            tv = meta.get("amount", 0.0)
            if close is not None:
                if sym not in self._close_history: self._close_history[sym] = deque(maxlen=maxlen)
                self._close_history[sym].append(close)
            if tv is not None:
                if sym not in self._turnover_history: self._turnover_history[sym] = deque(maxlen=maxlen)
                self._turnover_history[sym].append(tv)
    def _should_rebalance(self) -> bool:
        if self._bar_count <= self.config.warmup_bars:
            return False
        return super()._should_rebalance()
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        prem = getattr(c, "cb_over_rate", None)
        if prem is None:
            prem = getattr(c, "premium_rate", None)
            if prem is not None: prem = prem * 100.0
        if prem is not None and prem > cfg.max_premium: return False
        if c.symbol not in self._price_history or len(self._price_history[c.symbol]) == 0: return False
        if c.symbol not in self._amount_history or len(self._amount_history[c.symbol]) == 0: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        size = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        mom: dict[str, float] = {}
        for c in candidates:
            h = self._close_history.get(c.code)
            if h and len(h) >= cfg.momentum_lookback:
                prices = list(h); mom[c.code] = (prices[-1] - prices[-cfg.momentum_lookback]) / prices[-cfg.momentum_lookback] if prices[-cfg.momentum_lookback] > 0 else 0
            else: mom[c.code] = 0.0
        surge: dict[str, float] = {}
        for c in candidates:
            th = self._turnover_history.get(c.code)
            if th and len(th) >= cfg.surge_lookback:
                tv = list(th); recent = sum(tv[-5:]) / 5; base = sum(tv[-cfg.surge_lookback:]) / cfg.surge_lookback
                surge[c.code] = (recent - base) / base if base > 0 else 0
            else: surge[c.code] = 0.0
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        price = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        rs, rm, rsu, rpm, rp = _rank_scores(size), _rank_scores(mom), _rank_scores(surge), _rank_scores(prem), _rank_scores(price)
        composite = {c.code: cfg.w_size * rs.get(c.code, 0) + cfg.w_momentum * rm.get(c.code, 0) + cfg.w_surge * rsu.get(c.code, 0) + cfg.w_premium * rpm.get(c.code, 0) + cfg.w_price * rp.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
