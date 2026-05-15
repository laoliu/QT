"""新小盘策略 + 集成版"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CBRotationConfig, CBRotationStrategy, CandidateInfo
logger = logging.getLogger(__name__)

@dataclass
class NewSmallConfig(CBRotationConfig):
    top_k: int = 10; rebalance_days: int = 3; min_age_days: int = 5; max_age_days: int = 90
    max_issue_size: float = 50000.0; min_issue_size: float = 3000.0; min_days_to_maturity: int = 0
    fallback_max_age_days: int = 0

class NewSmallStrategy(CBRotationStrategy):
    name = "new_small"
    def __init__(self, config: NewSmallConfig | None = None, **kw: object):
        super().__init__(config=config or NewSmallConfig(), **kw)
    @property
    def config(self) -> NewSmallConfig: return self._config  # type: ignore
    def _extra_filter(self, c: CandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        age = getattr(c, "listed_days", None)
        if age is None:
            ld = getattr(c, "list_date", None) or (c.metadata.get("list_date") if hasattr(c, "metadata") else None)
            if ld and ctx and ctx.current_time:
                try:
                    from datetime import datetime
                    if isinstance(ld, str) and len(ld) == 8:
                        ld_dt = datetime.strptime(ld, "%Y%m%d")
                    elif isinstance(ld, str):
                        ld_dt = datetime.fromisoformat(ld)
                    else:
                        ld_dt = ld
                    age = (ctx.current_time - ld_dt).days
                except Exception:
                    pass
        if age is not None:
            if age < cfg.min_age_days: return False
            if age > cfg.max_age_days:
                if cfg.fallback_max_age_days > 0 and age <= cfg.fallback_max_age_days: pass
                else: return False
        sz = getattr(c, "issue_size", None)
        if sz is not None:
            if sz > cfg.max_issue_size or sz < cfg.min_issue_size: return False
        return True
    def _score_candidates(self, candidates: list[CandidateInfo], ctx: StrategyContext) -> list[str]:
        size = {c.code: getattr(c, "issue_size", 1e9) or 1e9 for c in candidates}
        return sorted(size, key=size.get)[:self.config.top_k]  # type: ignore

@dataclass
class NewSmallEnsembleConfig(NewSmallConfig):
    variants: list[dict] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)
    aggregation: str = "reciprocal_rank"

class NewSmallEnsembleStrategy(CBRotationStrategy):
    name = "new_small_ensemble"
    def __init__(self, config: NewSmallEnsembleConfig | None = None, **kw: object):
        super().__init__(config=config or NewSmallEnsembleConfig(), **kw)
    @property
    def config(self) -> NewSmallEnsembleConfig: return self._config  # type: ignore
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
            # Degenerate: sort by issue_size ascending
            size = {c.code: getattr(c, "issue_size", 1e9) or 1e9 for c in candidates}
            return sorted(size, key=size.get)[:cfg.top_k]  # type: ignore
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
            # Score within variant: sort by issue_size ascending
            filtered.sort(key=lambda c: getattr(c, "issue_size", 1e9) or 1e9)
            filtered = filtered[:v_top_k]
            w = weights[vi] if weights else 1.0
            for rank, c in enumerate(filtered, 1):
                scores[c.code] = scores.get(c.code, 0.0) + w * (1.0 / rank)
        return sorted(scores, key=scores.get, reverse=True)[:cfg.top_k]  # type: ignore
