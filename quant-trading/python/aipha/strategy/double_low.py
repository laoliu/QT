"""可转债双低策略 & 转股套利策略"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)

if TYPE_CHECKING:
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


# ── 双低策略 ──────────────────────────────────────────────

@dataclass
class DoubleLowMetrics:
    code: str
    price: float
    premium_rate: float
    double_low_score: float


@dataclass
class DoubleLowConfig(CBRotationConfig):
    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    max_premium: float = 0.30
    price_weight: float = 0.5
    premium_weight: float = 0.5
    low_size_bonus: float = 0.0
    size_threshold: float = 500_000.0
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10
    force_redeem_exit: bool = True


class DoubleLowStrategy(CBRotationStrategy):
    """可转债双低策略 — 价格 + 溢价率加权排序"""
    name = "double_low"

    def __init__(self, config: DoubleLowConfig | None = None, **kwargs: object):
        super().__init__(config=config or DoubleLowConfig(), **kwargs)

    @property
    def config(self) -> DoubleLowConfig:
        return self._config  # type: ignore[return-value]

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            premium = getattr(c, "premium_rate", None) or 0.0
            price = c.price
            dl_score = price * cfg.price_weight + premium * 100 * cfg.premium_weight
            # 低规模加分 (分数越低越好)
            if cfg.low_size_bonus > 0:
                remain = getattr(c, "remain_size", None) or float("inf")
                if remain < cfg.size_threshold:
                    dl_score -= cfg.low_size_bonus
            scored.append((c.code, dl_score))
        scored.sort(key=lambda x: x[1])  # 越低越好
        return [s[0] for s in scored[:cfg.top_k]]


# ── 转股套利策略 ──────────────────────────────────────────

@dataclass
class ConversionArbitrageConfig(CBRotationConfig):
    """转股套利策略参数"""
    top_k: int = 10
    rebalance_days: int = 1
    min_conv_spread: float = 0.005
    max_conv_spread: float = 0.15
    min_remain_size: float = 50_000.0
    max_price: float = 200.0
    stop_loss_pct: float = 0.05
    trailing_stop_pct: float = 0.03


class ConversionArbitrageStrategy(CBRotationStrategy):
    """转股套利策略 — 转股溢价率为负时买入可转债, T+1 转股卖出"""
    name = "conversion_arbitrage"

    def __init__(self, config: ConversionArbitrageConfig | None = None, **kwargs: object):
        super().__init__(config=config or ConversionArbitrageConfig(), **kwargs)

    @property
    def config(self) -> ConversionArbitrageConfig:
        return self._config  # type: ignore[return-value]

    def _filter_candidates(self, candidates: list[CandidateInfo], context: "StrategyContext") -> list[CandidateInfo]:
        cfg = self.config
        result: list[CandidateInfo] = []
        for c in candidates:
            spread = getattr(c, "conv_spread", None)
            if spread is None:
                continue
            if cfg.min_conv_spread <= -spread <= cfg.max_conv_spread:
                remain = getattr(c, "remain_size", None) or float("inf")
                if remain >= cfg.min_remain_size and c.price <= cfg.max_price:
                    result.append(c)
        return result

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            spread = getattr(c, "conv_spread", 0.0) or 0.0
            scored.append((c.code, spread))  # 越负越好
        scored.sort(key=lambda x: x[1])
        return [s[0] for s in scored[:cfg.top_k]]
