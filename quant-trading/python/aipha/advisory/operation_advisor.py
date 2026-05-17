"""操作建议生成器 — 买入/卖出/加仓/减仓/持有。

根据当前持仓状态与推荐信号，生成具体操作建议。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aipha.advisory.models import ActionType, Recommendation, TimeHorizon

logger = logging.getLogger(__name__)


@dataclass
class OperationAdvice:
    """单个操作建议。"""
    ts_code: str
    name: str = ""
    action: str = "hold"  # buy / sell / add / reduce / hold
    quantity_pct: float = 0.0  # 建议仓位百分比
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    urgency: str = "normal"  # high / normal / low
    reasons: list[str] = field(default_factory=list)
    risk_warnings: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HoldingInfo:
    """当前持仓信息。"""
    ts_code: str
    shares: int = 0
    avg_cost: float = 0.0
    current_price: float = 0.0
    pnl_pct: float = 0.0
    hold_days: int = 0


class OperationAdvisor:
    """操作建议生成器。"""

    def __init__(
        self,
        max_position_pct: float = 0.10,
        stop_loss_pct: float = 0.08,
    ) -> None:
        self._max_pos = max_position_pct
        self._stop_loss = stop_loss_pct

    def advise(
        self,
        recommendations: list[Recommendation],
        holdings: dict[str, HoldingInfo] | None = None,
    ) -> list[OperationAdvice]:
        """根据推荐与持仓生成操作建议。"""
        holdings = holdings or {}
        advices: list[OperationAdvice] = []
        for rec in recommendations:
            holding = holdings.get(rec.ts_code)
            advice = self._gen_advice(rec, holding)
            if advice:
                advices.append(advice)
        return advices

    def _gen_advice(
        self, rec: Recommendation, holding: HoldingInfo | None,
    ) -> OperationAdvice | None:
        has_position = holding is not None and holding.shares > 0
        if rec.action in (ActionType.STRONG_BUY, ActionType.BUY):
            if has_position:
                return OperationAdvice(
                    ts_code=rec.ts_code, name=rec.name,
                    action="add", quantity_pct=self._max_pos * 0.5,
                    reasons=rec.reasons, urgency="normal",
                )
            return OperationAdvice(
                ts_code=rec.ts_code, name=rec.name,
                action="buy", quantity_pct=self._max_pos,
                reasons=rec.reasons,
                urgency="high" if rec.action == ActionType.STRONG_BUY else "normal",
            )
        if rec.action in (ActionType.SELL,):
            if has_position:
                return OperationAdvice(
                    ts_code=rec.ts_code, name=rec.name,
                    action="sell", quantity_pct=1.0,
                    reasons=rec.reasons, urgency="high",
                )
            return None
        if rec.action == ActionType.REDUCE:
            if has_position:
                return OperationAdvice(
                    ts_code=rec.ts_code, name=rec.name,
                    action="reduce", quantity_pct=0.5,
                    reasons=rec.reasons, urgency="normal",
                )
            return None
        # HOLD — no action
        return None
