"""稳健性分析 — Bootstrap CI / 滚动原点 / 规模测试"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Sequence


@dataclass(frozen=True)
class BootstrapCI:
    metric_name: str
    observed: float
    ci_lower: float
    ci_upper: float
    confidence_level: float
    n_bootstrap: int
    std_error: float = 0.0
    bias: float = 0.0

    def contains_zero(self) -> bool:
        return self.ci_lower <= 0 <= self.ci_upper

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric_name, "observed": round(self.observed, 6),
            "ci_lower": round(self.ci_lower, 6), "ci_upper": round(self.ci_upper, 6),
            "confidence": self.confidence_level, "std_error": round(self.std_error, 6),
        }


@dataclass(frozen=True)
class RollingOriginResult:
    origin_idx: int
    train_size: int
    test_size: int
    metrics: dict[str, float]


@dataclass
class RollingOriginReport:
    strategy_name: str
    results: list[RollingOriginResult] = field(default_factory=list)
    n_origins: int = 0
    expanding: bool = True

    def metric_series(self, name: str) -> list[float]:
        return [r.metrics.get(name, 0) for r in self.results]

    def summary_stats(self, name: str) -> dict[str, float]:
        vals = self.metric_series(name)
        if not vals:
            return {}
        return {"mean": sum(vals) / len(vals),
                "std": (sum((v - sum(vals) / len(vals)) ** 2 for v in vals) / max(1, len(vals) - 1)) ** 0.5,
                "min": min(vals), "max": max(vals)}

    def to_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy_name, "n_origins": self.n_origins,
                "results": [{"idx": r.origin_idx, "metrics": r.metrics} for r in self.results]}


@dataclass(frozen=True)
class ScaleTestPoint:
    n_instruments: int
    metrics: dict[str, float]


@dataclass
class ScaleTestReport:
    strategy_name: str
    points: list[ScaleTestPoint] = field(default_factory=list)

    def metric_vs_scale(self, name: str) -> list[tuple[int, float]]:
        return [(p.n_instruments, p.metrics.get(name, 0)) for p in self.points]

    def is_monotonic_degradation(self, name: str, tolerance: float = 0.05) -> bool:
        vals = self.metric_vs_scale(name)
        if len(vals) < 2:
            return False
        for i in range(1, len(vals)):
            if vals[i][1] > vals[i - 1][1] + tolerance:
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {"strategy": self.strategy_name,
                "points": [{"n": p.n_instruments, "metrics": p.metrics} for p in self.points]}


@dataclass
class RobustnessReport:
    strategy_name: str
    bootstrap_results: dict[str, BootstrapCI] = field(default_factory=dict)
    rolling_origin: RollingOriginReport | None = None
    scale_test: ScaleTestReport | None = None
    overall_score: float = 0.0
    conclusion: str = ""

    def compute_score(self) -> float:
        score = 0.0
        n = 0
        for ci in self.bootstrap_results.values():
            if not ci.contains_zero():
                score += 1
            n += 1
        if self.rolling_origin:
            stats = self.rolling_origin.summary_stats("sharpe_ratio")
            if stats.get("mean", 0) > 0:
                score += 1
            n += 1
        self.overall_score = score / n if n > 0 else 0
        return self.overall_score

    def generate_conclusion(self) -> str:
        s = self.compute_score()
        if s >= 0.8:
            self.conclusion = "策略稳健性优秀"
        elif s >= 0.5:
            self.conclusion = "策略稳健性一般，存在潜在过拟合风险"
        else:
            self.conclusion = "策略稳健性较差，需谨慎"
        return self.conclusion

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy_name, "score": round(self.overall_score, 4),
            "conclusion": self.conclusion,
            "bootstrap": {k: v.to_dict() for k, v in self.bootstrap_results.items()},
        }


class BootstrapAnalyzer:
    """Bootstrap 置信区间分析"""

    def __init__(self, n_bootstrap: int = 1000, confidence_level: float = 0.95,
                 random_seed: int = 42):
        self.n_bootstrap = n_bootstrap
        self.confidence = confidence_level
        self.seed = random_seed

    def estimate_ci(self, values: Sequence[float], metric_name: str = "metric") -> BootstrapCI:
        rng = random.Random(self.seed)
        n = len(values)
        if n == 0:
            return BootstrapCI(metric_name, 0, 0, 0, self.confidence, self.n_bootstrap)

        observed = sum(values) / n
        boot_means = []
        for _ in range(self.n_bootstrap):
            sample = [values[rng.randint(0, n - 1)] for _ in range(n)]
            boot_means.append(sum(sample) / len(sample))

        boot_means.sort()
        alpha = 1 - self.confidence
        lo_idx = int(math.floor(alpha / 2 * self.n_bootstrap))
        hi_idx = int(math.ceil((1 - alpha / 2) * self.n_bootstrap)) - 1
        lo_idx = max(0, min(lo_idx, len(boot_means) - 1))
        hi_idx = max(0, min(hi_idx, len(boot_means) - 1))

        boot_mean = sum(boot_means) / len(boot_means)
        bias = boot_mean - observed
        std_err = (sum((b - boot_mean) ** 2 for b in boot_means) / max(1, len(boot_means) - 1)) ** 0.5

        return BootstrapCI(
            metric_name=metric_name, observed=observed,
            ci_lower=boot_means[lo_idx], ci_upper=boot_means[hi_idx],
            confidence_level=self.confidence, n_bootstrap=self.n_bootstrap,
            std_error=std_err, bias=bias,
        )

    def estimate_multiple(self, metrics: dict[str, Sequence[float]]) -> dict[str, BootstrapCI]:
        return {name: self.estimate_ci(vals, name) for name, vals in metrics.items()}
