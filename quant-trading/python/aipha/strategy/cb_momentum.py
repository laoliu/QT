"""可转债动量策略 — N日涨幅排序轮动"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from aipha.strategy.cb_rotation_base import (
    CBRotationConfig,
    CBRotationStrategy,
    CandidateInfo,
)

if TYPE_CHECKING:
    from aipha.domain.models import Bar
    from aipha.strategy.base import StrategyContext

logger = logging.getLogger(__name__)


@dataclass
class CBMomentumConfig(CBRotationConfig):
    top_k: int = 10
    rebalance_days: int = 5
    max_price: float = 150.0
    lookback_days: int = 20
    min_momentum: float = 0.0
    stop_loss_pct: float = 0.12
    trailing_stop_pct: float = 0.10


class CBMomentumStrategy(CBRotationStrategy):
    """可转债动量轮动 — 按 lookback_days 区间涨幅排序, 买入 top_k"""
    name = "cb_momentum"

    def __init__(self, config: CBMomentumConfig | None = None, **kwargs: object):
        super().__init__(config=config or CBMomentumConfig(), **kwargs)
        self._price_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.config.lookback_days + 5)
        )

    @property
    def config(self) -> CBMomentumConfig:
        return self._config  # type: ignore[return-value]

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            self._price_history[code].append(bar.close)

    def _momentum(self, code: str) -> float | None:
        hist = self._price_history.get(code)
        lb = self.config.lookback_days
        if hist is None or len(hist) < lb:
            return None
        old = hist[-lb]
        if old <= 0:
            return None
        return (hist[-1] - old) / old

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        hist = self._price_history.get(candidate.code if hasattr(candidate, 'code') else candidate.symbol)
        lb = self.config.lookback_days
        if hist is None or len(hist) < lb:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            m = self._momentum(c.code)
            if m is not None:
                scored.append((c.code, m))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
