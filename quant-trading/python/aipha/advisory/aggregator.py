"""信号聚合器。

汇聚多源信号（量化因子/技术指标/情报/目标价），
加权计算综合置信度与推荐动作。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aipha.advisory.models import (
    ActionType,
    ConfidenceLevel,
    Recommendation,
    ScreenerResult,
    TimeHorizon,
)


@dataclass
class SignalWeight:
    """信号源权重配置。"""
    quant: float = 0.35
    technical: float = 0.15
    intel: float = 0.30
    target_price: float = 0.20


@dataclass
class AggregatedSignal:
    """聚合后的信号。"""
    ts_code: str
    name: str = ""
    composite_score: float = 0.0
    source_scores: dict[str, float] = field(default_factory=dict)
    source_count: int = 0
    factors: dict[str, Any] = field(default_factory=dict)


class SignalAggregator:
    """多源信号聚合引擎。

    按架构规范加权:
      Quant 30-40%, Intel 25-35%, Technical 10-15%, Target Price 15-20%
    """

    def __init__(self, weights: SignalWeight | None = None) -> None:
        self._weights = weights or SignalWeight()

    def aggregate(
        self,
        screener_results: dict[str, list[ScreenerResult]],
        intel_scores: dict[str, float] | None = None,
        target_prices: dict[str, float] | None = None,
        current_prices: dict[str, float] | None = None,
    ) -> list[AggregatedSignal]:
        """聚合多源信号。"""
        intel_scores = intel_scores or {}
        target_prices = target_prices or {}
        current_prices = current_prices or {}

        code_signals: dict[str, AggregatedSignal] = {}

        # 1. 量化/基本面筛选结果
        for sname, results in screener_results.items():
            is_tech = sname in ("technical", "volume_price")
            for r in results:
                sig = code_signals.setdefault(
                    r.ts_code,
                    AggregatedSignal(ts_code=r.ts_code, name=r.name),
                )
                key = "technical" if is_tech else "quant"
                existing = sig.source_scores.get(key, 0.0)
                sig.source_scores[key] = max(existing, r.score)
                sig.factors[sname] = r.factors
                sig.source_count += 1

        # 2. 情报信号
        for code, iscore in intel_scores.items():
            sig = code_signals.setdefault(
                code, AggregatedSignal(ts_code=code)
            )
            sig.source_scores["intel"] = iscore * 100
            sig.source_count += 1

        # 3. 目标价信号
        for code, tp in target_prices.items():
            cp = current_prices.get(code, 0)
            if cp > 0:
                upside = (tp / cp - 1) * 100
                sig = code_signals.setdefault(
                    code, AggregatedSignal(ts_code=code)
                )
                sig.source_scores["target_price"] = min(
                    max(upside, 0), 100
                )
                sig.factors["target_price"] = tp
                sig.factors["upside_pct"] = round(upside, 2)
                sig.source_count += 1

        # 4. 加权合成
        w = self._weights
        weight_map = {
            "quant": w.quant,
            "technical": w.technical,
            "intel": w.intel,
            "target_price": w.target_price,
        }
        for sig in code_signals.values():
            total_w = 0.0
            weighted_sum = 0.0
            for src, wt in weight_map.items():
                if src in sig.source_scores:
                    weighted_sum += sig.source_scores[src] * wt
                    total_w += wt
            sig.composite_score = (
                weighted_sum / total_w if total_w > 0 else 0.0
            )

        return sorted(
            code_signals.values(),
            key=lambda s: s.composite_score,
            reverse=True,
        )

    def to_recommendations(
        self,
        signals: list[AggregatedSignal],
        top_n: int = 10,
        horizon: TimeHorizon = TimeHorizon.SHORT,
    ) -> list[Recommendation]:
        """将聚合信号转为推荐列表。"""
        recs: list[Recommendation] = []
        for sig in signals[:top_n]:
            action = self._score_to_action(sig.composite_score)
            confidence = self._score_to_confidence(
                sig.composite_score, sig.source_count
            )
            recs.append(Recommendation(
                ts_code=sig.ts_code,
                name=sig.name,
                action=action,
                horizon=horizon,
                confidence=confidence,
                reasons=list(sig.factors.keys()),
                signal_sources=list(sig.source_scores.keys()),
            ))
        return recs

    @staticmethod
    def _score_to_action(score: float) -> ActionType:
        if score >= 80:
            return ActionType.STRONG_BUY
        elif score >= 60:
            return ActionType.BUY
        elif score >= 40:
            return ActionType.HOLD
        elif score >= 25:
            return ActionType.REDUCE
        return ActionType.SELL

    @staticmethod
    def _score_to_confidence(
        score: float, source_count: int
    ) -> ConfidenceLevel:
        if score >= 70 and source_count >= 3:
            return ConfidenceLevel.HIGH
        elif score >= 50 and source_count >= 2:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW
