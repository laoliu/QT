"""新债策略 + 新债集成"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo, days_since_listed
logger = logging.getLogger(__name__)

@dataclass
class NewBondConfig(CBRotationConfig):
    top_k: int = 15; rebalance_days: int = 5; min_age_days: int = 5; max_age_days: int = 90
    max_price: float = 130.0; fallback_max_age_days: int = 0

class NewBondStrategy(CBRotationStrategy):
    name = "new_bond"
    def __init__(self, config: NewBondConfig | None = None, **kw: object):
        super().__init__(config=config or NewBondConfig(), **kw)
        self._effective_max_age: int = 0
    @property
    def config(self) -> NewBondConfig: return self._config  # type: ignore
    def on_start(self) -> None:
        super().on_start()
        self._effective_max_age = self.config.max_age_days
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        list_date = getattr(c, "list_date", "") or ""
        if not list_date:
            return False
        now = getattr(ctx, "current_time", None)
        age = days_since_listed(list_date, now)
        if age < cfg.min_age_days:
            return False
        if age > self._effective_max_age:
            if cfg.fallback_max_age_days > 0 and age <= cfg.fallback_max_age_days:
                pass
            else:
                return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        if not candidates:
            return []
        price = {c.code: getattr(c, "close", 999.0) or 999.0 for c in candidates}
        return sorted(price, key=price.get)[:self.config.top_k]  # type: ignore
    def _relax_filters_one_step(self) -> bool:
        cfg = self.config
        if cfg.fallback_max_age_days > 0 and self._effective_max_age < cfg.fallback_max_age_days:
            self._effective_max_age = min(self._effective_max_age + 30, cfg.fallback_max_age_days)
            return True
        return False
    def _maybe_tighten_filters(self) -> None:
        cfg = self.config
        if self._effective_max_age > cfg.max_age_days:
            self._effective_max_age = max(self._effective_max_age - 10, cfg.max_age_days)

@dataclass
class NewBondEnsembleConfig(NewBondConfig):
    variants: list[dict] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    aggregation: str = "reciprocal_rank"

class NewBondEnsembleStrategy(CBRotationStrategy):
    name = "new_bond_ensemble"
    def __init__(self, config: NewBondEnsembleConfig | None = None, **kw: object):
        super().__init__(config=config or NewBondEnsembleConfig(), **kw)
    @property
    def config(self) -> NewBondEnsembleConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        age = getattr(c, "listed_days", None)
        if age is not None and (age < cfg.min_age_days or age > cfg.max_age_days): return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        from aipha.strategy.cb_ultra import _rank_scores
        # reciprocal rank fusion across multiple sort criteria
        price = {c.code: -(getattr(c, "close", 200.0) or 200.0) for c in candidates}
        prem = {c.code: -(getattr(c, "premium_rate", 50.0) or 50.0) for c in candidates}
        size = {c.code: -(getattr(c, "issue_size", 1e9) or 1e9) for c in candidates}
        rp, rpm, rs = _rank_scores(price), _rank_scores(prem), _rank_scores(size)
        composite = {c.code: rp.get(c.code, 0) + rpm.get(c.code, 0) + rs.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:self.config.top_k]  # type: ignore
