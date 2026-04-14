"""F-Score + 价值增强"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_accruals_quality_factor, compute_piotroski_factor, compute_value_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class FScoreValueConfig(EquityRotationConfig):
    top_k: int = 25; rebalance_days: int = 20; min_price: float = 5.0; max_price: float = 500.0
    min_turnover: float = 2000.0; min_listed_days: int = 250; stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10; buffer_k: int = 8; max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0; max_mv: float = float("inf"); inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20; weight_fscore: float = 0.45; weight_value_bp: float = 0.35
    weight_accruals: float = 0.20; max_pb: float = 4.0; max_pe: float = 40.0
    min_fscore: int = 5

class FScoreValueEnhancedStrategy(EquityRotationStrategy):
    name = "fscore_value_enhanced"
    def __init__(self, config: FScoreValueConfig | None = None, **kw: object):
        super().__init__(config=config or FScoreValueConfig(), **kw)
    @property
    def config(self) -> FScoreValueConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        pb = getattr(c, "pb", None)
        if pb is not None and pb > cfg.max_pb: return False
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe: return False
        fs = getattr(c, "f_score", None)
        if fs is not None and fs < cfg.min_fscore: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config
        fs = compute_piotroski_factor(candidates); val = compute_value_factor(candidates)
        acc = compute_accruals_quality_factor(candidates)
        composite = {c.code: cfg.weight_fscore * fs.get(c.code, 0.0) + cfg.weight_value_bp * val.get(c.code, 0.0) + cfg.weight_accruals * acc.get(c.code, 0.0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
