"""可转债均值回归策略（布林带 + 面值锚定）

Alpha 来源：
1. 可转债天然有面值锚定（100元），价格偏离均值后有回归倾向
2. 低价区间下行空间有限（债底保护），但上行弹性完整
3. 布林带下轨买入 = 均值回归 + 过度悲观修正

策略逻辑：
1. 内部维护每只转债的 N 日价格历史（MA + std）
2. 计算布林带: lower = MA - k*std
3. 当价格 < lower → 超卖信号 → 买入候选
4. 按 (lower - close) / std 降序排列（偏离越大越优先）
5. 等权持有 top_k 只
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
class CBMeanReversionConfig(CBRotationConfig):
    """CB均值回归策略配置"""

    top_k: int = 15
    rebalance_days: int = 3
    lookback: int = 20
    bb_k: float = 2.0          # 布林带倍数


@StrategyRegistry().register("cb_mean_reversion")
class CBMeanReversionStrategy(CBRotationStrategy):
    """可转债均值回归策略

    在波动率收窄后价格跌破布林带下轨时买入超卖转债。
    按偏离程度降序排列，持有偏离最大的 top_k 只。
    """

    name = "cb_mean_reversion"

    def __init__(self, config: CBMeanReversionConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBMeanReversionConfig(**kwargs)
        self._price_history: dict[str, deque] = {}

    @property
    def config(self) -> CBMeanReversionConfig:
        """当前策略配置对象。"""
        return self._config

    def _on_start_extra(self) -> None:
        self._price_history.clear()

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        for symbol, bar in bars.items():
            if bar.close > 0:
                if symbol not in self._price_history:
                    self._price_history[symbol] = deque(maxlen=self._config.lookback)
                self._price_history[symbol].append(bar.close)

    def _should_rebalance(self) -> bool:
        if self._bar_count < self._config.lookback:
            return False
        return (self._bar_count - 1) % self._config.rebalance_days == 0

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """需要足够历史，且价格低于布林带下轨。"""
        cfg = self._config
        hist = self._price_history.get(candidate.symbol)
        if hist is None or len(hist) < cfg.lookback:
            return False
        prices = list(hist)
        ma = sum(prices) / len(prices)
        variance = sum((p - ma) ** 2 for p in prices) / len(prices)
        std = variance ** 0.5
        if std < 0.01:
            return False
        lower = ma - cfg.bb_k * std
        return candidate.close < lower

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按偏离程度降序（最超卖优先），取 top_k。"""
        cfg = self._config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            prices = list(self._price_history[c.symbol])
            ma = sum(prices) / len(prices)
            variance = sum((p - ma) ** 2 for p in prices) / len(prices)
            std = variance ** 0.5
            lower = ma - cfg.bb_k * std
            deviation = (lower - c.close) / std
            scored.append((c.symbol, deviation))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[: cfg.top_k]]
