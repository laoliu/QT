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
    """荐股建议。

    价位计划字段（signal_price / max_entry_price / stop_loss / target_price /
    holding_days）共同定义「这笔交易在什么价格区间内成立」。缺了它们，任何
    买入后的浮亏都无法区分「策略正常波动」与「判断已失效」。
    """
    ts_code: str
    name: str = ""
    action: ActionType = ActionType.HOLD
    horizon: TimeHorizon = TimeHorizon.SHORT
    signal_price: float | None = None     # 打分时点的参考价（信号价）
    max_entry_price: float | None = None  # 追高上限，现价高于此值即不应买入
    target_price: float | None = None
    stop_loss: float | None = None
    holding_days: int | None = None       # 计划持有期（交易日），到期无条件复核
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

    @property
    def is_buy(self) -> bool:
        return self.action in (ActionType.BUY, ActionType.STRONG_BUY)

    @property
    def reward_risk_ratio(self) -> float | None:
        """盈亏比 = (目标价 - 信号价) / (信号价 - 止损价)。

        价位计划不完整或风险敞口非正时返回 None。
        """
        if self.signal_price is None or self.target_price is None:
            return None
        if self.stop_loss is None:
            return None
        risk = self.signal_price - self.stop_loss
        if risk <= 0:
            return None
        return (self.target_price - self.signal_price) / risk

    def entry_block_reason(
        self,
        current_price: float,
        now: datetime | None = None,
    ) -> str | None:
        """返回「不应买入」的原因，None 表示可以按计划买入。

        在「推荐生成」与「实际下单」之间价格会漂移，这里做三重拦截:
        追高、开仓前已跌破止损、信号过期。
        """
        if not self.is_buy:
            return f"action={self.action.value} 非买入建议"
        if current_price <= 0:
            return f"当前价无效: {current_price}"

        now = now or datetime.now()
        if self.expires_at is not None and now > self.expires_at:
            return f"信号已过期于 {self.expires_at:%Y-%m-%d}"

        if self.max_entry_price is not None and current_price > self.max_entry_price:
            base = self.signal_price or self.max_entry_price
            chase = (current_price / base - 1) if base > 0 else 0.0
            return (
                f"已追高 {chase:.2%}: 现价 {current_price:.3f} > "
                f"入场上限 {self.max_entry_price:.3f}"
            )

        if self.stop_loss is not None and current_price <= self.stop_loss:
            return (
                f"开仓前已跌破止损: 现价 {current_price:.3f} <= "
                f"止损 {self.stop_loss:.3f}，形态已失效"
            )
        return None

    def is_entry_allowed(
        self,
        current_price: float,
        now: datetime | None = None,
    ) -> bool:
        """现价下是否仍可按计划买入。"""
        return self.entry_block_reason(current_price, now) is None

    def to_dict(self, live_price: float | None = None) -> dict[str, Any]:
        """序列化为 dict。

        ``price`` 是 ``execution.position_guard._check_new_entries`` 读取的
        下单参考价键名。传入 ``live_price`` 时用最新价填充 —— 只有这样
        position_guard 的追高拦截才有意义（拿信号价去比信号价算出的上限，
        永远不会触发）。不传则退回信号价。
        """
        entry_price = live_price if live_price and live_price > 0 else self.signal_price
        return {
            "ts_code": self.ts_code,
            "name": self.name,
            "action": self.action.value,
            "horizon": self.horizon.value,
            "signal_price": self.signal_price,
            "price": entry_price,
            "max_entry_price": self.max_entry_price,
            "target_price": self.target_price,
            "stop_loss": self.stop_loss,
            "holding_days": self.holding_days,
            "confidence": (
                self.confidence.value
                if isinstance(self.confidence, ConfidenceLevel)
                else self.confidence
            ),
            "confidence_level": self.confidence_level,
            "reward_risk_ratio": self.reward_risk_ratio,
            "reasons": list(self.reasons),
            "signal_sources": self.signal_sources,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "metadata": dict(self.metadata),
        }


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
