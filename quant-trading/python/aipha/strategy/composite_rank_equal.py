"""A 股复合等权策略

核心逻辑：最简单粗暴的多因子 — 6 因子等权排名。

学术基础：
- A 股实证表明等权因子在多数年份优于复杂加权（广发金工 2019）
- 等权的优势：无需历史数据做权重估计 → 无过拟合
- 6 因子覆盖价值/质量/动量/低波/规模/换手率，最大化正交性

与其他策略的差异：
- **零参数优化**：不做因子权重调整，全等权（1/6）
- **40 只持仓**：更分散，降低个股风险
- **最强基准线**：任何策略都应跑赢此等权基准

Alpha 来源（6 因子等权）：
1. **价值 EP** (1/6)
2. **质量 ROE** (1/6)
3. **动量 60d** (1/6)
4. **低波动** (1/6)
5. **规模** (1/6)
6. **低换手率** (1/6)

预期表现：AR 10-15%, MDD 20-28%, Sharpe 0.5-0.8
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.factors.equity_factors import (
    compute_momentum_factor,
    compute_quality_factor,
    compute_size_factor,
    compute_turnover_factor,
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

_EQUAL_WEIGHT = 1.0 / 6.0


@dataclass
class CompositeRankEqualConfig(EquityRotationConfig):
    """复合等权配置。"""

    top_k: int = 40                   # 40 持仓，最分散
    rebalance_days: int = 20          # 月度
    min_price: float = 3.0
    max_price: float = 300.0
    min_turnover: float = 800.0
    min_listed_days: int = 120
    stop_loss_pct: float = 0.15
    trailing_stop_pct: float = 0.12
    buffer_k: int = 12
    max_single_pct: float = 0.04     # 1/40 ≈ 2.5%，留余量
    min_mv: float = 300_000.0
    max_mv: float = float("inf")
    max_industry_pct: float = 0.20

    # 宽松过滤
    max_pe: float = 100.0

    # 因子参数
    momentum_lookback: int = 60
    momentum_skip_recent: int = 5
    volatility_lookback: int = 60


@StrategyRegistry().register("composite_rank_equal")
class CompositeRankEqualStrategy(EquityRotationStrategy):
    """复合等权策略：6 因子等权排名，零参数优化。"""

    name = "composite_rank_equal"

    def __init__(
        self, config: CompositeRankEqualConfig | None = None, **kwargs,
    ):
        super().__init__()
        self._config = config or CompositeRankEqualConfig(**kwargs)

    @property
    def config(self) -> CompositeRankEqualConfig:
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
        """6 因子等权排名。"""
        cfg = self._config
        if not candidates:
            return []

        scores: dict[str, float] = {}

        # 1. 价值 (EP)
        f_v = compute_value_factor(candidates, metric="pe_ttm")
        self.rank_normalize(candidates, f_v, _EQUAL_WEIGHT, scores)

        # 2. 质量 (ROE)
        f_q = compute_quality_factor(candidates, metric="roe")
        self.rank_normalize(candidates, f_q, _EQUAL_WEIGHT, scores)

        # 3. 动量 (60d)
        f_m = compute_momentum_factor(
            self._price_history, candidates,
            lookback=cfg.momentum_lookback,
            skip_recent=cfg.momentum_skip_recent,
        )
        self.rank_normalize(candidates, f_m, _EQUAL_WEIGHT, scores)

        # 4. 低波动 (60d)
        f_vol = compute_volatility_factor(
            self._price_history, candidates,
            lookback=cfg.volatility_lookback,
        )
        self.rank_normalize(candidates, f_vol, _EQUAL_WEIGHT, scores)

        # 5. 规模 (小市值优先)
        f_s = compute_size_factor(candidates)
        self.rank_normalize(candidates, f_s, _EQUAL_WEIGHT, scores)

        # 6. 低换手率
        f_t = compute_turnover_factor(candidates)
        self.rank_normalize(candidates, f_t, _EQUAL_WEIGHT, scores)

        return self.rank_sort(scores)
