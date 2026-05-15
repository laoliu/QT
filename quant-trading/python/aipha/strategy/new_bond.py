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
    def _compute_age(self, c: CandidateInfo, ctx: StrategyContext) -> int | None:
        from datetime import datetime
        ld = getattr(c, "list_date", "") or ""
        if not ld or not ctx or not ctx.current_time:
            return None
        try:
            if len(ld) == 8:
                ld_dt = datetime.strptime(ld, "%Y%m%d")
            else:
                ld_dt = datetime.fromisoformat(ld)
            return (ctx.current_time - ld_dt).days
        except Exception:
            return None
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config
        variants = cfg.variants
        weights = cfg.weights
        if weights and variants and len(weights) != len(variants):
            raise ValueError(f"weights length ({len(weights)}) != variants length ({len(variants)})")
        if not variants:
            # Degenerate: sort by price ascending
            if not candidates:
                return []
            price = {c.code: getattr(c, "close", 999.0) or 999.0 for c in candidates}
            return sorted(price, key=price.get)[:cfg.top_k]  # type: ignore
        # Variant-based reciprocal rank fusion
        scores: dict[str, float] = {}
        for vi, var in enumerate(variants):
            v_max_age = var.get("max_age_days", cfg.max_age_days)
            v_min_age = var.get("min_age_days", cfg.min_age_days)
            v_top_k = var.get("top_k", cfg.top_k)
            # Filter candidates for this variant
            filtered = []
            for c in candidates:
                age = self._compute_age(c, ctx)
                if age is not None:
                    if age < v_min_age or age > v_max_age:
                        continue
                filtered.append(c)
            # Score within variant: sort by price ascending
            filtered.sort(key=lambda c: getattr(c, "close", 999.0) or 999.0)
            filtered = filtered[:v_top_k]
            w = weights[vi] if weights else 1.0
            for rank, c in enumerate(filtered, 1):
                scores[c.code] = scores.get(c.code, 0.0) + w * (1.0 / rank)
        return sorted(scores, key=scores.get, reverse=True)[:cfg.top_k]  # type: ignore
