"""参数敏感性分析模块。

基于 OAT（One-At-a-Time）邻域扰动，评估策略参数对核心指标的敏感性。
将 scripts/param_stability.py 的分散逻辑统一为可复用的分析框架。

核心组件:
- SensitivityResult: 单参数扰动结果
- SensitivityReport: 完整敏感性分析报告
- run_sensitivity_analysis: 执行分析的入口函数

设计原则:
- 纯分析工具，不修改策略或配置
- 与回测引擎解耦（通过回调函数传入评估逻辑）
- 结果可序列化为 JSON，便于持久化和对比

Example::

    report = run_sensitivity_analysis(
        base_params={"top_k": 20, "stop_loss": 0.15},
        param_space=my_strategy.param_space(),
        evaluate_fn=lambda params: run_backtest(params)["sharpe_ratio"],
    )
    for r in report.results:
        print(f"{r.param_name}: stability={r.stability_score:.2f}")
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from aipha.strategy.param_space import ParamSpace, ParamType, perturb_params

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PerturbationPoint:
    """单个扰动点的评估结果。

    Attributes:
        param_name: 被扰动的参数名
        perturbation_pct: 扰动百分比（None 表示 bool/choice 翻转）
        param_value: 扰动后的参数值
        metric_value: 评估指标值
    """

    param_name: str
    perturbation_pct: float | None
    param_value: Any
    metric_value: float


@dataclass
class ParamSensitivity:
    """单个参数的敏感性分析结果。

    Attributes:
        param_name: 参数名
        base_value: 基准参数值
        base_metric: 基准指标值
        points: 各扰动点结果
        mean_metric: 扰动后指标均值
        std_metric: 扰动后指标标准差
        min_metric: 扰动后指标最小值
        max_metric: 扰动后指标最大值
        stability_score: 稳定性得分（0~1，越高越稳定）
        is_stable: 是否通过稳定性检查
    """

    param_name: str
    base_value: Any
    base_metric: float
    points: list[PerturbationPoint] = field(default_factory=list)
    mean_metric: float = 0.0
    std_metric: float = 0.0
    min_metric: float = 0.0
    max_metric: float = 0.0
    stability_score: float = 0.0
    is_stable: bool = True


@dataclass
class SensitivityReport:
    """完整的敏感性分析报告。

    Attributes:
        strategy_name: 策略名称
        metric_name: 评估指标名称
        base_params: 基准参数
        base_metric: 基准指标值
        results: 各参数的敏感性结果
        overall_stable: 是否所有参数都稳定
        most_sensitive_param: 最敏感的参数名
    """

    strategy_name: str
    metric_name: str
    base_params: dict[str, Any]
    base_metric: float
    results: list[ParamSensitivity] = field(default_factory=list)
    overall_stable: bool = True
    most_sensitive_param: str = ""

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典，便于 JSON 输出。"""
        return {
            "strategy_name": self.strategy_name,
            "metric_name": self.metric_name,
            "base_params": {
                k: v for k, v in self.base_params.items()
                if not k.startswith("_")
            },
            "base_metric": self.base_metric,
            "overall_stable": self.overall_stable,
            "most_sensitive_param": self.most_sensitive_param,
            "results": [
                {
                    "param_name": r.param_name,
                    "base_value": r.base_value,
                    "base_metric": r.base_metric,
                    "mean_metric": r.mean_metric,
                    "std_metric": r.std_metric,
                    "min_metric": r.min_metric,
                    "max_metric": r.max_metric,
                    "stability_score": r.stability_score,
                    "is_stable": r.is_stable,
                    "n_points": len(r.points),
                }
                for r in self.results
            ],
        }


def run_sensitivity_analysis(
    base_params: dict[str, Any],
    param_space: ParamSpace,
    evaluate_fn: Callable[[dict[str, Any]], float],
    *,
    metric_name: str = "sharpe_ratio",
    perturbation_pcts: tuple[float, ...] = (-0.20, -0.10, 0.10, 0.20),
    stability_threshold: float = 0.5,
    min_metric_threshold: float = 0.0,
    only_critical: bool = False,
) -> SensitivityReport:
    """执行参数敏感性分析。

    对每个参数做 OAT 扰动，评估指标变化，判定稳定性。

    稳定性判定规则：
    - stability_score = 1 - (std / |base_metric|)（当 base_metric ≠ 0）
    - is_stable: stability_score >= stability_threshold AND min_metric >= min_metric_threshold

    Args:
        base_params: 基准参数字典
        param_space: 参数空间声明
        evaluate_fn: 评估函数 ``params -> metric_value``
        metric_name: 指标名称（用于报告标注）
        perturbation_pcts: 扰动百分比列表
        stability_threshold: 稳定性得分阈值（低于此值视为不稳定）
        min_metric_threshold: 最低指标阈值（扰动后低于此值视为不稳定）
        only_critical: 仅分析关键参数

    Returns:
        SensitivityReport
    """
    # 基准评估
    logger.info(f"敏感性分析: 评估基准参数 ({param_space.strategy_name})")
    base_metric = evaluate_fn(base_params)

    # 生成扰动
    perturbations = perturb_params(
        base_params, param_space,
        perturbation_pcts=perturbation_pcts,
        only_critical=only_critical,
    )

    # 按参数分组评估
    param_points: dict[str, list[PerturbationPoint]] = {}
    for pert in perturbations:
        param_name = pert["_perturbed_param"]
        pct = pert["_perturbation_pct"]
        param_value = pert[param_name]

        # 清理元数据后评估
        clean_params = {
            k: v for k, v in pert.items()
            if not k.startswith("_")
        }

        try:
            metric_val = evaluate_fn(clean_params)
        except Exception as e:
            logger.warning(
                f"扰动评估失败: {param_name}={param_value} — {e}"
            )
            continue

        point = PerturbationPoint(
            param_name=param_name,
            perturbation_pct=pct,
            param_value=param_value,
            metric_value=metric_val,
        )
        param_points.setdefault(param_name, []).append(point)

    # 汇总各参数结果
    results: list[ParamSensitivity] = []
    worst_score = 1.0
    most_sensitive = ""

    for spec in (param_space.critical_params() if only_critical else param_space.specs):
        points = param_points.get(spec.name, [])
        if not points:
            results.append(ParamSensitivity(
                param_name=spec.name,
                base_value=base_params.get(spec.name, spec.default),
                base_metric=base_metric,
            ))
            continue

        metric_values = [p.metric_value for p in points]
        all_values = [base_metric] + metric_values
        mean_val = sum(all_values) / len(all_values)
        n = len(all_values)
        # 使用样本方差（ddof=1）而非总体方差，统计无偏估计
        variance = sum((v - mean_val) ** 2 for v in all_values) / (n - 1) if n > 1 else 0.0
        std_val = variance ** 0.5
        min_val = min(metric_values)
        max_val = max(metric_values)

        # 稳定性得分
        if abs(base_metric) > 1e-10:
            score = max(0.0, 1.0 - std_val / abs(base_metric))
        else:
            score = 1.0 if std_val < 1e-10 else 0.0

        is_stable = (
            score >= stability_threshold
            and min_val >= min_metric_threshold
        )

        sensitivity = ParamSensitivity(
            param_name=spec.name,
            base_value=base_params.get(spec.name, spec.default),
            base_metric=base_metric,
            points=points,
            mean_metric=mean_val,
            std_metric=std_val,
            min_metric=min_val,
            max_metric=max_val,
            stability_score=score,
            is_stable=is_stable,
        )
        results.append(sensitivity)

        if score < worst_score:
            worst_score = score
            most_sensitive = spec.name

    overall_stable = all(r.is_stable for r in results)

    report = SensitivityReport(
        strategy_name=param_space.strategy_name,
        metric_name=metric_name,
        base_params={
            k: v for k, v in base_params.items()
            if not k.startswith("_")
        },
        base_metric=base_metric,
        results=results,
        overall_stable=overall_stable,
        most_sensitive_param=most_sensitive,
    )

    logger.info(
        f"敏感性分析完成: {len(results)} 参数, "
        f"overall_stable={overall_stable}, "
        f"most_sensitive={most_sensitive}"
    )

    return report
