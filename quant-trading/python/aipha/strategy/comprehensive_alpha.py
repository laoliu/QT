"""综合 Alpha 策略 — 10 因子混合"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_dividend_factor,
    compute_earnings_growth_factor,
    compute_earnings_stability_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_valuation_percentile_factor,
    compute_volatility_factor,
    compute_value_factor,
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
class ComprehensiveAlphaConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 20
    min_mv: float = 300_000.0
    max_mv: float = 5_000_000.0
    min_price: float = 5.0
    max_price: float = 200.0
    min_turnover: float = 1000.0
    min_listed_days: int = 120
    buffer_k: int = 10
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.10
    inverse_vol_weighting: bool = True
    max_single_pct: float = 0.05
    cash_reserve_pct: float = 0.03
    weight_value: float = 0.15
    weight_valuation_pct: float = 0.10
    weight_quality: float = 0.10
    weight_piotroski: float = 0.08
    weight_accruals: float = 0.05
    weight_stability: float = 0.05
    weight_momentum: float = 0.15
    weight_volatility: float = 0.12
    weight_dividend: float = 0.10
    weight_growth: float = 0.10
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    momentum_multi_period: bool = True
    volatility_lookback: int = 60
    piotroski_min_dims: int = 5
    valuation_min_history: int = 20
    stability_min_periods: int = 3


class ComprehensiveAlphaStrategy(EquityRotationStrategy):
    """综合 Alpha — 10 因子平衡组合"""
    name = "comprehensive_alpha"

    def __init__(self, config: ComprehensiveAlphaConfig | None = None, **kwargs: object):
        super().__init__(config=config or ComprehensiveAlphaConfig(), **kwargs)

    @property
    def config(self) -> ComprehensiveAlphaConfig:
        return self._config  # type: ignore[return-value]

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        price_hist = {c.code: getattr(c, "price_history", []) for c in candidates}

        val = compute_value_factor(candidates, metric=cfg.value_metric)
        vpct = compute_valuation_percentile_factor(candidates)
        qual = compute_quality_factor(candidates, metric=cfg.quality_metric)
        pio = compute_piotroski_factor(candidates, min_dims=cfg.piotroski_min_dims)
        acc = compute_accruals_quality_factor(candidates)
        stab = compute_earnings_stability_factor(candidates, min_periods=cfg.stability_min_periods)
        mom = compute_momentum_factor(price_hist, candidates, lookback=cfg.momentum_lookback)
        vol = compute_volatility_factor(price_hist, candidates, lookback=cfg.volatility_lookback)
        div = compute_dividend_factor(candidates)
        growth = compute_earnings_growth_factor(candidates)

        composite: dict[str, float] = {}
        for c in candidates:
            composite[c.code] = (
                cfg.weight_value * val.get(c.code, 0.0)
                + cfg.weight_valuation_pct * vpct.get(c.code, 0.0)
                + cfg.weight_quality * qual.get(c.code, 0.0)
                + cfg.weight_piotroski * pio.get(c.code, 0.0)
                + cfg.weight_accruals * acc.get(c.code, 0.0)
                + cfg.weight_stability * stab.get(c.code, 0.0)
                + cfg.weight_momentum * mom.get(c.code, 0.0)
                + cfg.weight_volatility * vol.get(c.code, 0.0)
                + cfg.weight_dividend * div.get(c.code, 0.0)
                + cfg.weight_growth * growth.get(c.code, 0.0)
            )
        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
