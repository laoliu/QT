"""微盘价值策略 — 小市值 + 低估值 + 高质量 + 低波动"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_debt_factor,
    compute_momentum_factor,
    compute_piotroski_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_turnover_factor,
    compute_value_factor,
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
class MicroCapValueConfig(EquityRotationConfig):
    top_k: int = 50
    rebalance_days: int = 20
    min_mv: float = 50_000.0
    max_mv: float = 500_000.0
    min_price: float = 3.0
    max_price: float = 100.0
    min_turnover: float = 500.0
    min_listed_days: int = 180
    buffer_k: int = 15
    stop_loss_pct: float = 0.20
    trailing_stop_pct: float = 0.15
    max_single_pct: float = 0.025
    cash_reserve_pct: float = 0.03
    max_industry_pct: float = 0.15
    inverse_vol_weighting: bool = True
    min_roe: float = 8.0
    max_debt_to_assets: float = 60.0
    min_piotroski_score: int = 5
    require_positive_profit: bool = True
    weight_size: float = 0.30
    weight_value: float = 0.25
    weight_quality: float = 0.20
    weight_volatility: float = 0.15
    weight_turnover: float = 0.10
    weight_debt: float = 0.0
    weight_momentum: float = 0.0
    value_metric: str = "pe_ttm"
    quality_metric: str = "roe"
    volatility_lookback: int = 60
    momentum_lookback: int = 20
    momentum_skip_recent: int = 3


class MicroCapValueStrategy(EquityRotationStrategy):
    """微盘价值 — 小市值 + 低估值 + Piotroski F-score 过滤"""
    name = "micro_cap_value"

    def __init__(self, config: MicroCapValueConfig | None = None, **kwargs: object):
        super().__init__(config=config or MicroCapValueConfig(), **kwargs)

    @property
    def config(self) -> MicroCapValueConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: EquityCandidateInfo, context: StrategyContext) -> bool:
        cfg = self.config
        roe = getattr(candidate, "roe", None)
        if roe is not None and roe < cfg.min_roe:
            return False
        debt = getattr(candidate, "debt_to_assets", None)
        if debt is not None and debt > cfg.max_debt_to_assets:
            return False
        if cfg.require_positive_profit:
            profit = getattr(candidate, "net_profit", None)
            if profit is not None and profit <= 0:
                return False
        return True

    def _compute_candidate_fscore(self, candidate: EquityCandidateInfo) -> int | None:
        """计算 Piotroski F-score (简化版)"""
        score = 0
        roe = getattr(candidate, "roe", None)
        if roe is not None and roe > 0:
            score += 1
        cfo = getattr(candidate, "ocf", None)
        if cfo is not None and cfo > 0:
            score += 1
        debt = getattr(candidate, "debt_to_assets", None)
        prev_debt = getattr(candidate, "prev_debt_to_assets", None)
        if debt is not None and prev_debt is not None and debt < prev_debt:
            score += 1
        if roe is not None:
            prev_roe = getattr(candidate, "prev_roe", None)
            if prev_roe is not None and roe > prev_roe:
                score += 1
        return score

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        # Piotroski 过滤
        filtered = []
        for c in candidates:
            f = self._compute_candidate_fscore(c)
            if f is not None and f < cfg.min_piotroski_score:
                continue
            filtered.append(c)
        if not filtered:
            filtered = candidates  # fallback

        price_hist = {c.code: getattr(c, "price_history", []) for c in filtered}

        factors: dict[str, dict[str, float]] = {
            "size": compute_size_factor(filtered),
            "value": compute_value_factor(filtered, metric=cfg.value_metric),
            "quality": compute_quality_factor(filtered, metric=cfg.quality_metric),
            "volatility": compute_volatility_factor(price_hist, filtered, lookback=cfg.volatility_lookback),
            "turnover": compute_turnover_factor(filtered),
        }
        if cfg.weight_debt > 0:
            factors["debt"] = compute_debt_factor(filtered)
        if cfg.weight_momentum > 0:
            factors["momentum"] = compute_momentum_factor(price_hist, filtered, lookback=cfg.momentum_lookback)

        weights = {
            "size": cfg.weight_size, "value": cfg.weight_value,
            "quality": cfg.weight_quality, "volatility": cfg.weight_volatility,
            "turnover": cfg.weight_turnover, "debt": cfg.weight_debt,
            "momentum": cfg.weight_momentum,
        }

        composite: dict[str, float] = {}
        for c in filtered:
            s = sum(weights.get(f, 0) * factors.get(f, {}).get(c.code, 0.0) for f in weights)
            composite[c.code] = s

        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
