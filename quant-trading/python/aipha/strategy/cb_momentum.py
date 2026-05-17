"""可转债动量轮动策略

Alpha 来源：短期价格趋势延续效应（momentum）。
已有大量学术文献证实动量效应在债券与权益类资产中的存在性。
可转债兼具股债特性，动量效应同样显著。

策略逻辑：
1. 计算每只转债过去 lookback_days 个 bar 的收益率
2. 按收益率降序排列
3. 持有 top_k 只最强势转债
4. 定期轮动

风险：追高策略，回撤可能较大；与低价策略天然对冲。

实现说明：
- 策略内部维护价格历史窗口（collections.deque）
- 需要足够的 warmup bars（lookback_days）才会开始交易
"""

from __future__ import annotations

import logging
from collections import deque
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
    from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


@dataclass
class CBMomentumConfig(CBRotationConfig):
    """CB 动量策略配置"""

    top_k: int = 10
    lookback_days: int = 20      # 动量回看窗口
    max_price: float = 150.0     # 动量策略可放宽价格上限
    stop_loss_pct: float = 0.10


@StrategyRegistry().register("cb_momentum")
class CBMomentumStrategy(CBRotationStrategy):
    """可转债动量轮动策略

    持有过去 N 日涨幅最大的 top_k 只转债。
    需要 lookback_days 个 bar 预热。
    """

    name = "cb_momentum"

    def __init__(self, config: CBMomentumConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBMomentumConfig(**kwargs)
        self._price_history: dict[str, deque[float]] = {}

    @property
    def config(self) -> CBMomentumConfig:
        """当前策略配置对象。"""
        return self._config

    def _on_start_extra(self) -> None:
        self._price_history.clear()

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        max_len = self._config.lookback_days + 5
        for symbol, bar in bars.items():
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=max_len)
            self._price_history[symbol].append(bar.close)

    def _should_rebalance(self) -> bool:
        cfg = self._config
        effective = self._bar_count - cfg.lookback_days
        if effective < 1:
            return False
        return (effective - 1) % cfg.rebalance_days == 0

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """需要足够的价格历史。"""
        hist = self._price_history.get(candidate.symbol)
        if hist is None or len(hist) < self._config.lookback_days + 1:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按动量降序（涨幅最大优先），取 top_k。"""
        cfg = self._config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            hist = self._price_history[c.symbol]
            past_price = hist[-cfg.lookback_days]
            if past_price <= 0:
                continue
            momentum = (c.close - past_price) / past_price
            scored.append((c.symbol, momentum))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[: cfg.top_k]]
