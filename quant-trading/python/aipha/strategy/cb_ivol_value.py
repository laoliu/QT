"""可转债隐含波动率+价值策略"""
from __future__ import annotations
import logging, math
from collections import deque
from dataclasses import dataclass
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class IVOLValueConfig(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 5
    price_weight: float = 0.15; premium_weight: float = 0.10; ivol_weight: float = 0.20
    high_distance_weight: float = 0.15; theta_weight: float = 0.10; skewness_weight: float = 0.10
    size_weight: float = 0.20; ivol_lookback: int = 20; ivol_min_obs: int = 10
    high_distance_lookback: int = 252; skewness_lookback: int = 20; skewness_min_obs: int = 10
    max_price: float = 135.0; min_turnover: float = 50.0; min_days_to_maturity: int = 60
    exclude_st: bool = True; stop_loss_pct: float = 0.12; buffer_k: int = 3; lot_size: int = 10

class _IVOLTracker:
    def __init__(self):
        self._bar_count = 0
    def reset(self):
        self._bar_count = 0

class CBIVOLValueStrategy(CBRotationStrategy):
    name = "cb_ivol_value"
    def __init__(self, config: IVOLValueConfig | None = None, **kw: object):
        super().__init__(config=config or IVOLValueConfig(), **kw)
        self._returns: dict[str, deque[float]] = {}
        self._highs: dict[str, deque[float]] = {}
        self._ivol_tracker = _IVOLTracker()
    @property
    def config(self) -> IVOLValueConfig: return self._config  # type: ignore
    def _on_start_extra(self) -> None:
        self._returns.clear()
        self._highs.clear()
        self._ivol_tracker.reset()
    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: StrategyContext) -> None:
        # bars provided as argument
        self._ivol_tracker._bar_count += 1
        maxlen = max(self.config.ivol_lookback, self.config.high_distance_lookback, self.config.skewness_lookback) + 5
        for sym, bar in bars.items():
            close = getattr(bar, "close", None); high = getattr(bar, "high", None)
            if close is None: continue
            if sym not in self._returns: self._returns[sym] = deque(maxlen=maxlen)
            if sym not in self._highs: self._highs[sym] = deque(maxlen=maxlen)
            prev = list(self._returns[sym])
            if prev:
                last_close = prev[-1]
                self._returns[sym].append(math.log(close / last_close) if last_close > 0 else 0.0)
            else:
                self._returns[sym].append(0.0)
            if high is not None: self._highs[sym].append(high)
    def _get_ivol(self, symbol: str) -> float:
        rets = self._returns.get(symbol)
        if not rets or len(rets) < self.config.ivol_min_obs: return 0.0
        recent = list(rets)[-self.config.ivol_lookback:]
        if len(recent) < 2: return 0.0
        mu = sum(recent) / len(recent)
        var = sum((r - mu) ** 2 for r in recent) / (len(recent) - 1)
        return math.sqrt(var * 252) if var > 0 else 0.0
    def _get_high_distance(self, symbol: str, current: float) -> float:
        highs = self._highs.get(symbol)
        if not highs: return 0.0
        peak = max(list(highs)[-self.config.high_distance_lookback:])
        return (current - peak) / peak if peak > 0 else 0.0
    def _get_skewness(self, symbol: str) -> float:
        rets = self._returns.get(symbol)
        if not rets or len(rets) < self.config.skewness_min_obs: return 0.0
        recent = list(rets)[-self.config.skewness_lookback:]
        n = len(recent); mu = sum(recent) / n
        m2 = sum((r - mu) ** 2 for r in recent) / n; m3 = sum((r - mu) ** 3 for r in recent) / n
        return m3 / (m2 ** 1.5) if m2 > 0 else 0.0
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        cfg = self.config
        price_s = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        ivol = {c.code: -self._get_ivol(c.code) for c in candidates}
        hd = {c.code: self._get_high_distance(c.code, getattr(c, "close", 100.0) or 100.0) for c in candidates}
        skew = {c.code: -self._get_skewness(c.code) for c in candidates}
        size = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        rp, rpm, ri, rh, rs, rsz = _rank_scores(price_s), _rank_scores(prem), _rank_scores(ivol), _rank_scores(hd), _rank_scores(skew), _rank_scores(size)
        composite = {c.code: cfg.price_weight * rp.get(c.code, 0) + cfg.premium_weight * rpm.get(c.code, 0) + cfg.ivol_weight * ri.get(c.code, 0) + cfg.high_distance_weight * rh.get(c.code, 0) + cfg.skewness_weight * rs.get(c.code, 0) + cfg.size_weight * rsz.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
