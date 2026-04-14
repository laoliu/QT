"""质量动量复合策略 — 质量 + 风险调整动量 + 价值 + 规模"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_quality_factor,
    compute_risk_adjusted_momentum,
    compute_size_factor,
    compute_value_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)

logger = logging.getLogger(__name__)


@dataclass
class QualityMomentumConfig(EquityRotationConfig):
    top_k: int = 30
    rebalance_days: int = 15
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 800.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 10
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = 5_000_000.0
    max_industry_pct: float = 0.20
    weight_quality: float = 0.30
    weight_momentum: float = 0.30
    weight_value: float = 0.20
    weight_size: float = 0.20
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    volatility_lookback: int = 60
    max_pe: float = 80.0


class QualityMomentumComboStrategy(EquityRotationStrategy):
    """质量动量复合 — 质量 + 风险调整后动量"""
    name = "quality_momentum_combo"

    def __init__(self, config: QualityMomentumConfig | None = None, **kwargs: object):
        super().__init__(config=config or QualityMomentumConfig(), **kwargs)

    @property
    def config(self) -> QualityMomentumConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: EquityCandidateInfo, context: StrategyContext) -> bool:
        pe = getattr(candidate, "pe_ttm", None)
        if pe is not None and pe > self.config.max_pe:
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        price_hist = {c.code: getattr(c, "price_history", []) for c in candidates}

        qual = compute_quality_factor(candidates)
        mom = compute_risk_adjusted_momentum(price_hist, candidates, lookback=cfg.momentum_lookback)
        val = compute_value_factor(candidates)
        sz = compute_size_factor(candidates)

        composite: dict[str, float] = {}
        for c in candidates:
            composite[c.code] = (
                cfg.weight_quality * qual.get(c.code, 0.0)
                + cfg.weight_momentum * mom.get(c.code, 0.0)
                + cfg.weight_value * val.get(c.code, 0.0)
                + cfg.weight_size * sz.get(c.code, 0.0)
            )
        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
