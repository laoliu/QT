"""A 股小盘质量策略

核心逻辑：A 股最强异象 — 小市值溢价 + 质量过滤去壳值/垃圾股。

学术基础：
- 小市值溢价在 A 股年化 alpha 10-20%（华泰金工）
- 但纯小盘包含大量壳资源/ST/亏损股 → 质量过滤是关键
- 目标：20-100 亿中小盘 + ROE > 0 + 正增长 → "好小盘"

与 smallcap_rotation 的差异：
- 质量门槛更严（ROE > 3% + 利润正增长 + 非 ST）
- 市值区间更精确（20-100亿 vs 不限）
- 加入盈利加速因子（不只看大小）

Alpha 来源（4 因子）：
1. **规模因子** (30%) — 小市值优先
2. **质量 ROE** (25%) — 排除垃圾小盘
3. **盈利加速** (25%) — 有业绩支撑的小盘
4. **动量** (20%) — 趋势确认

硬性门槛：
- 流通市值 ∈ [20亿, 100亿]
- ROE > 3%（排除亏损和微利）
- PE_TTM ∈ (0, 60)

预期表现：AR 18-30%, MDD 30-45%, Sharpe 0.5-0.8
容量警告：小盘策略容量有限，参与率上限 5%
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_growth_factor,
    compute_momentum_factor,
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
class SmallCapQualityConfig(EquityRotationConfig):
    """小盘质量策略配置。"""

    top_k: int = 40                  # 40 只分散风险
    rebalance_days: int = 10         # 10 天调仓（小盘更快轮动）
    min_price: float = 3.0
    max_price: float = 100.0
    min_turnover: float = 500.0      # 小盘成交额低
    min_listed_days: int = 120
    stop_loss_pct: float = 0.18      # 小盘波动大，止损稍宽
    trailing_stop_pct: float = 0.15
    buffer_k: int = 12
    max_single_pct: float = 0.04     # 单票 4%，更分散
    min_mv: float = 200_000.0        # 20 亿
    max_mv: float = 1_000_000.0      # 100 亿
    max_industry_pct: float = 0.20

    # 因子权重
    weight_size: float = 0.30
    weight_quality: float = 0.25
    weight_growth: float = 0.25
    weight_momentum: float = 0.20

    # 硬性过滤
    min_roe: float = 3.0
    max_pe: float = 60.0

    # 因子参数
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    momentum_multi_period: bool = True


@StrategyRegistry().register("small_cap_quality")
class SmallCapQualityStrategy(EquityRotationStrategy):
    """小盘质量策略：20-100亿中小盘 + 质量/成长过滤。"""

    name = "small_cap_quality"

    def __init__(
        self, config: SmallCapQualityConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or SmallCapQualityConfig(**kwargs)

    @property
    def config(self) -> SmallCapQualityConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """小盘质量硬性过滤。"""
        cfg = self._config
        meta = candidate.metadata

        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        pe = meta.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False

        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """4 因子打分：规模 + 质量 + 盈利加速 + 动量。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 规模因子（小市值优先）
        f_size = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_size, cfg.weight_size, scores)

        # 2. 质量 ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        # 3. 盈利加速
        f_growth = compute_earnings_growth_factor(candidates)
        self.rank_normalize(candidates, f_growth, cfg.weight_growth, scores)

        # 4. 动量确认
        f_mom = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
            multi_period=cfg.momentum_multi_period,
        )
        self.rank_normalize(candidates, f_mom, cfg.weight_momentum, scores)

        return self.rank_sort(scores)
