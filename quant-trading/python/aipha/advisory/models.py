"""Advisory 模块数据模型 — 荐股建议、信号源、置信度。

遵循 docs/design/20_specs/intel_advisory_qmt_architecture.md §4。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class ActionType(str, Enum):
    """操作建议类型。"""
    STRONG_BUY = "strong_buy"
    BUY = "buy"
    HOLD = "hold"
    REDUCE = "reduce"
    SELL = "sell"


class TimeHorizon(str, Enum):
    """时间维度。"""
    SHORT = "short"    # 1-5 天
    MEDIUM = "medium"  # 1-3 月
    LONG = "long"      # 3 月+


class ConfidenceLevel(str, Enum):
    """置信度等级。"""
    HIGH = "high"      # ≥ 0.7
    MEDIUM = "medium"  # 0.4-0.7
    LOW = "low"        # < 0.4


@dataclass
class Recommendation:
    """荐股建议。"""
    ts_code: str
    name: str = ""
    action: ActionType = ActionType.HOLD
    horizon: TimeHorizon = TimeHorizon.SHORT
    target_price: float | None = None
    stop_loss: float | None = None
    confidence: float | ConfidenceLevel = 0.5
    reasons: list[str] = field(default_factory=list)
    signal_sources: dict[str, float] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def confidence_level(self) -> str:
        if isinstance(self.confidence, ConfidenceLevel):
            return self.confidence.value
        if self.confidence >= 0.7:
            return ConfidenceLevel.HIGH.value
        if self.confidence >= 0.4:
            return ConfidenceLevel.MEDIUM.value
        return ConfidenceLevel.LOW.value


@dataclass
class ScreenerResult:
    """筛选器结果。"""
    ts_code: str
    name: str = ""
    score: float = 0.0
    rank: int = 0
    factors: dict[str, float] = field(default_factory=dict)
    screener_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
