"""拥挤度限制 — Jaccard 重叠度检查"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CrowdingResult:
    passed: bool
    jaccard: float
    max_jaccard: float
    crowding_score: float
    worst_reference: str
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "jaccard": round(self.jaccard, 4),
            "max_jaccard": round(self.max_jaccard, 4),
            "crowding_score": round(self.crowding_score, 4),
            "worst_reference": self.worst_reference,
            "warnings": self.warnings,
        }


class CrowdingLimit:
    """拥挤度限制: 检查持仓与参考组合的 Jaccard 重叠度"""

    def __init__(
        self,
        max_jaccard: float = 0.30,
        reference_portfolios: dict[str, set[str]] | None = None,
    ):
        self._max_jaccard = max_jaccard
        self._references: dict[str, set[str]] = reference_portfolios or {}

    def add_reference(self, name: str, holdings: set[str]) -> None:
        self._references[name] = holdings

    @staticmethod
    def get_jaccard(set_a: set[str], set_b: set[str]) -> float:
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    def get_crowding_score(self, portfolio_holdings: set[str]) -> dict[str, float]:
        return {
            name: self.get_jaccard(portfolio_holdings, ref)
            for name, ref in self._references.items()
        }

    def check(self, portfolio_holdings: set[str]) -> CrowdingResult:
        if not self._references:
            return CrowdingResult(
                passed=True, jaccard=0.0, max_jaccard=self._max_jaccard,
                crowding_score=0.0, worst_reference="", warnings=[],
            )
        scores = self.get_crowding_score(portfolio_holdings)
        worst_name = max(scores, key=scores.get)  # type: ignore
        worst_score = scores[worst_name]
        avg_score = sum(scores.values()) / len(scores)

        warnings: list[str] = []
        passed = True
        if worst_score > self._max_jaccard:
            passed = False
            warnings.append(
                f"Jaccard with '{worst_name}' = {worst_score:.2%} > {self._max_jaccard:.0%}"
            )
        if avg_score > self._max_jaccard * 0.7:
            warnings.append(f"Avg crowding {avg_score:.2%} approaching limit")

        return CrowdingResult(
            passed=passed, jaccard=worst_score, max_jaccard=self._max_jaccard,
            crowding_score=avg_score, worst_reference=worst_name, warnings=warnings,
        )
