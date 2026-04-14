"""策略参数空间 — 用于网格搜索 / 敏感度分析 / 参数扰动"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ParamType(str, Enum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    CHOICE = "choice"


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    param_type: ParamType
    default: Any
    low: float | None = None
    high: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] | None = None
    description: str = ""
    is_critical: bool = False

    def __post_init__(self) -> None:
        if self.param_type == ParamType.CHOICE and not self.choices:
            raise ValueError(f"ParamSpec '{self.name}': CHOICE type requires choices")
        if self.param_type in (ParamType.INT, ParamType.FLOAT):
            if self.low is not None and self.high is not None and self.low > self.high:
                raise ValueError(f"ParamSpec '{self.name}': low > high")


@dataclass
class ParamSpace:
    strategy_name: str
    specs: list[ParamSpec] = field(default_factory=list)

    def get(self, name: str) -> ParamSpec | None:
        return next((s for s in self.specs if s.name == name), None)

    def defaults(self) -> dict[str, Any]:
        return {s.name: s.default for s in self.specs}

    def critical_params(self) -> list[ParamSpec]:
        return [s for s in self.specs if s.is_critical]

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.specs]


def _generate_values(spec: ParamSpec) -> list[Any]:
    if spec.param_type == ParamType.BOOL:
        return [True, False]
    if spec.param_type == ParamType.CHOICE:
        return list(spec.choices or [])
    if spec.low is None or spec.high is None:
        return [spec.default]
    step = spec.step
    if step is None:
        if spec.param_type == ParamType.INT:
            step = max(1, (spec.high - spec.low) / 10)
        else:
            step = (spec.high - spec.low) / 10
    vals = []
    v = spec.low
    while v <= spec.high + 1e-9:
        if spec.param_type == ParamType.INT:
            vals.append(int(round(v)))
        else:
            vals.append(round(v, 8))
        v += step
    return vals


def generate_grid(
    space: ParamSpace,
    *,
    max_combinations: int = 100_000,
    only_critical: bool = False,
) -> list[dict[str, Any]]:
    """生成参数网格"""
    specs = space.critical_params() if only_critical else space.specs
    if not specs:
        return [space.defaults()]

    axes: dict[str, list[Any]] = {}
    for s in specs:
        axes[s.name] = _generate_values(s)

    # 估算组合数
    total = 1
    for v in axes.values():
        total *= len(v)
    if total > max_combinations:
        logger.warning("Grid size %d exceeds max %d, truncating", total, max_combinations)

    names = list(axes.keys())
    base = space.defaults()
    results: list[dict[str, Any]] = []
    for combo in itertools.islice(itertools.product(*[axes[n] for n in names]), max_combinations):
        params = dict(base)
        for n, v in zip(names, combo):
            params[n] = v
        results.append(params)
    return results


def perturb_params(
    base_params: dict[str, Any],
    space: ParamSpace,
    *,
    perturbation_pcts: tuple[float, ...] = (-0.20, -0.10, 0.10, 0.20),
    only_critical: bool = False,
) -> list[dict[str, Any]]:
    """基于百分比扰动生成参数变体"""
    specs = space.critical_params() if only_critical else space.specs
    results: list[dict[str, Any]] = []
    for spec in specs:
        if spec.param_type not in (ParamType.INT, ParamType.FLOAT):
            continue
        base_val = base_params.get(spec.name, spec.default)
        if not isinstance(base_val, (int, float)) or base_val == 0:
            continue
        for pct in perturbation_pcts:
            perturbed = dict(base_params)
            new_val = base_val * (1 + pct)
            if spec.low is not None:
                new_val = max(spec.low, new_val)
            if spec.high is not None:
                new_val = min(spec.high, new_val)
            if spec.param_type == ParamType.INT:
                new_val = int(round(new_val))
            perturbed[spec.name] = new_val
            results.append(perturbed)
    return results
