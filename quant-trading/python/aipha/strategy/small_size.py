"""可转债小规模策略 — 按剩余规模排序"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
from aipha.strategy.cb_ultra import _rank_scores
if TYPE_CHECKING:
    from aipha.strategy.base import StrategyContext
logger = logging.getLogger(__name__)

@dataclass
class SmallSizeConfig(CBRotationConfig):
    top_k: int = 20; rebalance_days: int = 5; max_price: float = 130.0
    max_issue_size: float = 100000.0; min_issue_size: float = 5000.0
    min_turnover: float = 50.0; min_days_to_maturity: int = 60
    exclude_st: bool = True; stop_loss_pct: float = 0.15; lot_size: int = 10
    min_tradable_candidates: int = 0

class SmallSizeStrategy(CBRotationStrategy):
    name = "small_size"
    def __init__(self, config: SmallSizeConfig | None = None, **kw: object):
        super().__init__(config=config or SmallSizeConfig(), **kw)
    @property
    def config(self) -> SmallSizeConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: "StrategyContext") -> bool:
        sz = getattr(c, "issue_size", None) or float("inf")
        cfg = self.config
        if sz < cfg.min_issue_size or sz > cfg.max_issue_size: return False
        t = getattr(c, "turnover", None) or 0.0
        return t >= cfg.min_turnover
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: "StrategyContext") -> list[str]:
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        r = _rank_scores(sizes, ascending=True)
        scored = sorted(zip([c.code for c in candidates], r), key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:self.config.top_k]]

@dataclass
class SmallSizeEnsembleConfig(SmallSizeConfig):
    variants: list[str] = None  # type: ignore[assignment]
    weights: list[float] = None  # type: ignore[assignment]
    aggregation: str = "reciprocal_rank"
    def __post_init__(self) -> None:
        if self.variants is None: self.variants = []
        if self.weights is None: self.weights = []

class SmallSizeEnsembleStrategy(CBRotationStrategy):
    name = "small_size_ensemble"
    def __init__(self, config: SmallSizeEnsembleConfig | None = None, **kw: object):
        super().__init__(config=config or SmallSizeEnsembleConfig(), **kw)
    @property
    def config(self) -> SmallSizeEnsembleConfig: return self._config  # type: ignore
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: "StrategyContext") -> list[str]:
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        r = _rank_scores(sizes, ascending=True)
        scored = sorted(zip([c.code for c in candidates], r), key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:self.config.top_k]]
