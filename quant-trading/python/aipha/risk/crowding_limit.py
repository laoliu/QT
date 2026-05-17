"""
组合拥挤度约束

通过计算组合持仓与参考策略/指数成分的 Jaccard 重叠度，
量化因子暴露拥挤风险。当重叠度超过阈值时发出警告或阻止上线。

背景：2024 年 DMA 踩踏事件教训——多策略同向持仓导致流动性危机。
参见：copilot-instructions.md 原则 A [CN] 策略拥挤度检查、原则 E [CN]。

使用示例：
    limit = CrowdingLimit(max_jaccard=0.30)
    result = limit.check(portfolio_holdings={"000001.SZ", "000002.SZ"})
    print(result.passed, result.jaccard)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class CrowdingResult:
    """拥挤度检查结果"""

    passed: bool
    jaccard: float                   # 与最拥挤参考策略的 Jaccard 系数
    max_jaccard: float               # 配置的阈值
    crowding_score: float            # 加权拥挤分（所有参考策略的均值）
    worst_reference: Optional[str]   # Jaccard 最高的参考策略名称
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "jaccard": round(self.jaccard, 4),
            "max_jaccard": self.max_jaccard,
            "crowding_score": round(self.crowding_score, 4),
            "worst_reference": self.worst_reference,
            "warnings": self.warnings,
        }


class CrowdingLimit:
    """组合拥挤度约束

    与一组参考策略/指数成分的持仓做 Jaccard 比较，
    当最大 Jaccard 超过 max_jaccard 时标记为不通过。

    Jaccard = |A ∩ B| / |A ∪ B|
    """

    def __init__(
        self,
        max_jaccard: float = 0.30,
        reference_portfolios: Optional[dict[str, set[str]]] = None,
    ) -> None:
        if not (0.0 < max_jaccard <= 1.0):
            raise ValueError(f"max_jaccard must be in (0, 1], got {max_jaccard}")
        self.max_jaccard = max_jaccard
        self._references: dict[str, set[str]] = dict(reference_portfolios or {})

    def check(self, portfolio_holdings: set[str]) -> CrowdingResult:
        """对当前持仓做拥挤度检查"""
        if not portfolio_holdings:
            return CrowdingResult(
                passed=True, jaccard=0.0, max_jaccard=self.max_jaccard,
                crowding_score=0.0, worst_reference=None,
                warnings=["portfolio_holdings is empty"],
            )

        scores = self.get_crowding_score(portfolio_holdings)
        if not scores:
            return CrowdingResult(
                passed=True, jaccard=0.0, max_jaccard=self.max_jaccard,
                crowding_score=0.0, worst_reference=None,
            )

        worst_name = max(scores, key=lambda k: scores[k])
        worst_jaccard = scores[worst_name]
        avg_score = sum(scores.values()) / len(scores)

        warnings: list[str] = []
        if worst_jaccard >= self.max_jaccard * 0.8:
            warnings.append(
                f"Jaccard with '{worst_name}' is {worst_jaccard:.3f}, "
                f"approaching limit {self.max_jaccard}"
            )

        return CrowdingResult(
            passed=worst_jaccard < self.max_jaccard,
            jaccard=worst_jaccard,
            max_jaccard=self.max_jaccard,
            crowding_score=avg_score,
            worst_reference=worst_name,
            warnings=warnings,
        )

    def get_jaccard(self, set_a: set[str], set_b: set[str]) -> float:
        """计算两个集合间的 Jaccard 系数"""
        if not set_a and not set_b:
            return 0.0
        union = set_a | set_b
        if not union:
            return 0.0
        intersection = set_a & set_b
        return len(intersection) / len(union)

    def get_crowding_score(self, portfolio_holdings: set[str]) -> dict[str, float]:
        """返回与每个参考策略的 Jaccard 系数字典"""
        return {
            name: self.get_jaccard(portfolio_holdings, ref)
            for name, ref in self._references.items()
        }

    def add_reference(self, name: str, holdings: set[str]) -> None:
        """动态添加参考策略持仓"""
        if not name:
            raise ValueError("reference name must not be empty")
        self._references[name] = set(holdings)
