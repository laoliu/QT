"""合规引擎 — 交易前/交易后规则检查"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCK = "block"       # 阻止交易


@dataclass
class ComplianceViolation:
    rule_name: str
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ComplianceCheckResult:
    passed: bool
    violations: list[ComplianceViolation] = field(default_factory=list)

    @property
    def blocking_violations(self) -> list[ComplianceViolation]:
        return [v for v in self.violations if v.severity == Severity.BLOCK]


class ComplianceRule(ABC):
    """合规规则基类"""

    def __init__(self, name: str, severity: Severity = Severity.BLOCK) -> None:
        self.name = name
        self.severity = severity

    @abstractmethod
    def check(self, context: dict[str, Any]) -> ComplianceViolation | None:
        ...


# ── 具体规则实现 ────────────────────────────────────────────────────


class SingleStockLimitRule(ComplianceRule):
    """单票持仓上限 (默认10%)"""

    def __init__(self, max_weight: float = 0.10, severity: Severity = Severity.BLOCK) -> None:
        super().__init__("single_stock_limit", severity)
        self._max_weight = max_weight

    def check(self, context: dict[str, Any]) -> ComplianceViolation | None:
        weights: dict[str, float] = context.get("weights", {})
        for sym, w in weights.items():
            if abs(w) > self._max_weight:
                return ComplianceViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"{sym} weight {w:.2%} exceeds limit {self._max_weight:.2%}",
                    details={"symbol": sym, "weight": w, "limit": self._max_weight},
                )
        return None


class SectorConcentrationRule(ComplianceRule):
    """行业集中度上限 (默认30%)"""

    def __init__(self, max_sector_weight: float = 0.30, severity: Severity = Severity.BLOCK) -> None:
        super().__init__("sector_concentration", severity)
        self._max = max_sector_weight

    def check(self, context: dict[str, Any]) -> ComplianceViolation | None:
        weights: dict[str, float] = context.get("weights", {})
        sector_map: dict[str, str] = context.get("sector_map", {})
        sector_weights: dict[str, float] = {}
        for sym, w in weights.items():
            sector = sector_map.get(sym, "unknown")
            sector_weights[sector] = sector_weights.get(sector, 0.0) + abs(w)
        for sector, sw in sector_weights.items():
            if sw > self._max:
                return ComplianceViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"Sector '{sector}' weight {sw:.2%} exceeds {self._max:.2%}",
                    details={"sector": sector, "weight": sw, "limit": self._max},
                )
        return None


class MinHoldingCountRule(ComplianceRule):
    """最少持仓标的数"""

    def __init__(self, min_count: int = 5, severity: Severity = Severity.WARNING) -> None:
        super().__init__("min_holding_count", severity)
        self._min = min_count

    def check(self, context: dict[str, Any]) -> ComplianceViolation | None:
        weights: dict[str, float] = context.get("weights", {})
        active = sum(1 for w in weights.values() if abs(w) > 1e-6)
        if active < self._min:
            return ComplianceViolation(
                rule_name=self.name,
                severity=self.severity,
                message=f"Only {active} holdings, minimum required {self._min}",
                details={"count": active, "minimum": self._min},
            )
        return None


class TurnoverLimitRule(ComplianceRule):
    """单次换手率上限"""

    def __init__(self, max_turnover: float = 0.30, severity: Severity = Severity.WARNING) -> None:
        super().__init__("turnover_limit", severity)
        self._max = max_turnover

    def check(self, context: dict[str, Any]) -> ComplianceViolation | None:
        old_weights: dict[str, float] = context.get("old_weights", {})
        new_weights: dict[str, float] = context.get("weights", {})
        all_syms = set(old_weights) | set(new_weights)
        turnover = sum(abs(new_weights.get(s, 0) - old_weights.get(s, 0)) for s in all_syms) / 2
        if turnover > self._max:
            return ComplianceViolation(
                rule_name=self.name,
                severity=self.severity,
                message=f"Turnover {turnover:.2%} exceeds limit {self._max:.2%}",
                details={"turnover": turnover, "limit": self._max},
            )
        return None


class RestrictedListRule(ComplianceRule):
    """禁止交易名单"""

    def __init__(self, restricted: set[str] | None = None, severity: Severity = Severity.BLOCK) -> None:
        super().__init__("restricted_list", severity)
        self._restricted = restricted or set()

    def add(self, symbol: str) -> None:
        self._restricted.add(symbol)

    def remove(self, symbol: str) -> None:
        self._restricted.discard(symbol)

    def check(self, context: dict[str, Any]) -> ComplianceViolation | None:
        weights: dict[str, float] = context.get("weights", {})
        for sym in weights:
            if sym in self._restricted and abs(weights[sym]) > 1e-8:
                return ComplianceViolation(
                    rule_name=self.name,
                    severity=self.severity,
                    message=f"{sym} is on the restricted list",
                    details={"symbol": sym},
                )
        return None


# ── 合规引擎 ────────────────────────────────────────────────────────


class ComplianceEngine:
    """合规检查引擎。

    注册多条规则，交易前统一检查。
    任何一条 BLOCK 级别违规即阻止交易。
    """

    def __init__(self) -> None:
        self._rules: list[ComplianceRule] = []

    def add_rule(self, rule: ComplianceRule) -> None:
        self._rules.append(rule)

    def add_default_rules(self) -> None:
        """加载默认的A股合规规则"""
        self.add_rule(SingleStockLimitRule())
        self.add_rule(SectorConcentrationRule())
        self.add_rule(MinHoldingCountRule())
        self.add_rule(TurnoverLimitRule())
        self.add_rule(RestrictedListRule())

    def check(self, context: dict[str, Any]) -> ComplianceCheckResult:
        """执行所有合规检查"""
        violations: list[ComplianceViolation] = []
        for rule in self._rules:
            violation = rule.check(context)
            if violation is not None:
                violations.append(violation)
                logger.warning(f"Compliance violation: [{violation.severity.value}] {violation.message}")

        blocking = any(v.severity == Severity.BLOCK for v in violations)
        return ComplianceCheckResult(passed=not blocking, violations=violations)

    def check_weights(
        self,
        weights: dict[str, float],
        old_weights: dict[str, float] | None = None,
        sector_map: dict[str, str] | None = None,
    ) -> ComplianceCheckResult:
        """简化的权重检查接口"""
        context: dict[str, Any] = {"weights": weights}
        if old_weights is not None:
            context["old_weights"] = old_weights
        if sector_map is not None:
            context["sector_map"] = sector_map
        return self.check(context)
