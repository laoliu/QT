"""荐股引擎 — 聚合多源信号生成推荐列表。

三档时间维度: SHORT(1-5d) / MEDIUM(1-3m) / LONG(3m+),
各维度使用不同权重（见 §4.3 信号聚合权重体系）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aipha.advisory.aggregator import AggregatedSignal, SignalAggregator, SignalWeight
from aipha.advisory.models import (
    ActionType,
    Recommendation,
    ScreenerResult,
    TimeHorizon,
)

logger = logging.getLogger(__name__)

# 按 §4.3 短线/中线/长线权重差异表
HORIZON_WEIGHTS: dict[TimeHorizon, SignalWeight] = {
    TimeHorizon.SHORT: SignalWeight(quant=0.25, technical=0.25, intel=0.35, target_price=0.15),
    TimeHorizon.MEDIUM: SignalWeight(quant=0.35, technical=0.15, intel=0.30, target_price=0.20),
    TimeHorizon.LONG: SignalWeight(quant=0.40, technical=0.05, intel=0.20, target_price=0.35),
}

# 建议有效期
EXPIRY_DAYS: dict[TimeHorizon, int] = {
    TimeHorizon.SHORT: 3,
    TimeHorizon.MEDIUM: 30,
    TimeHorizon.LONG: 90,
}


@dataclass
class RecommendationConfig:
    top_n: int = 10
    min_confidence: float = 0.4
    min_source_count: int = 2
    horizons: list[TimeHorizon] = field(
        default_factory=lambda: [TimeHorizon.SHORT, TimeHorizon.MEDIUM],
    )


class RecommendationEngine:
    """荐股引擎 — 核心入口。"""

    def __init__(self, config: RecommendationConfig | None = None) -> None:
        self._config = config or RecommendationConfig()
        self._aggregators: dict[TimeHorizon, SignalAggregator] = {
            h: SignalAggregator(weights=HORIZON_WEIGHTS.get(h))
            for h in TimeHorizon
        }

    def generate(
        self,
        screener_results: dict[str, list[ScreenerResult]],
        intel_scores: dict[str, float] | None = None,
        target_prices: dict[str, float] | None = None,
        current_prices: dict[str, float] | None = None,
    ) -> dict[TimeHorizon, list[Recommendation]]:
        """为每个时间维度生成推荐列表。"""
        result: dict[TimeHorizon, list[Recommendation]] = {}
        for horizon in self._config.horizons:
            agg = self._aggregators[horizon]
            signals = agg.aggregate(
                screener_results, intel_scores, target_prices, current_prices,
            )
            recs = self._signals_to_recs(signals, horizon)
            result[horizon] = recs
            logger.info(
                "horizon=%s generated %d recommendations", horizon.value, len(recs),
            )
        return result

    def _signals_to_recs(
        self,
        signals: list[AggregatedSignal],
        horizon: TimeHorizon,
    ) -> list[Recommendation]:
        cfg = self._config
        now = datetime.now()
        expiry = now + timedelta(days=EXPIRY_DAYS.get(horizon, 7))
        recs: list[Recommendation] = []
        for sig in signals[: cfg.top_n]:
            if sig.source_count < cfg.min_source_count:
                continue
            action = self._score_to_action(sig.composite_score)
            conf_val = min(sig.composite_score / 100, 1.0)
            if conf_val < cfg.min_confidence:
                continue
            recs.append(Recommendation(
                ts_code=sig.ts_code,
                name=sig.name,
                action=action,
                horizon=horizon,
                confidence=conf_val,
                reasons=list(sig.factors.keys()),
                signal_sources=sig.source_scores,
                created_at=now,
                expires_at=expiry,
            ))
        return recs

    @staticmethod
    def _score_to_action(score: float) -> ActionType:
        if score >= 80:
            return ActionType.STRONG_BUY
        if score >= 60:
            return ActionType.BUY
        if score >= 40:
            return ActionType.HOLD
        if score >= 25:
            return ActionType.REDUCE
        return ActionType.SELL
