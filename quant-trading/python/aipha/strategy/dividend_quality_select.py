"""高分红质量精选"""
from __future__ import annotations
import logging
from dataclasses import dataclass
from aipha.factors.equity_factors import compute_accruals_quality_factor, compute_dividend_factor, compute_quality_factor, compute_value_factor, compute_volatility_factor
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo, EquityRotationConfig, EquityRotationStrategy
logger = logging.getLogger(__name__)

@dataclass
class DividendQualitySelectConfig(EquityRotationConfig):
    top_k: int = 25; rebalance_days: int = 20; min_price: float = 5.0; max_price: float = 500.0
    min_turnover: float = 2000.0; min_listed_days: int = 250; stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10; buffer_k: int = 8; max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0; max_mv: float = float("inf"); inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20; weight_dividend: float = 0.30; weight_quality: float = 0.30
    weight_volatility: float = 0.25; weight_value: float = 0.15
    quality_roe_weight: float = 0.55; quality_accruals_weight: float = 0.45
    min_dividend_yield: float = 1.0; min_roe: float = 8.0; max_pe: float = 40.0; max_debt_to_assets: float = 70.0
    volatility_lookback: int = 60

class DividendQualitySelectStrategy(EquityRotationStrategy):
    name = "dividend_quality_select"
    def __init__(self, config: DividendQualitySelectConfig | None = None, **kw: object):
        super().__init__(config=config or DividendQualitySelectConfig(), **kw)
    @property
    def config(self) -> DividendQualitySelectConfig: return self._config  # type: ignore
    def _extra_filter(self, c: EquityCandidateInfo, ctx: StrategyContext) -> bool:
        cfg = self.config
        dv = getattr(c, "dv_ratio", None)
        if dv is not None and dv < cfg.min_dividend_yield: return False
        roe = getattr(c, "roe", None)
        if roe is not None and roe < cfg.min_roe: return False
        pe = getattr(c, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe: return False
        return True
    def _score_candidates(self, candidates: list[EquityCandidateInfo], ctx: StrategyContext) -> list[str]:
        cfg = self.config; ph = {c.code: getattr(c, "price_history", []) for c in candidates}
        div = compute_dividend_factor(candidates); roe_s = compute_quality_factor(candidates)
        acc = compute_accruals_quality_factor(candidates)
        vol = compute_volatility_factor(ph, candidates, lookback=cfg.volatility_lookback)
        val = compute_value_factor(candidates)
        composite = {c.code: cfg.weight_dividend * div.get(c.code, 0.0) + cfg.weight_quality * (cfg.quality_roe_weight * roe_s.get(c.code, 0.0) + cfg.quality_accruals_weight * acc.get(c.code, 0.0)) + cfg.weight_volatility * vol.get(c.code, 0.0) + cfg.weight_value * val.get(c.code, 0.0) for c in candidates}
        return sorted(composite, key=composite.get, reverse=True)[:cfg.top_k]  # type: ignore
