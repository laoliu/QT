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
        self._cb_value_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=self.config.lookback_days + 5)
        )

    @property
    def config(self) -> CBValueMomentumConfig:
        return self._config  # type: ignore[return-value]

    def _on_start_extra(self) -> None:
        self._price_history.clear()
        self._cb_value_history.clear()

    def _on_bar_update_state(self, bars: dict[str, "Bar"], context: "StrategyContext") -> None:
        for code, bar in bars.items():
            self._price_history[code].append(bar.close)
            cv = getattr(bar, "cb_value", None)
            if cv is None and hasattr(bar, "metadata"):
                cv = bar.metadata.get("cb_value")
            if cv is not None:
                self._cb_value_history[code].append(cv)

    def _extra_filter(self, candidate: CandidateInfo, context: "StrategyContext") -> bool:
        cfg = self.config
        p = getattr(candidate, "cb_over_rate", None)
        if p is None:
            p = getattr(candidate, "premium_rate", None)
            if p is not None: p = p * 100.0
        if p is None: return False
        if p > cfg.max_premium: return False
        # Need enough history for momentum calc
        sym = candidate.code if hasattr(candidate, 'code') else candidate.symbol
        hist = self._cb_value_history.get(sym) if hasattr(self, "_cb_value_history") else None
        if hist is None:
            hist = self._price_history.get(sym)
        if not hist or len(hist) <= cfg.lookback_days:
            return False
        return True

    def _score_candidates(
        self, candidates: list[CandidateInfo], context: "StrategyContext",
    ) -> list[str]:
        cfg = self.config

        scored: list[tuple[str, float]] = []
        for c in candidates:
            # 价值分 (溢价率越低越好)
            p = getattr(c, "cb_over_rate", None)
            if p is None:
                p = getattr(c, "premium_rate", None)
                if p is not None: p = p * 100.0
            premium_pct = (p or 0.0) / 100.0
            value_score = max(0, 1.0 - premium_pct)

            # 动量分 (基于 cb_value 历史)
            hist = self._cb_value_history.get(c.code) if hasattr(self, "_cb_value_history") else None
            if hist is None:
                hist = self._price_history.get(c.code)
            lb = cfg.lookback_days
            momentum_score = 0.0
            if hist and len(hist) > lb:
                old = hist[0]
                if old > 0:
                    momentum_score = (hist[-1] - old) / old
                else:
                    continue  # old=0 → skip to avoid divide-by-zero

            total = cfg.value_weight * value_score + cfg.momentum_weight * momentum_score
            scored.append((c.code, total))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [s[0] for s in scored[:cfg.top_k]]
