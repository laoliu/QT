"""盈利能力复合策略"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_accruals_quality_factor, compute_quality_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class ProfitabilityCompositeConfig(EquityRotationConfig):
    top_k: int = 25; rebalance_days: int = 20; min_price: float = 5.0; max_price: float = 500.0
    min_turnover: float = 2000.0; min_listed_days: int = 250; stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10; buffer_k: int = 8; max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0; max_mv: float = float("inf"); inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20; weight_gross_profit: float = 0.40; weight_roe: float = 0.35
    weight_accruals: float = 0.25; min_gross_margin: float = 20.0; min_roe: float = 5.0

def _gross_profit_factor(candidates: list[EquityCandidateInfo]) -> dict[str, float]:
    margins = {c.code: getattr(c, "gross_margin", 0.0) or 0.0 for c in candidates}
    if not margins: return {}
    lo, hi = min(margins.values()), max(margins.values())
    rng = hi - lo if hi > lo else 1.0
    return {code: (v - lo) / rng for code, v in margins.items()}

class ProfitabilityCompositeStrategy(EquityRotationStrategy):
    name = "profitability_composite"
    def __init__(self, config: ProfitabilityCompositeConfig | None = None, **kw: object):
        super().__init__(config=config or ProfitabilityCompositeConfig(), **kw)
    @property
    def config(self) -> ProfitabilityCompositeConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        gm = getattr(c, "gross_margin", None)
        if gm is not None and gm < cfg.min_gross_margin: return False
        roe = getattr(c, "roe", None)
        if roe is not None and roe < cfg.min_roe: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config
        gp = _gross_profit_factor(candidates); roe_s = compute_quality_factor(candidates)
        acc = compute_accruals_quality_factor(candidates)
        composite = {c.code: cfg.weight_gross_profit * gp.get(c.code, 0.0) + cfg.weight_roe * roe_s.get(c.code, 0.0) + cfg.weight_accruals * acc.get(c.code, 0.0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
