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
        t = getattr(c, "amount", None) or 0.0
        return t >= cfg.min_turnover
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: "StrategyContext") -> list[str]:
        sizes = [getattr(c, "issue_size", 10000.0) or 10000.0 for c in candidates]
        r = _rank_scores(sizes, ascending=False)
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
        cfg = self.config
        if cfg.aggregation not in ("reciprocal_rank", "borda"):
            raise ValueError(f"unsupported aggregation: {cfg.aggregation}")
        variants = cfg.variants or []
        weights = cfg.weights or [1.0] * max(len(variants), 1)
        if variants and len(weights) != len(variants):
            raise ValueError(f"weights length {len(weights)} != variants length {len(variants)}")
        if not candidates:
            return []
        if not variants:
            # fallback: basic small-size ranking
            valid = [c for c in candidates if c.close > 0]
            valid.sort(key=lambda c: (getattr(c, "issue_size", float("inf")) or float("inf"), c.symbol))
            return [c.code for c in valid[:cfg.top_k]]
        # variant voting
        votes: dict[str, float] = {}
        size_map: dict[str, float] = {}
        for c in candidates:
            size_map[c.code] = getattr(c, "issue_size", float("inf")) or float("inf")
        for vi, vspec in enumerate(variants):
            w = weights[vi]
            v_max_price = vspec.get("max_price", cfg.max_price) if isinstance(vspec, dict) else cfg.max_price
            v_max_issue = vspec.get("max_issue_size", cfg.max_issue_size) if isinstance(vspec, dict) else cfg.max_issue_size
            v_min_issue = vspec.get("min_issue_size", cfg.min_issue_size) if isinstance(vspec, dict) else cfg.min_issue_size
            v_min_turnover = vspec.get("min_turnover", 0.0) if isinstance(vspec, dict) else 0.0
            v_top_k = vspec.get("top_k", cfg.top_k) if isinstance(vspec, dict) else cfg.top_k
            # filter
            filtered = []
            for c in candidates:
                if c.close <= 0: continue
                if c.close > v_max_price: continue
                sz = getattr(c, "issue_size", float("inf")) or float("inf")
                if sz < v_min_issue or sz > v_max_issue: continue
                amt = getattr(c, "amount", 0.0) or 0.0
                if v_min_turnover > 0 and amt < v_min_turnover: continue
                filtered.append(c)
            # sort by issue_size ascending (smallest first = best)
            filtered.sort(key=lambda c: (getattr(c, "issue_size", float("inf")) or float("inf"), c.symbol))
            for rank, c in enumerate(filtered[:v_top_k]):
                score = w / (rank + 1) if cfg.aggregation == "reciprocal_rank" else w * (v_top_k - rank)
                votes[c.code] = votes.get(c.code, 0.0) + score
        # sort by votes descending, tiebreak by issue_size ascending
        result = sorted(votes.keys(), key=lambda k: (-votes[k], size_map.get(k, float("inf")), k))
        return result[:cfg.top_k]
