"""可转债低价摊大饼策略

Alpha 来源：低价转债接近债底（面值100元），下行空间有限，
同时保留正股反弹时的向上弹性。"摊大饼"即广泛分散持有，
降低个券黑天鹅风险。

策略逻辑：
1. 过滤基本条件（流动性、发行规模、到期时间）
2. 按转债价格升序排列
3. 持有最便宜的 top_k 只
4. 每 rebalance_days 个 bar 轮动

优势：
- 信号极度稳定（价格排序变化慢），对成交模型最不敏感
- 理论基础扎实：债底保护 + 免费看涨期权
- 分散持仓降低单券风险
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
class LowPriceConfig(CBRotationConfig):
    """低价摊大饼策略配置"""

    top_k: int = 30          # 分散持有数量（摊大饼核心）
    rebalance_days: int = 5
    max_price: float = 115.0  # 低价聚焦
    min_turnover: float = 20.0
    min_days_to_maturity: int = 30
    exclude_st: bool = True
    stop_loss_pct: float = 0.10
    lot_size: int = 10


@StrategyRegistry().register("low_price")
class LowPriceStrategy(CBRotationStrategy):
    """低价摊大饼策略

    持有全市场最便宜的 N 只可转债，等权分配，定期轮动。
    核心思想：越便宜的转债债底保护越强，下行有限、上行有弹性。
    """

    name = "low_price"

    def __init__(self, config: LowPriceConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or LowPriceConfig(**kwargs)

    @property
    def config(self) -> LowPriceConfig:
        """策略配置。"""
        return self._config

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按价格升序（最便宜优先），取 top_k。"""
        candidates.sort(key=lambda c: c.close)
        return [c.symbol for c in candidates[: self._config.top_k]]
