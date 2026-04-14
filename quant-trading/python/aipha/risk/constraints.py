"""风控约束框架

核心设计：CompositeRiskManager 聚合所有 violations（不短路），
返回最严格的 adjusted_qty。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aipha.domain.models import Side

logger = logging.getLogger(__name__)


class RiskViolationType(str, Enum):
    POSITION_LIMIT = "POSITION_LIMIT"
    SECTOR_LIMIT = "SECTOR_LIMIT"
    DRAWDOWN_LIMIT = "DRAWDOWN_LIMIT"
    TURNOVER_LIMIT = "TURNOVER_LIMIT"
    CONCENTRATION_LIMIT = "CONCENTRATION_LIMIT"
    LIQUIDITY_LIMIT = "LIQUIDITY_LIMIT"
    FORCE_REDEEM_EXPOSURE = "FORCE_REDEEM_EXPOSURE"
    OTHER = "OTHER"


@dataclass
class RiskCheckResult:
    """风控检查结果"""

    passed: bool
    violation_type: RiskViolationType | None = None
    message: str = ""
    adjusted_qty: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RiskConstraint(ABC):
    """风控约束抽象基类"""

    @abstractmethod
    def check(
        self,
        symbol: str,
        qty: int,
        side: Side,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__


class PositionLimit(RiskConstraint):
    """单票最大持仓权重约束"""

    def __init__(self, max_weight: float = 0.10, lot_size: int = 100):
        self._max_weight = max_weight
        self._lot_size = lot_size

    def check(
        self,
        symbol: str,
        qty: int,
        side: Side,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        if side == Side.SELL:
            return RiskCheckResult(passed=True)

        price = prices.get(symbol, 0.0)
        if price <= 0 or nav <= 0:
            return RiskCheckResult(passed=True)

        current_qty = current_positions.get(symbol, 0)
        max_notional = nav * self._max_weight
        current_notional = current_qty * price
        remaining = max_notional - current_notional

        if remaining <= 0:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.POSITION_LIMIT,
                message=f"{symbol} 已达持仓上限 {self._max_weight:.0%}",
                adjusted_qty=0,
            )

        max_qty = int(remaining / price)
        max_qty = (max_qty // self._lot_size) * self._lot_size

        if max_qty >= qty:
            return RiskCheckResult(passed=True)

        return RiskCheckResult(
            passed=False,
            violation_type=RiskViolationType.POSITION_LIMIT,
            message=f"{symbol} 限仓 {self._max_weight:.0%}, qty {qty}→{max_qty}",
            adjusted_qty=max_qty,
        )


class SectorLimit(RiskConstraint):
    """行业最大持仓权重约束"""

    def __init__(self, max_weight: float = 0.30, sector_mapping: dict[str, str] | None = None):
        self._max_weight = max_weight
        self._sector_mapping = sector_mapping or {}

    def check(
        self,
        symbol: str,
        qty: int,
        side: Side,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        if side == Side.SELL:
            return RiskCheckResult(passed=True)

        sector = self._sector_mapping.get(symbol)
        if not sector:
            return RiskCheckResult(passed=True)

        if nav <= 0:
            return RiskCheckResult(passed=True)

        sector_notional = sum(
            current_positions.get(s, 0) * prices.get(s, 0)
            for s, sec in self._sector_mapping.items()
            if sec == sector
        )

        price = prices.get(symbol, 0)
        new_notional = sector_notional + qty * price
        max_notional = nav * self._max_weight

        if new_notional <= max_notional:
            return RiskCheckResult(passed=True)

        return RiskCheckResult(
            passed=False,
            violation_type=RiskViolationType.SECTOR_LIMIT,
            message=f"行业 {sector} 超限 {self._max_weight:.0%}",
            adjusted_qty=max(0, int((max_notional - sector_notional) / price)) if price > 0 else 0,
        )


class DrawdownLimit(RiskConstraint):
    """最大回撤约束"""

    def __init__(self, max_drawdown: float = 0.20, scale_factor_floor: float = 0.0):
        self._max_dd = max_drawdown
        self._floor = scale_factor_floor
        self._peak_nav = 0.0

    def update_peak(self, nav: float) -> None:
        if nav > self._peak_nav:
            self._peak_nav = nav

    def check(
        self,
        symbol: str,
        qty: int,
        side: Side,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        if side == Side.SELL:
            return RiskCheckResult(passed=True)

        self.update_peak(nav)
        if self._peak_nav <= 0:
            return RiskCheckResult(passed=True)

        current_dd = (self._peak_nav - nav) / self._peak_nav
        if current_dd < self._max_dd * 0.5:
            return RiskCheckResult(passed=True)

        if current_dd >= self._max_dd:
            return RiskCheckResult(
                passed=False,
                violation_type=RiskViolationType.DRAWDOWN_LIMIT,
                message=f"回撤 {current_dd:.1%} 超限 {self._max_dd:.1%}",
                adjusted_qty=0,
            )

        # 线性减仓区间
        scale = max(self._floor, 1.0 - (current_dd - self._max_dd * 0.5) / (self._max_dd * 0.5))
        adjusted = int(qty * scale)
        return RiskCheckResult(
            passed=False,
            violation_type=RiskViolationType.DRAWDOWN_LIMIT,
            message=f"回撤 {current_dd:.1%}, 缩仓 {scale:.0%}",
            adjusted_qty=adjusted,
        )


class TurnoverLimit(RiskConstraint):
    """日换手率约束"""

    def __init__(self, max_turnover: float = 0.30):
        self._max_turnover = max_turnover
        self._day_turnover = 0.0

    def reset_day(self) -> None:
        self._day_turnover = 0.0

    def check(
        self,
        symbol: str,
        qty: int,
        side: Side,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        price = prices.get(symbol, 0)
        if nav <= 0 or price <= 0:
            return RiskCheckResult(passed=True)

        trade_notional = qty * price
        new_turnover = self._day_turnover + trade_notional / nav

        if new_turnover <= self._max_turnover:
            self._day_turnover = new_turnover
            return RiskCheckResult(passed=True)

        remaining = (self._max_turnover - self._day_turnover) * nav
        adjusted = max(0, int(remaining / price))
        return RiskCheckResult(
            passed=False,
            violation_type=RiskViolationType.TURNOVER_LIMIT,
            message=f"日换手率 {new_turnover:.1%} 超限 {self._max_turnover:.1%}",
            adjusted_qty=adjusted,
        )


class CompositeRiskManager:
    """组合风控管理器 — 聚合所有约束（不短路）。

    关键设计：收集所有 violations 后返回最严格的 adjusted_qty，
    而非遇到第一个 violation 就停止。
    """

    def __init__(self, constraints: list[RiskConstraint] | None = None):
        self._constraints = constraints or []

    def add(self, constraint: RiskConstraint) -> None:
        self._constraints.append(constraint)

    def check(
        self,
        symbol: str,
        qty: int,
        side: Side,
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> RiskCheckResult:
        """检查所有约束，返回聚合结果。"""
        violations: list[RiskCheckResult] = []
        min_qty = qty

        for constraint in self._constraints:
            result = constraint.check(symbol, qty, side, current_positions, nav, prices)
            if not result.passed:
                violations.append(result)
                if result.adjusted_qty is not None:
                    min_qty = min(min_qty, result.adjusted_qty)

        if not violations:
            return RiskCheckResult(passed=True)

        messages = "; ".join(v.message for v in violations)
        return RiskCheckResult(
            passed=False,
            violation_type=violations[0].violation_type,
            message=messages,
            adjusted_qty=min_qty,
            metadata={"violations_count": len(violations)},
        )

    def check_batch(
        self,
        orders: list[dict[str, Any]],
        current_positions: dict[str, int],
        nav: float,
        prices: dict[str, float],
    ) -> dict[str, RiskCheckResult]:
        """批量检查订单。"""
        results: dict[str, RiskCheckResult] = {}
        for order in orders:
            symbol = order["symbol"]
            qty = order["qty"]
            side = order["side"]
            results[symbol] = self.check(symbol, qty, side, current_positions, nav, prices)
        return results
