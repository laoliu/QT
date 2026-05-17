"""参数空间声明与稳健性分析工具。

提供策略参数空间的结构化声明、网格生成、邻域扰动分析，
将原本散落在脚本中的参数稳健性逻辑统一为可复用模块。

核心组件:
- ParamSpec: 单个参数的元数据（类型、默认值、范围、描述）
- ParamSpace: 策略级参数空间声明
- generate_grid: 从 ParamSpace 生成参数网格
- perturb_params: 对参数做邻域扰动

设计原则:
- 与 Strategy 基类解耦（通过 classmethod / standalone 工具）
- 支持 int/float/bool/choice 四种参数类型
- 可插拔：策略可选是否声明 param_space，未声明不影响运行
"""
from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ParamType(str, Enum):
    """参数类型。"""

    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    CHOICE = "choice"  # 离散枚举型


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """单个策略参数的规格声明。

    Attributes:
        name: 参数名（与策略构造函数/Config 的字段名一致）
        param_type: 参数类型
        default: 默认值
        low: 下界（对 int/float 有效）
        high: 上界（对 int/float 有效）
        step: 网格步长（可选，对 int/float 有效）
        choices: 离散选项列表（对 CHOICE 有效）
        description: 人类可读描述
        is_critical: 是否为关键参数（影响策略核心逻辑），用于敏感性分析排优先级
    """

    name: str
    param_type: ParamType
    default: Any
    low: float | None = None
    high: float | None = None
    step: float | None = None
    choices: tuple[Any, ...] | None = None
    description: str = ""
    is_critical: bool = False

    def __post_init__(self):
        """验证规格一致性。"""
        if self.param_type in (ParamType.INT, ParamType.FLOAT):
            if self.low is None or self.high is None:
                raise ValueError(
                    f"参数 '{self.name}' 类型为 {self.param_type.value}，"
                    f"必须指定 low 和 high"
                )
            if self.low > self.high:
                raise ValueError(
                    f"参数 '{self.name}': low ({self.low}) > high ({self.high})"
                )
        if self.param_type == ParamType.CHOICE:
            if not self.choices:
                raise ValueError(
                    f"参数 '{self.name}' 类型为 CHOICE，必须提供 choices"
                )


@dataclass
class ParamSpace:
    """策略参数空间声明。

    Attributes:
        strategy_name: 策略名称
        specs: 参数规格列表
    """

    strategy_name: str
    specs: list[ParamSpec] = field(default_factory=list)

    def get(self, name: str) -> ParamSpec | None:
        """按名称获取参数规格。"""
        for s in self.specs:
            if s.name == name:
                return s
        return None

    def defaults(self) -> dict[str, Any]:
        """返回所有参数的默认值字典。"""
        return {s.name: s.default for s in self.specs}

    def critical_params(self) -> list[ParamSpec]:
        """返回关键参数列表。"""
        return [s for s in self.specs if s.is_critical]

    @property
    def names(self) -> list[str]:
        """所有参数名。"""
        return [s.name for s in self.specs]


# ============================================================================
# 网格生成
# ============================================================================


def _generate_values(spec: ParamSpec) -> list[Any]:
    """为单个参数生成扫描值列表。

    Args:
        spec: 参数规格

    Returns:
        值列表
    """
    if spec.param_type == ParamType.BOOL:
        return [True, False]

    if spec.param_type == ParamType.CHOICE:
        return list(spec.choices or [])

    if spec.param_type == ParamType.INT:
        low = int(spec.low)  # type: ignore[arg-type]
        high = int(spec.high)  # type: ignore[arg-type]
        step = int(spec.step) if spec.step else max(1, (high - low) // 4)
        values = list(range(low, high + 1, step))
        # 确保 high 在列表中
        if values and values[-1] != high:
            values.append(high)
        return values

    if spec.param_type == ParamType.FLOAT:
        low = float(spec.low)  # type: ignore[arg-type]
        high = float(spec.high)  # type: ignore[arg-type]
        step = float(spec.step) if spec.step else (high - low) / 4
        if step <= 0:
            return [low, high]
        values: list[Any] = []
        v = low
        while v <= high + step * 0.01:  # 容忍浮点误差
            values.append(round(v, 10))
            v += step
        if values and abs(values[-1] - high) > step * 0.01:
            values.append(high)
        return values

    return [spec.default]


def generate_grid(
    space: ParamSpace,
    *,
    max_combinations: int = 100_000,
    only_critical: bool = False,
) -> list[dict[str, Any]]:
    """从参数空间生成笛卡尔积参数网格。

    Args:
        space: 参数空间
        max_combinations: 最大组合数，超出则截断并发出警告
        only_critical: 仅对关键参数做网格，其余用默认值

    Returns:
        参数字典列表
    """
    specs = space.critical_params() if only_critical else space.specs
    if not specs:
        return [space.defaults()]

    non_grid_specs = [s for s in space.specs if s not in specs]
    fixed = {s.name: s.default for s in non_grid_specs}

    param_names = [s.name for s in specs]
    param_values = [_generate_values(s) for s in specs]

    total = 1
    for vals in param_values:
        total *= len(vals)

    if total > max_combinations:
        logger.warning(
            f"参数网格过大 ({total:,} 组合)，截断至 {max_combinations:,}"
        )

    grid: list[dict[str, Any]] = []
    for combo in itertools.islice(itertools.product(*param_values), max_combinations):
        params = dict(zip(param_names, combo))
        params.update(fixed)
        grid.append(params)

    return grid


# ============================================================================
# 邻域扰动
# ============================================================================


def perturb_params(
    base_params: dict[str, Any],
    space: ParamSpace,
    *,
    perturbation_pcts: tuple[float, ...] = (-0.20, -0.10, 0.10, 0.20),
    only_critical: bool = False,
) -> list[dict[str, Any]]:
    """对参数做邻域扰动，用于敏感性分析。

    对每个数值参数（int/float），按 ``base_value * (1 + pct)`` 生成扰动值。
    BOOL 和 CHOICE 类型按逐值替换生成。
    每次只扰动一个参数（OAT: One-At-a-Time），其余保持基准值。

    Args:
        base_params: 基准参数字典
        space: 参数空间
        perturbation_pcts: 扰动百分比列表
        only_critical: 仅扰动关键参数

    Returns:
        扰动后的参数字典列表（含标注 ``_perturbed_param`` 和 ``_perturbation_pct``）
    """
    specs = space.critical_params() if only_critical else space.specs
    results: list[dict[str, Any]] = []

    for spec in specs:
        base_val = base_params.get(spec.name, spec.default)

        if spec.param_type in (ParamType.INT, ParamType.FLOAT):
            for pct in perturbation_pcts:
                new_val = base_val * (1 + pct)
                # 钳位到范围
                if spec.low is not None:
                    new_val = max(spec.low, new_val)
                if spec.high is not None:
                    new_val = min(spec.high, new_val)
                if spec.param_type == ParamType.INT:
                    new_val = int(round(new_val))
                else:
                    new_val = round(new_val, 10)

                # 如果扰动后等于基准值则跳过
                if new_val == base_val:
                    continue

                p = dict(base_params)
                p[spec.name] = new_val
                p["_perturbed_param"] = spec.name
                p["_perturbation_pct"] = pct
                results.append(p)

        elif spec.param_type == ParamType.BOOL:
            alt = not base_val
            p = dict(base_params)
            p[spec.name] = alt
            p["_perturbed_param"] = spec.name
            p["_perturbation_pct"] = None
            results.append(p)

        elif spec.param_type == ParamType.CHOICE:
            for choice in (spec.choices or []):
                if choice == base_val:
                    continue
                p = dict(base_params)
                p[spec.name] = choice
                p["_perturbed_param"] = spec.name
                p["_perturbation_pct"] = None
                results.append(p)

    return results
