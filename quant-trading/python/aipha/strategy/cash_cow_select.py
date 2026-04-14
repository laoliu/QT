"""现金牛选股策略 — 高现金质量 + 高 ROE + 低估值 + 低换手"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_quality_factor,
    compute_turnover_factor,
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
class CashCowSelectConfig(EquityRotationConfig):
    top_k: int = 25
    rebalance_days: int = 20
    min_price: float = 5.0
    max_price: float = 500.0
    min_turnover: float = 1500.0
    min_listed_days: int = 250
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10
    buffer_k: int = 8
    max_single_pct: float = 0.06
    min_mv: float = 800_000.0
    max_mv: float = float("inf")
    inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20
    weight_cash_quality: float = 0.35
    weight_roe: float = 0.25
    weight_value: float = 0.25
    weight_turnover: float = 0.15
    min_roe: float = 8.0
    max_pe: float = 35.0
    max_debt_to_assets: float = 65.0


class CashCowSelectStrategy(EquityRotationStrategy):
    """现金牛 — 现金流质量好 + ROE 稳定 + 估值合理"""
    name = "cash_cow_select"

    def __init__(self, config: CashCowSelectConfig | None = None, **kwargs: object):
        super().__init__(config=config or CashCowSelectConfig(), **kwargs)

    @property
    def config(self) -> CashCowSelectConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: EquityCandidateInfo, context: StrategyContext) -> bool:
        cfg = self.config
        roe = getattr(candidate, "roe", None)
        if roe is not None and roe < cfg.min_roe:
            return False
        pe = getattr(candidate, "pe_ttm", None)
        if pe is not None and pe > cfg.max_pe:
            return False
        debt = getattr(candidate, "debt_to_assets", None)
        if debt is not None and debt > cfg.max_debt_to_assets:
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        cash_q = compute_accruals_quality_factor(candidates)
        roe = compute_quality_factor(candidates)
        val = compute_value_factor(candidates)
        turn = compute_turnover_factor(candidates)

        composite: dict[str, float] = {}
        for c in candidates:
            composite[c.code] = (
                cfg.weight_cash_quality * cash_q.get(c.code, 0.0)
                + cfg.weight_roe * roe.get(c.code, 0.0)
                + cfg.weight_value * val.get(c.code, 0.0)
                + cfg.weight_turnover * turn.get(c.code, 0.0)
            )
        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
