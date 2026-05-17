"""组合级调仓建议 — 基于推荐信号与风控约束。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aipha.advisory.models import Recommendation, TimeHorizon
from aipha.advisory.operation_advisor import HoldingInfo, OperationAdvice

logger = logging.getLogger(__name__)


@dataclass
class PortfolioAdvice:
    """组合调仓建议。"""
    target_weights: dict[str, float] = field(default_factory=dict)
    operations: list[OperationAdvice] = field(default_factory=list)
    total_turnover: float = 0.0
    risk_summary: dict[str, Any] = field(default_factory=dict)


class PortfolioAdvisor:
    """组合调仓顾问。"""

    def __init__(
        self,
        max_single_weight: float = 0.10,
        max_turnover: float = 0.30,
        max_positions: int = 20,
    ) -> None:
        self._max_w = max_single_weight
        self._max_turnover = max_turnover
        self._max_positions = max_positions

    def advise(
        self,
        recommendations: list[Recommendation],
        current_holdings: dict[str, HoldingInfo] | None = None,
        total_capital: float = 1_000_000,
    ) -> PortfolioAdvice:
        """生成组合调仓建议。"""
        current_holdings = current_holdings or {}
        # 1. 筛选可操作推荐
        actionable = [
            r for r in recommendations
            if r.action.value in ("strong_buy", "buy", "sell", "reduce")
        ]
        # 2. 计算目标权重
        target_w = self._calc_target_weights(actionable, current_holdings)
        # 3. 计算操作清单
        ops = self._calc_operations(target_w, current_holdings, total_capital)
        turnover = sum(abs(o.quantity_pct) for o in ops) / 2
        return PortfolioAdvice(
            target_weights=target_w,
            operations=ops,
            total_turnover=min(turnover, self._max_turnover),
        )

    def _calc_target_weights(
        self,
        recs: list[Recommendation],
        holdings: dict[str, HoldingInfo],
    ) -> dict[str, float]:
        weights: dict[str, float] = {}
        # 保留 hold 头寸
        for code, h in holdings.items():
            if h.shares > 0 and h.current_price > 0 and h.avg_cost > 0:
                weights[code] = min(self._max_w, 0.05)
        # 加入新买入标的
        buy_recs = [r for r in recs if r.action.value in ("strong_buy", "buy")]
        for r in buy_recs[: self._max_positions]:
            w = self._max_w if r.action.value == "strong_buy" else self._max_w * 0.7
            weights[r.ts_code] = w
        # 移除卖出标的
        for r in recs:
            if r.action.value in ("sell",):
                weights.pop(r.ts_code, None)
        # 归一化
        total = sum(weights.values())
        if total > 1.0:
            factor = 1.0 / total
            weights = {k: round(v * factor, 4) for k, v in weights.items()}
        return weights

    def _calc_operations(
        self,
        target_w: dict[str, float],
        holdings: dict[str, HoldingInfo],
        capital: float,
    ) -> list[OperationAdvice]:
        ops: list[OperationAdvice] = []
        all_codes = set(target_w) | set(holdings)
        for code in all_codes:
            tw = target_w.get(code, 0.0)
            h = holdings.get(code)
            current_w = 0.0
            if h and h.current_price > 0:
                current_w = (h.shares * h.current_price) / capital if capital > 0 else 0
            diff = tw - current_w
            if abs(diff) < 0.005:
                continue
            action = "buy" if diff > 0 and current_w == 0 else (
                "add" if diff > 0 else ("sell" if tw == 0 else "reduce")
            )
            ops.append(OperationAdvice(
                ts_code=code, action=action,
                quantity_pct=abs(diff),
            ))
        return ops
