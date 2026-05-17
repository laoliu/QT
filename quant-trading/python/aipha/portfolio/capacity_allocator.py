"""资金量感知权重分配器 (Capacity-Aware Allocator)。

根据总资金规模和各组件的容量上限，自动调整组合权重，
避免向容量受限的策略（如小盘CB）分配超出其容量的资金。

核心逻辑:
    1. 读取各组件的容量约束 (max_capital / 绝对金额上限, max_weight / 权重上限)
  2. 按原始权重计算分配金额
    3. 若某组件分配超出硬约束，截断到更严格的上限
  4. 超出部分按指定规则 (proportional / equal / priority) 重分配

用法:
    from aipha.portfolio.capacity_allocator import CapacityAwareAllocator
    allocator = CapacityAwareAllocator(
        base_weights={"ml": 0.50, "cb": 0.40, "etf": 0.10},
        capacity_limits={"cb": 500_000},  # CB最多50万
    )
    adjusted = allocator.compute(total_capital=10_000_000)
    # adjusted.weights = {"ml": 0.55, "cb": 0.05, "etf": 0.40}
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

__all__ = [
    "CapacityAwareAllocator",
    "AllocationResult",
    "CapacityConstraint",
]


@dataclass(frozen=True)
class CapacityConstraint:
    """单组件容量约束。"""

    component: str
    max_capital: float = float("inf")  # 绝对金额上限 (元)
    max_weight: float = 1.0  # 权重上限
    min_weight: float = 0.0  # 最低权重 (保底配置)

    def __post_init__(self) -> None:
        if self.max_capital < 0:
            raise ValueError(
                f"max_capital must be >= 0, got {self.max_capital}"
            )
        if not 0 <= self.max_weight <= 1:
            raise ValueError(
                f"max_weight must be in [0,1], got {self.max_weight}"
            )
        if not 0 <= self.min_weight <= 1:
            raise ValueError(
                f"min_weight must be in [0,1], got {self.min_weight}"
            )
        if self.min_weight > self.max_weight:
            raise ValueError(
                "min_weight cannot exceed max_weight: "
                f"{self.min_weight} > {self.max_weight}"
            )


@dataclass
class AllocationResult:
    """分配结果。"""

    weights: dict[str, float]
    capitals: dict[str, float]
    total_capital: float
    adjustments: dict[str, float] = field(default_factory=dict)
    capped_components: list[str] = field(default_factory=list)
    excess_redistributed: float = 0.0

    @property
    def effective_utilization(self) -> float:
        """有效资金利用率（排除被cap的闲置）。"""
        return sum(self.capitals.values()) / self.total_capital

    def summary(self) -> str:
        """可读摘要。"""
        lines = ["CapacityAwareAllocation:"]
        for c in sorted(self.weights):
            lines.append(
                f"  {c}: w={self.weights[c]:.1%}, "
                f"cap={self.capitals[c]/10000:.0f}万"
            )
        if self.capped_components:
            lines.append(f"  Capped: {self.capped_components}")
            lines.append(
                f"  Redistributed: {self.excess_redistributed/10000:.0f}万"
            )
        return "\n".join(lines)


class CapacityAwareAllocator:
    """资金量感知权重分配器。

    根据资金规模自动调整组件权重，确保不超出容量约束。

    Args:
        base_weights: 基础权重 {component: weight}
        capacity_limits: 容量约束 {component: max_capital(元)}
        min_weights: 最低权重 {component: min_weight}
        redistribution: 超出资金重分配方式
            - "proportional": 按剩余组件原始权重比例分配
            - "equal": 等分给剩余组件
            - "priority": 按优先级列表依次分配
        priority_order: redistribution="priority" 时的优先级列表
    """

    REDISTRIBUTION_MODES = ("proportional", "equal", "priority")

    def __init__(
        self,
        base_weights: dict[str, float],
        capacity_limits: dict[str, float] | None = None,
        max_weights: dict[str, float] | None = None,
        min_weights: dict[str, float] | None = None,
        redistribution: str = "proportional",
        priority_order: list[str] | None = None,
    ) -> None:
        if redistribution not in self.REDISTRIBUTION_MODES:
            raise ValueError(
                f"redistribution must be one of {self.REDISTRIBUTION_MODES}"
            )
        # 验证权重合法性
        total_w = sum(base_weights.values())
        if not math.isclose(total_w, 1.0, abs_tol=0.01):
            raise ValueError(
                f"base_weights must sum to 1.0, got {total_w:.4f}"
            )
        self.base_weights = dict(base_weights)
        self.capacity_limits = dict(capacity_limits or {})
        self.max_weights = dict(max_weights or {})
        self.min_weights = dict(min_weights or {})
        self.redistribution = redistribution
        self.priority_order = list(priority_order or [])

    def compute(self, total_capital: float) -> AllocationResult:
        """计算调整后的权重分配.

        Args:
            total_capital: 总资金量 (元)

        Returns:
            AllocationResult 包含调整后权重和分配金额
        """
        if total_capital <= 0:
            raise ValueError(f"total_capital must be > 0, got {total_capital}")

        weights = dict(self.base_weights)
        capitals = {c: w * total_capital for c, w in weights.items()}
        capped: list[str] = []
        excess = 0.0
        adjustments: dict[str, float] = {}

        # Pass 1: 识别超出硬约束的组件并截断
        constrained_components = set(self.capacity_limits) | set(self.max_weights)
        for comp in constrained_components:
            if comp not in capitals:
                continue
            limit = self.capacity_limits.get(comp, float("inf"))
            max_weight = self.max_weights.get(comp, 1.0)
            hard_cap_w = min(limit / total_capital, max_weight)
            soft_floor_w = self.min_weights.get(comp, 0.0)

            if soft_floor_w > hard_cap_w:
                logger.warning(
                    "Soft floor ignored for %s: min_weight=%.1f%% exceeds "
                    "hard ceiling=%.1f%% (capital=%.0f万, limit=%.0f万)",
                    comp,
                    soft_floor_w * 100,
                    hard_cap_w * 100,
                    total_capital / 10000,
                    limit / 10000,
                )

            if weights[comp] > hard_cap_w:
                old_w = weights[comp]
                new_w = min(max(old_w, 0.0), hard_cap_w)
                excess += (old_w - new_w) * total_capital
                adjustments[comp] = new_w - old_w
                weights[comp] = new_w
                capitals[comp] = new_w * total_capital
                capped.append(comp)
                logger.info(
                    "Capacity cap %s: %.1f%% → %.1f%% "
                    "(hard_ceiling=%.1f%%, limit=%.0f万, alloc=%.0f万)",
                    comp, old_w * 100, new_w * 100,
                    hard_cap_w * 100,
                    limit / 10000, capitals[comp] / 10000,
                )

        # Pass 2: 将超出部分重分配给未受限组件
        if excess > 0 and capped:
            uncapped = [
                c for c in weights if c not in capped and weights[c] > 0
            ]
            if not uncapped:
                uncapped = [
                    c for c in weights if c not in capped
                ]
            if uncapped:
                excess_weight = excess / total_capital
                self._redistribute(
                    weights, uncapped, excess_weight, adjustments,
                )
            else:
                # 所有组件都被cap, 无法重分配
                logger.warning(
                    "All components capped, %.0f excess cannot be redistributed",
                    excess,
                )
            # 更新 capitals
            for c in weights:
                capitals[c] = weights[c] * total_capital

        return AllocationResult(
            weights=weights,
            capitals=capitals,
            total_capital=total_capital,
            adjustments=adjustments,
            capped_components=capped,
            excess_redistributed=excess,
        )

    def _redistribute(
        self,
        weights: dict[str, float],
        uncapped: list[str],
        excess_w: float,
        adjustments: dict[str, float],
    ) -> None:
        """将超出权重按策略分配给未受限组件。"""
        if self.redistribution == "proportional":
            total_uncapped = sum(weights[c] for c in uncapped)
            if total_uncapped <= 0:
                # 全部为0，等分
                share = excess_w / len(uncapped)
                for c in uncapped:
                    weights[c] += share
                    adjustments[c] = adjustments.get(c, 0.0) + share
            else:
                for c in uncapped:
                    ratio = weights[c] / total_uncapped
                    add = excess_w * ratio
                    weights[c] += add
                    adjustments[c] = adjustments.get(c, 0.0) + add
        elif self.redistribution == "equal":
            share = excess_w / len(uncapped)
            for c in uncapped:
                weights[c] += share
                adjustments[c] = adjustments.get(c, 0.0) + share
        elif self.redistribution == "priority":
            order = [c for c in self.priority_order if c in uncapped]
            order.extend(c for c in uncapped if c not in order)
            remaining = excess_w
            for c in order:
                add = remaining
                weights[c] += add
                adjustments[c] = adjustments.get(c, 0.0) + add
                remaining = 0.0
                break  # 全部给最高优先级

    def compute_for_range(
        self,
        capital_levels: list[float],
    ) -> list[AllocationResult]:
        """批量计算不同资金量下的分配。"""
        return [self.compute(cap) for cap in capital_levels]


def create_allocator_from_config(
    config: dict,
) -> CapacityAwareAllocator | None:
    """从 YAML 配置创建分配器。

    Args:
        config: 包含 portfolio_weights + capacity_aware 的配置字典

    Returns:
        CapacityAwareAllocator 实例，如未启用则返回 None
    """
    cap_cfg = config.get("capacity_aware", {})
    if not cap_cfg.get("enabled", False):
        return None

    base_weights = config.get("portfolio_weights", {})
    if not base_weights:
        return None

    limits = {}
    max_weights = {}
    min_weights = {}

    # 支持两种格式: 简写 (cb_max_capital) 和详细格式 (limits.cb.max_capital)
    if "cb_max_capital" in cap_cfg:
        limits["cb"] = cap_cfg["cb_max_capital"]
    if "cb_max_weight" in cap_cfg:
        max_weights["cb"] = cap_cfg["cb_max_weight"]
    if "cb_min_weight" in cap_cfg:
        min_weights["cb"] = cap_cfg["cb_min_weight"]

    # 通用 limits 字典
    for comp, limit_cfg in cap_cfg.get("limits", {}).items():
        if isinstance(limit_cfg, (int, float)):
            limits[comp] = float(limit_cfg)
        elif isinstance(limit_cfg, dict):
            limits[comp] = limit_cfg.get("max_capital", float("inf"))
            if "max_weight" in limit_cfg:
                max_weights[comp] = limit_cfg["max_weight"]
            if "min_weight" in limit_cfg:
                min_weights[comp] = limit_cfg["min_weight"]

    return CapacityAwareAllocator(
        base_weights=base_weights,
        capacity_limits=limits,
        max_weights=max_weights,
        min_weights=min_weights,
        redistribution=cap_cfg.get("redistribution_rule", "proportional"),
    )
