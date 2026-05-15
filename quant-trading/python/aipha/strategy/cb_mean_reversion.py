"""可转债均值回归策略 — 布林带偏离度排序"""
from __future__ import annotations

import logging
import math
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
class CBMeanReversionConfig(CBRotationConfig):
    top_k: int = 20
    rebalance_days: int = 5
    max_price: float = 140.0
    lookback: int = 20
    bb_k: float = 2.0
    min_deviation: float = -2.0
    stop_loss_pct: float = 0.10
    trailing_stop_pct: float = 0.08


class CBMeanReversionStrategy(CBRotationStrategy):
    """可转债均值回归 — 低于布林带下轨越多, 得分越高"""
    name = "cb_mean_reversion"

    def __init__(self, config: CBMeanReversionConfig | None = None, **kwargs: object):
        super().__init__(config=config or CBMeanReversionConfig(), **kwargs)
        self._price_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.config.lookback + 10)
        )

    @property
    def config(self) -> CBMeanReversionConfig:
        return self._config  # type: ignore[return-value]

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            self._price_history[code].append(bar.close)

    def _bb_deviation(self, code: str) -> float | None:
        hist = self._price_history.get(code)
        lb = self.config.lookback
        if hist is None or len(hist) < lb:
            return None
        prices = list(hist)[-lb:]
        mean = sum(prices) / lb
        std = math.sqrt(sum((p - mean) ** 2 for p in prices) / lb)
        if std < 1e-9:
            return None
        return (prices[-1] - mean) / std  # z-score

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config
        scored: list[tuple[str, float]] = []
        for c in candidates:
            z = self._bb_deviation(c.symbol)
            if z is not None and z <= cfg.min_deviation:
                # 越低越好 → 取负值做降序
                scored.append((c.symbol, -z))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
