"""可转债低溢价率轮动策略

Alpha 来源：低溢价率意味着转债价格接近转股价值（Delta≈1），
正股任何上涨都能几乎等比例体现在转债上，同时保留债底保护。
这类转债是"股性最强"的转债。

策略逻辑：
1. 过滤基本条件（含溢价率上限）
2. 按 cb_over_rate 升序排列
3. 持有溢价率最低的 top_k 只
4. 定期轮动
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class LowPremiumConfig(CBRotationConfig):
    """低溢价率轮动配置"""

    top_k: int = 15
    rebalance_days: int = 5
    max_price: float = 150.0         # 低溢价转债价格可能偏高
    max_premium: float = 50.0        # 初步筛选上限
    min_turnover: float = 30.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    lot_size: int = 10


@StrategyRegistry().register("low_premium")
class LowPremiumStrategy(CBRotationStrategy):
    """低溢价率轮动策略

    持有溢价率最低的 top_k 只转债，追求最大正股弹性。
    """

    name = "low_premium"

    def __init__(self, config: LowPremiumConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or LowPremiumConfig(**kwargs)

    @property
    def config(self) -> LowPremiumConfig:
        """策略配置。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """溢价率上限过滤。"""
        if candidate.cb_over_rate is None:
            return False
        return candidate.cb_over_rate <= self._config.max_premium

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按溢价率升序，取 top_k。"""
        # 只保留有溢价率数据的
        valid = [c for c in candidates if c.cb_over_rate is not None]
        valid.sort(key=lambda c: c.cb_over_rate)  # type: ignore[arg-type]
        return [c.symbol for c in valid[: self._config.top_k]]
