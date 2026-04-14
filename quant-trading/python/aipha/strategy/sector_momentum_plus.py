"""行业动量增强版（板块+个股双重动量+资金流）"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_momentum_factor, compute_quality_factor, compute_turnover_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
from aipha.strategy.sector_momentum import _industry_rank_scores
logger = logging.getLogger(__name__)

@dataclass
class SectorMomentumPlusConfig(EquityRotationConfig):
    top_k: int = 25; rebalance_days: int = 10; min_price: float = 5.0; max_price: float = 500.0
    min_turnover: float = 3000.0; min_listed_days: int = 250; stop_loss_pct: float = 0.10
    trailing_stop_pct: float = 0.08; buffer_k: int = 8; max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0; max_mv: float = float("inf"); inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.30; top_n_sectors: int = 5; sector_momentum_lookback: int = 20
    weight_sector_mom: float = 0.30; weight_stock_mom: float = 0.25; weight_fund_flow: float = 0.20
    weight_quality: float = 0.15; weight_turnover: float = 0.10; fund_flow_lookback: int = 10

def _fund_flow_scores(candidates: list[EquityCandidateInfo]) -> dict[str, float]:
    raw = {c.code: getattr(c, "net_fund_flow", 0.0) or 0.0 for c in candidates}
    if not raw: return {}
    lo, hi = min(raw.values()), max(raw.values())
    rng = hi - lo if hi > lo else 1.0
    return {k: (v - lo) / rng for k, v in raw.items()}

class SectorMomentumPlusStrategy(EquityRotationStrategy):
    name = "sector_momentum_plus"
    def __init__(self, config: SectorMomentumPlusConfig | None = None, **kw: object):
        super().__init__(config=config or SectorMomentumPlusConfig(), **kw)
    @property
    def config(self) -> SectorMomentumPlusConfig: return self._config  # type: ignore
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        sec_mom = _industry_rank_scores(candidates, cfg.sector_momentum_lookback)
        stk_mom = compute_momentum_factor(ph, candidates)
        ff = _fund_flow_scores(candidates)
        qual = compute_quality_factor(candidates)
        turn = compute_turnover_factor(candidates)
        composite = {c.code: cfg.weight_sector_mom * sec_mom.get(c.code, 0) + cfg.weight_stock_mom * stk_mom.get(c.code, 0) + cfg.weight_fund_flow * ff.get(c.code, 0) + cfg.weight_quality * qual.get(c.code, 0) + cfg.weight_turnover * turn.get(c.code, 0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
