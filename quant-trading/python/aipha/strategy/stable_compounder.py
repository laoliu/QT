"""A 股稳健复利策略

核心逻辑：以**盈利稳定性**和**ROE 一致性**为核心选股标准。

与其他防御策略的差异：
- 不看盈利绝对水平 → 看盈利**波动性**（稳>高）
- 不看短期动量 → 看**长期盈利趋势一致性**
- 典型标的：消费龙头、公用事业、医药白马
- "慢而稳"的复利机器 vs 高波动高收益

Alpha 来源（4 因子）：
1. **盈利稳定性** (35%) — 营收增长率 CV 的倒数
2. **质量 ROE** (30%) — 高且稳定的 ROE
3. **低波动** (20%) — 股价波动率低
4. **价值确认** (15%) — 不追高估值

硬性门槛：
- ROE ≥ 8%（连续盈利）
- PE_TTM ∈ (0, 40)
- 流通市值 ≥ 100 亿（大盘稳定标的）
- 上市满 3 年（足够长的财务历史判断稳定性）

预期表现：AR 8-12%, MDD 12-18%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_earnings_stability_factor,
    compute_quality_factor,
    compute_value_factor,
    compute_volatility_factor,
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
class StableCompounderConfig(EquityRotationConfig):
    """稳健复利策略配置。"""

    top_k: int = 25
    rebalance_days: int = 20          # 月度调仓
    min_price: float = 5.0
    max_price: float = 500.0
    min_turnover: float = 2000.0
    min_listed_days: int = 750        # 满3年（需要足够长盈利历史）
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10
    buffer_k: int = 10
    max_single_pct: float = 0.06
    min_mv: float = 1_000_000.0      # 100 亿以上
    max_mv: float = float("inf")
    inverse_vol_weighting: bool = True
    max_industry_pct: float = 0.20

    # 因子权重
    weight_stability: float = 0.35
    weight_quality: float = 0.30
    weight_volatility: float = 0.20
    weight_value: float = 0.15

    # 硬性过滤
    min_roe: float = 8.0
    max_pe: float = 40.0

    # 盈利稳定性参数
    stability_min_periods: int = 4    # 至少 4 个报告期
    volatility_lookback: int = 60
    momentum_lookback: int = 20


@StrategyRegistry().register("stable_compounder")
class StableCompounderStrategy(EquityRotationStrategy):
    """A 股稳健复利策略。

    专选盈利稳定、ROE 一致的"慢公司"，以低波动换取长期复利。
    """

    name = "stable_compounder"

    def __init__(
        self, config: StableCompounderConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or StableCompounderConfig(**kwargs)

    @property
    def config(self) -> StableCompounderConfig:
        return self._config

    def _extra_filter(
        self,
        candidate: EquityCandidateInfo,
        context: StrategyContext,
    ) -> bool:
        """稳健复利硬性过滤。"""
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
        """4 因子打分：盈利稳定性 + ROE + 低波动 + 价值。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 盈利稳定性（主因子）
        f_stab = compute_earnings_stability_factor(
            candidates, min_periods=cfg.stability_min_periods,
        )
        self.rank_normalize(candidates, f_stab, cfg.weight_stability, scores)

        # 2. 质量 ROE
        f_roe = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_roe, cfg.weight_quality, scores)

        # 3. 低波动
        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_vol, cfg.weight_volatility, scores)

        # 4. 价值确认
        f_value = compute_value_factor(candidates, metric="pe_ttm")
        self.rank_normalize(candidates, f_value, cfg.weight_value, scores)

        return self.rank_sort(scores)
