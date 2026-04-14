"""A股多因子策略 — EP/ROE/动量/特质波动率 四因子"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_ep_factor,
    compute_ivol_factor,
    compute_momentum_factor,
    compute_quality_factor,
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
class AStockMultiFactorConfig(EquityRotationConfig):
    weight_ep: float = 0.25
    weight_roe: float = 0.25
    weight_mom: float = 0.25
    weight_ivol: float = 0.25
    mom_lookback: int = 250
    mom_skip_recent: int = 20
    ivol_lookback: int = 60
    ep_pe_metric: str = "pe_ttm"
    roe_metric: str = "roe"

    def __post_init__(self) -> None:
        total = self.weight_ep + self.weight_roe + self.weight_mom + self.weight_ivol
        if total > 0:
            self.weight_ep /= total
            self.weight_roe /= total
            self.weight_mom /= total
            self.weight_ivol /= total


class AStockMultiFactorStrategy(EquityRotationStrategy):
    """A 股四因子: EP + ROE + 动量 + 低特质波动"""
    name = "a_stock_multifactor"

    def __init__(self, config: AStockMultiFactorConfig | None = None, **kwargs: object):
        super().__init__(config=config or AStockMultiFactorConfig(), **kwargs)

    @property
    def config(self) -> AStockMultiFactorConfig:
        return self._config  # type: ignore[return-value]

    @classmethod
    def from_params(cls, **params: object) -> "AStockMultiFactorStrategy":
        return cls(config=AStockMultiFactorConfig(**params))  # type: ignore[arg-type]

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self.config
        price_hist = {c.code: getattr(c, "price_history", []) for c in candidates}

        ep = compute_ep_factor(candidates, metric=cfg.ep_pe_metric)
        roe = compute_quality_factor(candidates, metric=cfg.roe_metric)
        mom = compute_momentum_factor(price_hist, candidates, lookback=cfg.mom_lookback)
        ivol = compute_ivol_factor(price_hist, candidates, lookback=cfg.ivol_lookback)

        composite: dict[str, float] = {}
        for c in candidates:
            composite[c.code] = (
                cfg.weight_ep * ep.get(c.code, 0.0)
                + cfg.weight_roe * roe.get(c.code, 0.0)
                + cfg.weight_mom * mom.get(c.code, 0.0)
                + cfg.weight_ivol * ivol.get(c.code, 0.0)
            )
        sorted_codes = sorted(composite.keys(), key=lambda c: composite[c], reverse=True)
        return sorted_codes[:cfg.top_k]
