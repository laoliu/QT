"""参数敏感度分析 — 扰动测试"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerturbationPoint:
    param_name: str
    perturbation_pct: float | None
    param_value: Any
    metric_value: float


@dataclass
class ParamSensitivity:
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
    strategy_name: str = ""
    metric_name: str = "sharpe_ratio"
    base_params: dict[str, Any] = field(default_factory=dict)
    base_metric: float = 0.0
    results: list[ParamSensitivity] = field(default_factory=list)
    overall_stable: bool = True
    most_sensitive_param: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name, "metric": self.metric_name,
            "base_metric": round(self.base_metric, 6),
            "overall_stable": self.overall_stable,
            "most_sensitive": self.most_sensitive_param,
            "params": [{
                "name": r.param_name, "base": r.base_value,
                "stability": round(r.stability_score, 4), "stable": r.is_stable,
                "min": round(r.min_metric, 6), "max": round(r.max_metric, 6),
            } for r in self.results],
        }

    def summary(self) -> str:
        lines = [
            "=" * 50,
            f"敏感度分析: {self.strategy_name} ({self.metric_name})",
            f"基准: {self.base_metric:.4f}  整体稳定: {self.overall_stable}",
            "-" * 50,
        ]
        for r in self.results:
            flag = "✓" if r.is_stable else "✗"
            lines.append(f" {flag} {r.param_name}: 稳定性={r.stability_score:.2f} "
                         f"[{r.min_metric:.4f}, {r.max_metric:.4f}]")
        if self.most_sensitive_param:
            lines.append(f"最敏感参数: {self.most_sensitive_param}")
        return "\n".join(lines)


def _perturb_numeric(value: float, pct: float) -> float:
    return value * (1 + pct)


def run_sensitivity_analysis(
    base_params: dict[str, Any],
    evaluate_fn: Callable[[dict[str, Any]], float],
    *,
    metric_name: str = "sharpe_ratio",
    perturbation_pcts: tuple[float, ...] = (-0.3, -0.2, -0.1, 0.1, 0.2, 0.3),
    stability_threshold: float = 0.3,
    min_metric_threshold: float = 0.0,
    param_names: list[str] | None = None,
    strategy_name: str = "",
) -> SensitivityReport:
    """运行参数敏感度分析

    Parameters
    ----------
    base_params : dict — 基准参数
    evaluate_fn : callable — 接受参数 dict 返回指标值
    perturbation_pcts : tuple — 扰动百分比列表
    stability_threshold : float — 稳定性阈值 (变异系数)
    param_names : list — 要测试的参数名 (None=全部数值型)
    """
    base_metric = evaluate_fn(base_params)
    sensitivities = []
    target_params = param_names or [k for k, v in base_params.items() if isinstance(v, (int, float))]

    for pname in target_params:
        base_val = base_params.get(pname)
        if not isinstance(base_val, (int, float)) or base_val == 0:
            continue

        points = [PerturbationPoint(pname, 0.0, base_val, base_metric)]
        for pct in perturbation_pcts:
            perturbed_val = _perturb_numeric(float(base_val), pct)
            params_copy = dict(base_params)
            if isinstance(base_val, int):
                params_copy[pname] = max(1, round(perturbed_val))
            else:
                params_copy[pname] = perturbed_val

            try:
                m = evaluate_fn(params_copy)
            except Exception as e:
                logger.warning("扰动 %s=%s 失败: %s", pname, perturbed_val, e)
                continue
            points.append(PerturbationPoint(pname, pct, params_copy[pname], m))

        metrics = [p.metric_value for p in points]
        n = len(metrics)
        mean_m = sum(metrics) / n if n > 0 else 0
        std_m = (sum((x - mean_m) ** 2 for x in metrics) / max(1, n - 1)) ** 0.5 if n > 1 else 0
        cv = std_m / abs(mean_m) if abs(mean_m) > 1e-10 else 0
        stability = max(0, 1 - cv)

        sensitivities.append(ParamSensitivity(
            param_name=pname, base_value=base_val, base_metric=base_metric,
            points=points, mean_metric=mean_m, std_metric=std_m,
            min_metric=min(metrics) if metrics else 0,
            max_metric=max(metrics) if metrics else 0,
            stability_score=stability,
            is_stable=cv <= stability_threshold and min(metrics) >= min_metric_threshold,
        ))

    overall = all(s.is_stable for s in sensitivities)
    most_sensitive = min(sensitivities, key=lambda s: s.stability_score).param_name if sensitivities else ""

    return SensitivityReport(
        strategy_name=strategy_name, metric_name=metric_name,
        base_params=base_params, base_metric=base_metric,
        results=sensitivities, overall_stable=overall,
        most_sensitive_param=most_sensitive,
    )
