"""行业动量轮动"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_momentum_factor, compute_quality_factor, compute_turnover_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class SectorMomentumConfig(EquityRotationConfig):
    top_k: int = 25; rebalance_days: int = 10; min_price: float = 5.0; max_price: float = 500.0
    min_turnover: float = 3000.0; min_listed_days: int = 250; stop_loss_pct: float = 0.10
    trailing_stop_pct: float = 0.08; buffer_k: int = 8; max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0; max_mv: float = float("inf"); inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.30; top_industries: int = 6; industry_momentum_lookback: int = 20
    weight_industry_mom: float = 0.35; weight_stock_mom: float = 0.30; weight_quality: float = 0.20
    weight_turnover: float = 0.15

def _industry_rank_scores(candidates: list[EquityCandidateInfo], lookback: int) -> dict[str, float]:
    from collections import defaultdict
    by_ind: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for c in candidates:
        ind = getattr(c, "industry", "")
        ret = getattr(c, f"return_{lookback}d", None) or getattr(c, "return_20d", 0.0) or 0.0
        by_ind[ind].append((c.code, ret))
    ind_avg = {ind: sum(r for _, r in items) / max(len(items), 1) for ind, items in by_ind.items()}
    ranked = sorted(ind_avg, key=ind_avg.get, reverse=True)  # type: ignore
    n = max(len(ranked), 1)
    ind_score = {ind: 1.0 - i / n for i, ind in enumerate(ranked)}
    return {c.code: ind_score.get(getattr(c, "industry", ""), 0.5) for c in candidates}

class SectorMomentumStrategy(EquityRotationStrategy):
    name = "sector_momentum"
    def __init__(self, config: SectorMomentumConfig | None = None, **kw: object):
        super().__init__(config=config or SectorMomentumConfig(), **kw)
    @property
    def config(self) -> SectorMomentumConfig: return self._config  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        ind_mom = _industry_rank_scores(candidates, cfg.industry_momentum_lookback)
        stk_mom = compute_momentum_factor(ph, candidates)
        qual = compute_quality_factor(candidates)
        turn = compute_turnover_factor(candidates)
        composite = {c.code: cfg.weight_industry_mom * ind_mom.get(c.code, 0) + cfg.weight_stock_mom * stk_mom.get(c.code, 0) + cfg.weight_quality * qual.get(c.code, 0) + cfg.weight_turnover * turn.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
