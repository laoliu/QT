"""A 股 Piotroski F-Score 增强价值策略

核心逻辑（Piotroski 2000 + A 股实证）：
- **F-Score 作为主排名因子** — 9 维二值评分中取高分（≥6）
- 针对**低 PB 价值股**应用 F-Score → 区分"好便宜"与"价值陷阱"
- 学术实证：F-Score 对小盘价值股年化提升 5-8%（Piotroski 2000）

与其他策略的差异：
- F-Score 是唯一一个以**财务报表变化方向**为信号的因子（非绝对水平）
- 先筛低 PB 便宜货 → 再用 F-Score 选基本面改善的 → 双重 alpha
- 不依赖动量/波动率量价因子，纯基本面驱动

Alpha 来源（3 因子，极简）：
1. **Piotroski F-Score** (45%) — 主因子，盈利/杠杆/效率改善方向
2. **价值 BP** (35%) — 低 PB 前提
3. **应计盈余质量** (20%) — Sloan 异象辅助确认

硬性门槛：
- PB ∈ (0, 4)（先筛低 PB 池）
- PE_TTM ∈ (0, 40)
- ROE > 0%（至少不亏损）
- 流通市值 ≥ 30 亿

预期表现：AR 10-16%, MDD 18-28%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_accruals_quality_factor,
    compute_piotroski_factor,
    compute_value_factor,
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
class FScoreValueConfig(EquityRotationConfig):
    """Piotroski F-Score 增强价值配置。"""

    top_k: int = 30
    rebalance_days: int = 20          # 月度调仓
    min_price: float = 3.0
    max_price: float = 200.0
    min_turnover: float = 800.0
    min_listed_days: int = 250        # 满1年（需要年报变化数据）
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 8
    max_single_pct: float = 0.05
    min_mv: float = 300_000.0        # 30 亿以上
    max_mv: float = float("inf")
    max_industry_pct: float = 0.25

    # 因子权重（3 因子，极简设计）
    weight_fscore: float = 0.45
    weight_value_bp: float = 0.35
    weight_accruals: float = 0.20

    # 硬性过滤（低 PB 价值池）
    max_pb: float = 4.0
    max_pe: float = 40.0
    min_roe: float = 0.0             # 至少不亏损

    # F-Score 参数
    piotroski_min_dims: int = 4      # 至少 4 个维度可计算


@StrategyRegistry().register("fscore_value_enhanced")
class FScoreValueEnhancedStrategy(EquityRotationStrategy):
    """Piotroski F-Score 增强价值策略。

    先筛低 PB 价值股，再用 F-Score 选基本面改善方向的标的。
    """

    name = "fscore_value_enhanced"

    def __init__(
        self, config: FScoreValueConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or FScoreValueConfig(**kwargs)

    @property
    def config(self) -> FScoreValueConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """筛低 PB 价值池。"""
        cfg = self._config
        meta = candidate.metadata

        # PB 范围（核心：必须便宜）
        pb = meta.get("pb")
        if pb is not None and (pb <= 0 or pb > cfg.max_pb):
            return False

        # PE 范围
        pe = meta.get("pe_ttm")
        if pe is not None and (pe <= 0 or pe > cfg.max_pe):
            return False

        # ROE 底线（排除亏损）
        roe = meta.get("roe")
        if roe is not None and roe < cfg.min_roe:
            return False

        return True

    def _score_candidates(
        self,
        candidates: list[EquityCandidateInfo],
        context: StrategyContext,
    ) -> list[str]:
        """3 因子打分：F-Score + BP价值 + 应计质量。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. Piotroski F-Score（主因子）
        f_pio = compute_piotroski_factor(
            candidates, min_dimensions=cfg.piotroski_min_dims,
        )
        self.rank_normalize(candidates, f_pio, cfg.weight_fscore, scores)

        # 2. 价值因子（低 PB）
        f_bp = compute_value_factor(candidates, metric="pb")
        self.rank_normalize(candidates, f_bp, cfg.weight_value_bp, scores)

        # 3. 应计盈余质量
        f_acc = compute_accruals_quality_factor(candidates)
        self.rank_normalize(candidates, f_acc, cfg.weight_accruals, scores)

        return self.rank_sort(scores)
