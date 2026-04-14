"""可转债价值动量复合策略"""
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
class CBValueMomentumConfig(CBRotationConfig):
    top_k: int = 10
    lookback_days: int = 20
    max_premium: float = 30.0
    max_price: float = 150.0
    stop_loss_pct: float = 0.10
    rebalance_days: int = 5
    min_turnover: float = 20.0
    value_weight: float = 0.5
    momentum_weight: float = 0.5


class CBValueMomentumStrategy(CBRotationStrategy):
    """价值动量复合 — 低溢价率 (价值) + 近期涨幅 (动量) 加权"""
    name = "cb_value_momentum"

    def __init__(self, config: CBValueMomentumConfig | None = None, **kwargs: object):
        super().__init__(config=config or CBValueMomentumConfig(), **kwargs)
        self._price_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.config.lookback_days + 5)
        )

    @property
    def config(self) -> CBValueMomentumConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        self._price_history.clear()

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            self._price_history[code].append(bar.close)

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        premium = getattr(candidate, "premium_rate", None)
        if premium is not None and premium * 100 > cfg.max_premium:
            return False
        turnover = getattr(candidate, "turnover", None) or 0.0
        return turnover >= cfg.min_turnover

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config

        scored: list[tuple[str, float]] = []
        for c in candidates:
            # 价值分 (溢价率越低越好)
            premium = (getattr(c, "premium_rate", 0.0) or 0.0)
            value_score = max(0, 1.0 - premium)

            # 动量分
            hist = self._price_history.get(c.code)
            lb = cfg.lookback_days
            momentum_score = 0.0
            if hist and len(hist) >= lb:
                old = hist[-lb]
                if old > 0:
                    momentum_score = (hist[-1] - old) / old

            total = cfg.value_weight * value_score + cfg.momentum_weight * momentum_score
            scored.append((c.code, total))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
