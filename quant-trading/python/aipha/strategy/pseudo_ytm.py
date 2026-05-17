"""可转债近似 YTM（到期收益率）策略

Alpha 来源：价格低于面值（100元）的转债具有正到期收益率，
加上嵌入的看涨期权价值被市场忽视。高 YTM 转债通常是被错杀
的信用利差机会。

近似公式（简化，忽略票息）：
    pseudo_ytm ≈ (100 - close) / close / years_to_maturity × 100 (%)

策略逻辑：
1. 过滤：仅保留 close < face_value (100元) 的转债
2. 计算近似 YTM
3. 按 YTM 降序排列，持有最高的 top_k 只
4. 较长调仓周期（10~20 bar），因信号极慢变

优势：
- 纯防守策略，与动量互补
- 信号极稳定，NBO 影响最小
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
    days_to_maturity,
)
from aipha.strategy.registry import StrategyRegistry

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@dataclass
class PseudoYTMConfig(CBRotationConfig):
    """近似 YTM 策略配置"""

    top_k: int = 15
    rebalance_days: int = 10
    max_price: float = 100.0          # 仅买低于面值的
    face_value: float = 100.0         # 面值
    min_years_to_maturity: float = 0.5  # 至少半年到期
    min_turnover: float = 20.0
    stop_loss_pct: float = 0.10


@StrategyRegistry().register("pseudo_ytm")
class PseudoYTMStrategy(CBRotationStrategy):
    """近似到期收益率策略

    买入"到期收益率"最高的低价转债，相当于以低于面值买入
    含免费看涨期权的固收产品。
    """

    name = "pseudo_ytm"

    def __init__(self, config: PseudoYTMConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or PseudoYTMConfig(**kwargs)

    @property
    def config(self) -> PseudoYTMConfig:
        """策略配置。"""
        return self._config

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """仅保留低于面值且到期年限足够的转债。"""
        cfg = self._config
        if candidate.close >= cfg.face_value:
            return False
        if not candidate.maturity_date:
            return False
        years = days_to_maturity(candidate.maturity_date, context.current_time) / 365.25
        if years < cfg.min_years_to_maturity:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按近似 YTM 降序（收益率最高优先），取 top_k。"""
        cfg = self._config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            years = days_to_maturity(c.maturity_date, context.current_time) / 365.25
            if years <= 0:
                continue
            ytm = (cfg.face_value - c.close) / c.close / years * 100.0
            scored.append((c.symbol, ytm))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[: cfg.top_k]]
