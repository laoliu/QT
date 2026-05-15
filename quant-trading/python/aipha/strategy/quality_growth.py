"""质量成长策略 — 高 ROE + 盈利增长 + 动量筛选"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_volatility_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)

if TYPE_CHECKING:
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class QualityGrowthConfig(EquityRotationConfig):
    top_k: int = 25
    rebalance_days: int = 15
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 1500.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.15
    buffer_k: int = 8
    max_single_pct: float = 0.06
    min_mv: float = 300_000.0
    max_mv: float = 5_000_000.0
    inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20
    min_roe: float = 10.0
    min_revenue_growth: float = 0.0
    min_profit_growth: float = 0.0
    max_pe: float = 50.0
    max_debt_to_assets: float = 65.0
    weight_earnings_growth: float = 0.30
    weight_momentum: float = 0.25
    weight_quality: float = 0.20
    weight_volatility: float = 0.15
    weight_valuation_pct: float = 0.10
    momentum_lookback: int = 20
    momentum_skip_recent: int = 3
    momentum_multi_period: bool = True
    volatility_lookback: int = 60
    piotroski_min_dims: int = 3
    quality_roe_weight: float = 0.50
    quality_piotroski_weight: float = 0.50


class QualityGrowthStrategy(EquityRotationStrategy):
    """质量成长 — ROE 高 + 盈利增长 + 动量双确认"""
    name = "quality_growth"

    def __init__(self, config: QualityGrowthConfig | None = None, **kwargs: object):
        super().__init__(config=config or QualityGrowthConfig(), **kwargs)

    @property
    def config(self) -> QualityGrowthConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: EquityCandidateInfo, context: StrategyContext) -> bool:
        cfg = self.config
        roe = getattr(candidate, "roe", None)
        if roe is not None and roe < cfg.min_roe:
            return False
        pe = getattr(candidate, "pe_ttm", None)
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        debt = getattr(candidate, "debt_to_assets", None)
        if debt is not None and debt > cfg.max_debt_to_assets:
            return False
        revenue = getattr(candidate, "revenue_yoy", None)
        if revenue is not None and revenue < cfg.min_revenue_growth:
            return False
        profit = getattr(candidate, "dt_netprofit_yoy", None)
        if profit is not None and profit < cfg.min_profit_growth:
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        price_hist = {c.code: getattr(c, "price_history", []) for c in candidates}

        growth = compute_earnings_growth_factor(candidates)
        mom = compute_momentum_factor(price_hist, candidates, lookback=cfg.momentum_lookback)
        roe_scores = compute_quality_factor(candidates)
        pio = compute_piotroski_factor(candidates, min_dims=cfg.piotroski_min_dims)
        vol = compute_volatility_factor(price_hist, candidates, lookback=cfg.volatility_lookback)

        composite: dict[str, float] = {}
        for c in candidates:
            qual = (cfg.quality_roe_weight * roe_scores.get(c.code, 0.0)
                    + cfg.quality_piotroski_weight * pio.get(c.code, 0.0))
            composite[c.code] = (
                cfg.weight_earnings_growth * growth.get(c.code, 0.0)
                + cfg.weight_momentum * mom.get(c.code, 0.0)
                + cfg.weight_quality * qual
                + cfg.weight_volatility * vol.get(c.code, 0.0)
            )
        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
