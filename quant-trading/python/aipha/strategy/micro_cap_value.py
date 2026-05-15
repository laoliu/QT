"""微盘价值策略 — 小市值 + 低估值 + 高质量 + 低波动"""
from __future__ import annotations

import logging
import math
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
        roe = candidate.metadata.get("roe", None)
        if roe is None or (isinstance(roe, float) and math.isnan(roe)):
            return False
        if roe < cfg.min_roe:
            return False
        debt = candidate.metadata.get("debt_to_assets", None)
        if debt is not None and not (isinstance(debt, float) and math.isnan(debt)):
            if debt > cfg.max_debt_to_assets:
                return False
        if cfg.require_positive_profit:
            eps = candidate.metadata.get("eps", None)
            if eps is not None and not (isinstance(eps, float) and math.isnan(eps)):
                if eps <= 0:
                    return False
        return True

    def _compute_candidate_fscore(self, candidate: EquityCandidateInfo) -> int | None:
        """计算 Piotroski F-score (简化版, 7维)"""
        meta = candidate.metadata
        score = 0
        dims = 0

        # 1. ROA positive
        roa = meta.get("roa", None)
        if roa is not None and not (isinstance(roa, float) and math.isnan(roa)):
            dims += 1
            if roa > 0:
                score += 1

        # 2. CFO positive
        cfps = meta.get("cfps", None)
        if cfps is not None and not (isinstance(cfps, float) and math.isnan(cfps)):
            dims += 1
            if cfps > 0:
                score += 1

        # 3. Accrual: CFO > EPS
        eps = meta.get("eps", None)
        if (cfps is not None and eps is not None
                and not (isinstance(cfps, float) and math.isnan(cfps))
                and not (isinstance(eps, float) and math.isnan(eps))):
            dims += 1
            if cfps > eps:
                score += 1

        # 4. ROA improving
        prev_roa = meta.get("prev_roa", None)
        if (roa is not None and prev_roa is not None
                and not (isinstance(roa, float) and math.isnan(roa))
                and not (isinstance(prev_roa, float) and math.isnan(prev_roa))):
            dims += 1
            if roa > prev_roa:
                score += 1

        # 5. Debt decreasing
        debt = meta.get("debt_to_assets", None)
        prev_debt = meta.get("prev_debt_to_assets", None)
        if (debt is not None and prev_debt is not None
                and not (isinstance(debt, float) and math.isnan(debt))
                and not (isinstance(prev_debt, float) and math.isnan(prev_debt))):
            dims += 1
            if debt < prev_debt:
                score += 1

        # 6. Current ratio improving
        cr = meta.get("current_ratio", None)
        prev_cr = meta.get("prev_current_ratio", None)
        if (cr is not None and prev_cr is not None
                and not (isinstance(cr, float) and math.isnan(cr))
                and not (isinstance(prev_cr, float) and math.isnan(prev_cr))):
            dims += 1
            if cr > prev_cr:
                score += 1

        # 7. Gross margin improving
        gm = meta.get("grossprofit_margin", None)
        prev_gm = meta.get("prev_grossprofit_margin", None)
        if (gm is not None and prev_gm is not None
                and not (isinstance(gm, float) and math.isnan(gm))
                and not (isinstance(prev_gm, float) and math.isnan(prev_gm))):
            dims += 1
            if gm > prev_gm:
                score += 1

        if dims < 3:
            return None
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
