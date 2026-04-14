"""均衡多因子策略 — 14 因子可配权重 + 可选市场情绪自适应"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_debt_factor,
    compute_dividend_factor,
    compute_earnings_growth_factor,
    compute_earnings_stability_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_str_factor,
    compute_turnover_factor,
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
class BalancedMultiFactorConfig(EquityRotationConfig):
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
    max_single_pct: float = 0.06
    cash_reserve_pct: float = 0.03
    # 14 因子权重
    weight_value: float = 0.25
    weight_quality: float = 0.25
    weight_momentum: float = 0.25
    weight_volatility: float = 0.25
    weight_dividend: float = 0.0
    weight_earnings_growth: float = 0.0
    weight_piotroski: float = 0.0
    weight_earnings_stability: float = 0.0
    weight_valuation_percentile: float = 0.0
    weight_accruals_quality: float = 0.0
    weight_debt: float = 0.0
    weight_turnover: float = 0.0
    weight_size: float = 0.0
    weight_str: float = 0.0
    # 因子参数
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    momentum_multi_period: bool = True
    volatility_lookback: int = 60
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"
    str_lookback: int = 5
    # 市场情绪自适应
    regime_adaptive: bool = False
    regime_bull_delta_value: float = -0.03
    regime_bull_delta_quality: float = 0.0
    regime_bull_delta_momentum: float = 0.08
    regime_bull_delta_volatility: float = -0.05
    regime_bear_delta_value: float = 0.05
    regime_bear_delta_quality: float = 0.03
    regime_bear_delta_momentum: float = -0.08
    regime_bear_delta_volatility: float = 0.05


class BalancedMultiFactorStrategy(EquityRotationStrategy):
    """均衡多因子 — 至多 14 因子加权组合, 可选市场情绪自适应"""
    name = "balanced_multifactor"

    def __init__(self, config: BalancedMultiFactorConfig | None = None, **kwargs: object):
        super().__init__(config=config or BalancedMultiFactorConfig(), **kwargs)

    @property
    def config(self) -> BalancedMultiFactorConfig:
        return self._config  # type: ignore[return-value]

    def _get_weights(self, context: StrategyContext) -> dict[str, float]:
        cfg = self.config
        w = {
            "value": cfg.weight_value, "quality": cfg.weight_quality,
            "momentum": cfg.weight_momentum, "volatility": cfg.weight_volatility,
            "dividend": cfg.weight_dividend, "earnings_growth": cfg.weight_earnings_growth,
            "piotroski": cfg.weight_piotroski, "earnings_stability": cfg.weight_earnings_stability,
            "valuation_percentile": cfg.weight_valuation_percentile,
            "accruals_quality": cfg.weight_accruals_quality,
            "debt": cfg.weight_debt, "turnover": cfg.weight_turnover,
            "size": cfg.weight_size, "str": cfg.weight_str,
        }
        if cfg.regime_adaptive:
            regime = getattr(context, "market_regime", "NEUTRAL")
            if regime == "BULL":
                w["value"] += cfg.regime_bull_delta_value
                w["quality"] += cfg.regime_bull_delta_quality
                w["momentum"] += cfg.regime_bull_delta_momentum
                w["volatility"] += cfg.regime_bull_delta_volatility
            elif regime == "BEAR":
                w["value"] += cfg.regime_bear_delta_value
                w["quality"] += cfg.regime_bear_delta_quality
                w["momentum"] += cfg.regime_bear_delta_momentum
                w["volatility"] += cfg.regime_bear_delta_volatility
        # 归一化, 剔除非正权重
        w = {k: max(0, v) for k, v in w.items()}
        total = sum(w.values()) or 1.0
        return {k: v / total for k, v in w.items() if v > 0}

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        weights = self._get_weights(context)
        price_hist = {c.code: getattr(c, "price_history", []) for c in candidates}

        factors: dict[str, dict[str, float]] = {}
        factor_fns = {
            "value": lambda: compute_value_factor(candidates, metric=cfg.value_metric),
            "quality": lambda: compute_quality_factor(candidates, metric=cfg.quality_metric),
            "momentum": lambda: compute_momentum_factor(price_hist, candidates, lookback=cfg.momentum_lookback),
            "volatility": lambda: compute_volatility_factor(price_hist, candidates, lookback=cfg.volatility_lookback),
            "dividend": lambda: compute_dividend_factor(candidates),
            "earnings_growth": lambda: compute_earnings_growth_factor(candidates),
            "piotroski": lambda: compute_piotroski_factor(candidates),
            "earnings_stability": lambda: compute_earnings_stability_factor(candidates),
            "valuation_percentile": lambda: compute_valuation_percentile_factor(candidates),
            "accruals_quality": lambda: compute_accruals_quality_factor(candidates),
            "debt": lambda: compute_debt_factor(candidates),
            "turnover": lambda: compute_turnover_factor(candidates),
            "size": lambda: compute_size_factor(candidates),
            "str": lambda: compute_str_factor(price_hist, candidates, lookback=cfg.str_lookback),
        }
        for name in weights:
            fn = factor_fns.get(name)
            if fn:
                factors[name] = fn()

        composite: dict[str, float] = {}
        for c in candidates:
            s = sum(weights.get(f, 0) * factors.get(f, {}).get(c.code, 0.0)
                    for f in weights)
            composite[c.code] = s

        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
