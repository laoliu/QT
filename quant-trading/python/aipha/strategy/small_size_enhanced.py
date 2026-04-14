"""可转债小规模增强策略 — 规模 + 溢价率 + 动量"""
from __future__ import annotations
import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
from aipha.strategy.cb_ultra import _rank_scores
if TYPE_CHECKING:
    from aipha.domain.models import Bar
    from aipha.strategy.base import StrategyContext
logger = logging.getLogger(__name__)

@dataclass
class SmallSizeEnhancedConfig(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 1; max_price: float = 115.0
    max_issue_size: float = 30000.0; min_issue_size: float = 3000.0
    min_turnover: float = 50.0; min_days_to_maturity: int = 60
    exclude_st: bool = True; stop_loss_pct: float = 0.15; lot_size: int = 10
    buffer_k: int = 5; size_weight: float = 0.50; premium_weight: float = 0.30
    momentum_weight: float = 0.20; max_premium: float = 50.0

class SmallSizeEnhancedStrategy(CBRotationStrategy):
    name = "small_size_enhanced"
    def __init__(self, config: SmallSizeEnhancedConfig | None = None, **kw: object):
        super().__init__(config=config or SmallSizeEnhancedConfig(), **kw)
        self._hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=60))
    @property
    def config(self) -> SmallSizeEnhancedConfig: return self._config  # type: ignore
    def _on_bar_update_state(self, bars: dict[str, "Bar"], ctx: "StrategyContext") -> None:
        for code, bar in bars.items(): self._hist[code].append(bar.close)
    def _extra_filter(self, c: CandidateInfo, ctx: "StrategyContext") -> bool:
        cfg = self.config
        sz = getattr(c, "issue_size", None) or float("inf")
        if sz < cfg.min_issue_size or sz > cfg.max_issue_size: return False
        prem = getattr(c, "premium_rate", None)
        if prem is not None and prem * 100 > cfg.max_premium: return False
        return (getattr(c, "turnover", None) or 0.0) >= cfg.min_turnover
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: "StrategyContext") -> list[str]:
        cfg = self.config
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        prems = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        moms = []
        for c in candidates:
            h = self._hist.get(c.code)
            if h and len(h) >= 20:
                old = h[-20]; moms.append((h[-1] - old) / old if old > 0 else 0.0)
            else: moms.append(0.0)
        r_sz = _rank_scores(sizes, ascending=True)
        r_pr = _rank_scores(prems, ascending=True)
        r_mm = _rank_scores(moms, ascending=False)
        scored = [(candidates[i].code, cfg.size_weight * r_sz[i] + cfg.premium_weight * r_pr[i] + cfg.momentum_weight * r_mm[i]) for i in range(len(candidates))]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
