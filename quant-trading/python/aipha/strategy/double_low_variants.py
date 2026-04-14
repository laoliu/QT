"""双低策略变体 — Z-Score / Rank / Percentile 三种标准化方式"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)

if TYPE_CHECKING:
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


# ── 归一化工具 ──────────────────────────────────────────

def _zscore_normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    mean = sum(values) / len(values)
    std = math.sqrt(sum((v - mean) ** 2 for v in values) / max(len(values), 1))
    if std < 1e-9:
        return [0.0] * len(values)
    return [(v - mean) / std for v in values]


def _rank_normalize(values: list[float], ascending: bool = True) -> list[float]:
    n = len(values)
    if n == 0:
        return []
    indexed = sorted(enumerate(values), key=lambda x: x[1], reverse=not ascending)
    result = [0.0] * n
    for rank, (idx, _) in enumerate(indexed):
        result[idx] = rank / max(n - 1, 1)
    return result


def _percentile_normalize(values: list[float]) -> list[float]:
    n = len(values)
    if n == 0:
        return []
    sorted_vals = sorted(values)
    result: list[float] = []
    for v in values:
        # 百分位 = 小于 v 的个数 / (n - 1)
        count = sum(1 for sv in sorted_vals if sv < v)
        result.append(count / max(n - 1, 1))
    return result


# ── 共用配置基础 ────────────────────────────────────────

@dataclass
class _DoubleLowVariantConfigBase(CBRotationConfig):
    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 130.0
    max_premium: float = 50.0
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    lot_size: int = 10
    price_weight: float = 0.50
    premium_weight: float = 0.50
    use_cb_value: bool = False
    cb_value_weight: float = 0.0


def _variant_extra_filter(candidate: CandidateInfo, cfg: _DoubleLowVariantConfigBase) -> bool:
    premium = getattr(candidate, "premium_rate", None)
    if premium is not None and premium * 100 > cfg.max_premium:
        return False
    turnover = getattr(candidate, "turnover", None) or 0.0
    return turnover >= cfg.min_turnover


# ── Z-Score 变体 ────────────────────────────────────────

@dataclass
class DoubleLowZScoreConfig(_DoubleLowVariantConfigBase):
    pass


class DoubleLowZScoreStrategy(CBRotationStrategy):
    """双低 Z-Score 标准化"""
    name = "double_low_zscore"

    def __init__(self, config: DoubleLowZScoreConfig | None = None, **kwargs: object):
        super().__init__(config=config or DoubleLowZScoreConfig(), **kwargs)

    @property
    def config(self) -> DoubleLowZScoreConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        return _variant_extra_filter(candidate, self.config)

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        prices = [c.price for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        z_price = _zscore_normalize(prices)
        z_prem = _zscore_normalize(premiums)
        # Z-score 越低越好 → 取负
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = -(cfg.price_weight * z_price[i] + cfg.premium_weight * z_prem[i])
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]


# ── Rank 变体 ───────────────────────────────────────────

@dataclass
class DoubleLowRankConfig(_DoubleLowVariantConfigBase):
    pass


class DoubleLowRankStrategy(CBRotationStrategy):
    """双低 Rank 标准化"""
    name = "double_low_rank"

    def __init__(self, config: DoubleLowRankConfig | None = None, **kwargs: object):
        super().__init__(config=config or DoubleLowRankConfig(), **kwargs)

    @property
    def config(self) -> DoubleLowRankConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        return _variant_extra_filter(candidate, self.config)

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        prices = [c.price for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        r_price = _rank_normalize(prices, ascending=True)   # 低价高分
        r_prem = _rank_normalize(premiums, ascending=True)   # 低溢价高分
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            s = cfg.price_weight * r_price[i] + cfg.premium_weight * r_prem[i]
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]


# ── Percentile 变体 ─────────────────────────────────────

@dataclass
class DoubleLowPercentileConfig(_DoubleLowVariantConfigBase):
    pass


class DoubleLowPercentileStrategy(CBRotationStrategy):
    """双低 Percentile 标准化"""
    name = "double_low_percentile"

    def __init__(self, config: DoubleLowPercentileConfig | None = None, **kwargs: object):
        super().__init__(config=config or DoubleLowPercentileConfig(), **kwargs)

    @property
    def config(self) -> DoubleLowPercentileConfig:
        return self._config  # type: ignore[return-value]

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        return _variant_extra_filter(candidate, self.config)

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        prices = [c.price for c in candidates]
        premiums = [(getattr(c, "premium_rate", 0.0) or 0.0) * 100 for c in candidates]
        p_price = _percentile_normalize(prices)   # 百分位, 越低越好
        p_prem = _percentile_normalize(premiums)
        scored: list[tuple[str, float]] = []
        for i, c in enumerate(candidates):
            # 百分位越低 → (1 - pct) 越高
            s = cfg.price_weight * (1 - p_price[i]) + cfg.premium_weight * (1 - p_prem[i])
            scored.append((c.code, s))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
