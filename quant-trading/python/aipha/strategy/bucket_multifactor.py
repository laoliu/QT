"""分桶多因子策略（大/中/小盘分桶独立打分+合并）"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from aipha.factors.equity_factors import (compute_value_factor, compute_quality_factor,
    compute_momentum_factor, compute_volatility_factor, compute_size_factor, compute_turnover_factor)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class BucketSpec:
    name: str; mv_min: float; mv_max: float; top_k: int
    weight_alloc: float; factor_weights: dict[str, float]

_DEFAULT_BUCKETS = [
    BucketSpec("large", 100e9, float("inf"), 10, 0.30, {"value": 0.30, "quality": 0.30, "momentum": 0.15, "volatility": 0.15, "size": 0.10}),
    BucketSpec("mid", 20e9, 100e9, 12, 0.40, {"value": 0.25, "quality": 0.25, "momentum": 0.20, "volatility": 0.15, "size": 0.15}),
    BucketSpec("small", 0, 20e9, 8, 0.30, {"value": 0.20, "quality": 0.20, "momentum": 0.25, "volatility": 0.15, "size": 0.20}),
]

@dataclass
class BucketMultiFactorConfig(EquityRotationConfig):
    top_k: int = 30; rebalance_days: int = 10; inverse_vol_weighting: bool = True
    bucket_specs: list[dict] = field(default_factory=list)
    momentum_lookback: int = 60; momentum_skip_recent: int = 5; momentum_multi_period: bool = True
    volatility_lookback: int = 60; str_lookback: int = 5; value_metric: str = "pe_ttm"
    quality_metric: str = "roe"

class BucketMultiFactorStrategy(EquityRotationStrategy):
    name = "bucket_multifactor"
    def __init__(self, config: BucketMultiFactorConfig | None = None, **kw: object):
        super().__init__(config=config or BucketMultiFactorConfig(), **kw)
        self._buckets = _DEFAULT_BUCKETS
    @property
    def config(self) -> BucketMultiFactorConfig: return self._config  # type: ignore
    def _score_bucket(self, cands: list[EquityCandidateInfo], fw: dict[str, float], ctx: StrategyContext) -> list[str]:
        ph = {c.code: getattr(c, "price_history", []) for c in cands}
        factors = {"value": compute_value_factor(cands), "quality": compute_quality_factor(cands),
                   "momentum": compute_momentum_factor(ph, cands), "volatility": compute_volatility_factor(ph, cands),
                   "size": compute_size_factor(cands)}
        composite = {}
        for c in cands:
            composite[c.code] = sum(fw.get(n, 0) * factors[n].get(c.code, 0) for n in factors)
        return sorted(composite, key=composite.get, reverse=True)  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        result: list[str] = []
        for bucket in self._buckets:
            bucket_cands = [c for c in candidates if bucket.mv_min <= (getattr(c, "circ_mv", 0) or 0) < bucket.mv_max]
            if not bucket_cands: continue
            ranked = self._score_bucket(bucket_cands, bucket.factor_weights, ctx)
            result.extend(ranked[:bucket.top_k])
        return result[:self.config.top_k]
