"""A 股盈利复合策略

核心逻辑：Novy-Marx 2013 的 Gross Profitability + ROE + Accruals 三维盈利质量。

学术基础：
- Novy-Marx (2013): Gross Profit/Assets 是比 ROE 更纯净的盈利信号
- Sloan (1996): 低应计 = 盈利质量高
- 三者组合在 A 股 IC 约 5-8%（华泰金工实证）

与其他策略的差异：
- **三个盈利维度**：GP + ROE + Accruals，不用估值/动量/波动率
- **纯基本面驱动**：完全不依赖量价因子
- 适合与动量/量价策略形成正交组合

Alpha 来源（3 因子）：
1. **Gross Profitability** (40%) — 毛利/总资产
2. **ROE** (35%) — 净利润/净资产
3. **应计质量** (25%) — (CFO - Net Income) / Total Assets

预期表现：AR 10-15%, MDD 22-30%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_quality_factor,
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
class ProfitabilityCompositeConfig(EquityRotationConfig):
    """盈利复合策略配置。"""

    top_k: int = 30
    rebalance_days: int = 20          # 月度
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 800.0
    min_listed_days: int = 250
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 10
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 因子权重（纯盈利质量，3因子）
    weight_gross_profit: float = 0.40
    weight_roe: float = 0.35
    weight_accruals: float = 0.25

    # 宽松过滤
    max_pe: float = 80.0
    momentum_lookback: int = 20


@StrategyRegistry().register("profitability_composite")
class ProfitabilityCompositeStrategy(EquityRotationStrategy):
    """盈利复合策略：GP + ROE + 应计质量三维度。"""

    name = "profitability_composite"

    def __init__(
        self, config: ProfitabilityCompositeConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or ProfitabilityCompositeConfig(**kwargs)

    @property
    def config(self) -> ProfitabilityCompositeConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        pe = candidate.metadata.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > self._config.max_pe):
            return False
        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """3 因子打分：GP + ROE + 应计质量。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. Gross Profitability（毛利率作为代理）
        # nan_handling="median": 缺失 fina 数据的股票给中位排名分，避免 NaN 污染
        f_gp = compute_quality_factor(
            candidates, metric="grossprofit_margin",
        )
        self.rank_normalize(
            candidates, f_gp, cfg.weight_gross_profit, scores,
            nan_handling="median",
        )

        # 2. ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(
            candidates, f_roe, cfg.weight_roe, scores,
            nan_handling="median",
        )

        # 3. 应计盈余质量
        f_acc = compute_accruals_quality_factor(candidates)
        self.rank_normalize(
            candidates, f_acc, cfg.weight_accruals, scores,
            nan_handling="median",
        )

        return self.rank_sort(scores)
