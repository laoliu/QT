"""A 股高送转预期策略 (High Delivery Expectation)

核心逻辑：预测高送转（每10股送转≥5股），买入前持有 60-120 天。
- A 股"高送转"效应经久不衰（虽然监管趋严，但预期仍有 alpha）
- 用基本面代理预测：高留存比 + 高 EPS + 小盘 + 高股本公积

代理因子（无法直接获取送转公告）：
- 高每股收益（EPS）→ 有送转能力
- 高盈利增速 → 业绩支持送转
- 小市值 → 更倾向送转（扩大股本）
- 低价 → 有"拉低"股价的动机

与 small_cap_quality 的差异：
- small_cap_quality：纯质量选小盘
- **high_delivery_expect**：选"可能送转"的小盘（更偏事件预测）

Alpha 来源（4 因子）：
1. **盈利增速** (30%) — 业绩支持高送转
2. **规模** (30%) — 小盘更倾向送转
3. **质量** (20%) — ROE 底线
4. **低价** (20%) — 低价股更有送转动机（拉低绝对价格）

预期表现：AR 15-25%, MDD 30-40%, Sharpe 0.4-0.7
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_quality_factor,
    compute_size_factor,
)
from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import (
    EquityCandidateInfo,
    EquityRotationConfig,
    EquityRotationStrategy,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class HighDeliveryExpectConfig(EquityRotationConfig):
    """高送转预期配置。"""

    top_k: int = 15
    rebalance_days: int = 20         # 偏长持有
    min_price: float = 3.0
    max_price: float = 80.0          # 低价偏好
    min_turnover: float = 300.0
    min_listed_days: int = 250
    stop_loss_pct: float = 0.22
    trailing_stop_pct: float = 0.18
    buffer_k: int = 5
    max_single_pct: float = 0.08
    min_mv: float = 100_000.0       # 10 亿
    max_mv: float = 1_500_000.0     # 150 亿
    max_industry_pct: float = 0.25

    # 因子权重
    weight_earnings: float = 0.30
    weight_size: float = 0.30
    weight_quality: float = 0.20
    weight_low_price: float = 0.20

    max_pe: float = 80.0
    min_roe: float = 3.0


@StrategyRegistry().register("high_delivery_expect")
class HighDeliveryExpectStrategy(EquityRotationStrategy):
    """高送转预期：盈利增速 + 小盘 + ROE + 低价。"""

    name = "high_delivery_expect"

    def __init__(
        self, config: HighDeliveryExpectConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or HighDeliveryExpectConfig(**kwargs)

    @property
    def config(self) -> HighDeliveryExpectConfig:
        return self._config

    def _compute_low_price_factor(
        self, candidates: list[EquityCandidateInfo],
    ) -> dict[str, float]:
        """低价因子：价格越低分越高（1/price 归一化）。"""
        result: dict[str, float] = {}
        for c in candidates:
            if c.close > 0:
                result[c.symbol] = 1.0 / c.close  # 低价 = 高分
            else:
                result[c.symbol] = float("nan")
        return result

    def _extra_filter(
        self, candidate: EquityCandidateInfo, context: StrategyContext,
    ) -> bool:
        cfg = self._config
        roe = candidate.metadata.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False
        return True

    def _score_candidates(
        self, candidates: list[EquityCandidateInfo], context: StrategyContext,
    ) -> list[str]:
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        f_earn = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_earn, cfg.weight_earnings, scores)

        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        f_lprice = self._compute_low_price_factor(candidates)
        self.rank_normalize(candidates, f_lprice, cfg.weight_low_price, scores)

        return self.rank_sort(scores)
