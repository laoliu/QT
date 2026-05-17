"""可转债转股价值动量策略

Alpha 来源：
1. 原 cb_momentum（价格动量 -8.78%）使用转债价格变化 → 受债底干扰
   低价区转债价格受债性抑制，涨幅受限，"假动量"多
2. 转股价值（cb_value）动量更纯粹反映正股驱动力
3. cb_value 上升 = 正股走强 → 转债跟涨概率大
4. 叠加低溢价率过滤确保转债能有效跟随正股

策略逻辑：
1. 维护每只转债的 cb_value 历史窗口（deque）
2. 计算 lookback_days 期间 cb_value 变化率
3. 筛选溢价率 < max_premium（确保股性充足）
4. 按 cb_value 动量降序排列，持有 top_k 只
5. 需要 warmup（lookback_days 个 bar 后才交易）
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
class CBValueMomentumConfig(CBRotationConfig):
    """转股价值动量策略配置"""

    top_k: int = 10
    lookback_days: int = 20

    # 溢价率上限（确保转债有股性，能跟随正股）
    max_premium: float = 30.0
    max_price: float = 150.0
    stop_loss_pct: float = 0.10


@StrategyRegistry().register("cb_value_momentum")
class CBValueMomentumStrategy(CBRotationStrategy):
    """转股价值动量策略

    追踪正股驱动最强的转债：cb_value 上涨最快 + 低溢价率。
    比纯价格动量更纯粹，避免"低价债假动量"问题。
    """

    name = "cb_value_momentum"

    def __init__(self, config: CBValueMomentumConfig | None = None, **kwargs):
        super().__init__()
        self._config = config or CBValueMomentumConfig(**kwargs)
        self._cb_value_history: dict[str, deque[float]] = {}

    @property
    def config(self) -> CBValueMomentumConfig:
        """策略配置。"""
        return self._config

    def _on_start_extra(self) -> None:
        self._cb_value_history.clear()

    def _on_bar_update_state(
        self, bars: dict[str, Bar], context: StrategyContext,
    ) -> None:
        max_len = self._config.lookback_days + 1
        for symbol, bar in bars.items():
            meta = bar.metadata or {}
            cv = meta.get("cb_value")
            if cv is not None and cv > 0:
                if symbol not in self._cb_value_history:
                    self._cb_value_history[symbol] = deque(maxlen=max_len)
                self._cb_value_history[symbol].append(cv)

    def _extra_filter(self, candidate: CandidateInfo, context: StrategyContext) -> bool:
        """需要足够的 cb_value 历史，且溢价率低于上限。"""
        cfg = self._config
        if candidate.cb_over_rate is None or candidate.cb_over_rate > cfg.max_premium:
            return False
        hist = self._cb_value_history.get(candidate.symbol)
        if not hist or len(hist) < cfg.lookback_days + 1:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: StrategyContext,
    ) -> list[str]:
        """按 cb_value 动量降序（最强优先），取 top_k。"""
        cfg = self._config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            hist = self._cb_value_history[c.symbol]
            old_cv = hist[0]
            new_cv = hist[-1]
            if old_cv <= 0:
                continue
            momentum = (new_cv - old_cv) / old_cv
            scored.append((c.symbol, momentum))
        scored.sort(key=lambda x: (-x[1], x[0]))
        return [s for s, _ in scored[: cfg.top_k]]
