"""统计稳健性验证框架。

提供：
- BootstrapAnalyzer: 基于 Bootstrap 重抽样的置信区间估计
- RollingOriginValidator: 滚动原点交叉验证（时间序列专用）
- PortfolioScaleTest: 组合规模（标的数量）鲁棒性测试
- RobustnessReport: 综合稳健性报告

设计原则：
- 纯统计方法，不依赖具体策略实现
- 输入为绩效指标序列（如各折 Sharpe / return），输出为统计结论
- 可与 WalkForwardValidator / BacktestEngine 的输出无缝对接
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Sequence


# ============================================================================
# Bootstrap 置信区间
# ============================================================================


@dataclass(frozen=True, slots=True)
class BootstrapCI:
    """Bootstrap 置信区间结果。

    Attributes:
        metric_name: 指标名称
        observed: 原始观测均值
        ci_lower: 置信区间下界
        ci_upper: 置信区间上界
        confidence_level: 置信水平（如 0.95）
        n_bootstrap: 重抽样次数
        std_error: Bootstrap 标准误
        bias: Bootstrap 偏差估计（均值 - 观测）
    """

    metric_name: str
    observed: float
    ci_lower: float
    ci_upper: float
    confidence_level: float
    n_bootstrap: int
    std_error: float
    bias: float

    def contains_zero(self) -> bool:
        """置信区间是否包含 0（若包含则指标不显著）。"""
        return self.ci_lower <= 0 <= self.ci_upper

    def to_dict(self) -> dict:
        """将 Bootstrap 置信区间结果序列化为字典。"""
        return {
            "metric_name": self.metric_name,
            "observed": self.observed,
            "ci_lower": self.ci_lower,
            "ci_upper": self.ci_upper,
            "confidence_level": self.confidence_level,
            "n_bootstrap": self.n_bootstrap,
            "std_error": self.std_error,
            "bias": self.bias,
        }


class BootstrapAnalyzer:
    """Bootstrap 重抽样置信区间分析器。

    对给定的观测序列（如多折 OOS Sharpe）执行 Bootstrap 重抽样，
    产出指标均值的置信区间，用于判断策略绩效是否统计上显著。

    Args:
        n_bootstrap: 重抽样次数（默认 10000）
        confidence_level: 置信水平（默认 0.95）
        random_seed: 随机种子
    """

    def __init__(
        self,
        n_bootstrap: int = 10000,
        confidence_level: float = 0.95,
        random_seed: int = 42,
    ):
        if n_bootstrap < 100:
            raise ValueError(f"n_bootstrap 至少 100，得到 {n_bootstrap}")
        if not 0 < confidence_level < 1:
            raise ValueError(f"confidence_level 须在 (0,1)，得到 {confidence_level}")
        self.n_bootstrap = n_bootstrap
        self.confidence_level = confidence_level
        self.rng = random.Random(random_seed)

    def estimate_ci(
        self,
        values: Sequence[float],
        metric_name: str = "metric",
    ) -> BootstrapCI:
        """对给定样本估计 Bootstrap 置信区间。

        使用百分位法（percentile method）。

        Args:
            values: 观测值序列（如各折 OOS Sharpe）
            metric_name: 指标名称

        Returns:
            BootstrapCI 结果
        """
        n = len(values)
        if n == 0:
            return BootstrapCI(
                metric_name=metric_name,
                observed=0.0,
                ci_lower=0.0,
                ci_upper=0.0,
                confidence_level=self.confidence_level,
                n_bootstrap=0,
                std_error=0.0,
                bias=0.0,
            )

        observed_mean = sum(values) / n

        # Bootstrap 重抽样
        bootstrap_means: list[float] = []
        for _ in range(self.n_bootstrap):
            sample = [self.rng.choice(values) for _ in range(n)]
            bootstrap_means.append(sum(sample) / n)

        bootstrap_means.sort()

        # 百分位置信区间
        alpha = 1 - self.confidence_level
        lower_idx = max(0, int(math.floor(alpha / 2 * self.n_bootstrap)) - 1)
        upper_idx = min(
            self.n_bootstrap - 1,
            int(math.ceil((1 - alpha / 2) * self.n_bootstrap)) - 1,
        )
        ci_lower = bootstrap_means[lower_idx]
        ci_upper = bootstrap_means[upper_idx]

        # 标准误和偏差
        boot_mean = sum(bootstrap_means) / len(bootstrap_means)
        variance = sum((x - boot_mean) ** 2 for x in bootstrap_means) / (
            len(bootstrap_means) - 1
        )
        std_error = math.sqrt(variance)
        bias = boot_mean - observed_mean

        return BootstrapCI(
            metric_name=metric_name,
            observed=observed_mean,
            ci_lower=ci_lower,
            ci_upper=ci_upper,
            confidence_level=self.confidence_level,
            n_bootstrap=self.n_bootstrap,
            std_error=std_error,
            bias=bias,
        )

    def estimate_multiple(
        self,
        metrics: dict[str, Sequence[float]],
    ) -> dict[str, BootstrapCI]:
        """对多个指标批量估计置信区间。

        Args:
            metrics: {指标名: 观测值序列}

        Returns:
            {指标名: BootstrapCI}
        """
        return {
            name: self.estimate_ci(vals, metric_name=name)
            for name, vals in metrics.items()
        }


# ============================================================================
# 滚动原点交叉验证
# ============================================================================


@dataclass(frozen=True, slots=True)
class RollingOriginResult:
    """滚动原点 CV 单期结果。

    Attributes:
        origin_idx: 原点索引（第几次滚动）
        train_size: 训练样本数
        test_size: 测试样本数
        metrics: 测试期绩效指标
    """

    origin_idx: int
    train_size: int
    test_size: int
    metrics: dict[str, float]


@dataclass(slots=True)
class RollingOriginReport:
    """滚动原点 CV 汇总报告。

    Attributes:
        strategy_name: 策略名称
        results: 各原点结果
        n_origins: 原点总数
        expanding: 是否扩展窗口（True=扩展，False=固定）
    """

    strategy_name: str
    results: list[RollingOriginResult] = field(default_factory=list)
    n_origins: int = 0
    expanding: bool = True

    def metric_series(self, metric_name: str) -> list[float]:
        """提取某指标跨所有原点的序列。"""
        return [
            r.metrics.get(metric_name, 0.0)
            for r in self.results
            if metric_name in r.metrics
        ]

    def summary_stats(self, metric_name: str) -> dict[str, float]:
        """单个指标的汇总统计（均值/标准差/最小/最大/中位数）。"""
        series = self.metric_series(metric_name)
        if not series:
            return {"mean": 0, "std": 0, "min": 0, "max": 0, "median": 0, "count": 0}
        n = len(series)
        mean = sum(series) / n
        var = sum((x - mean) ** 2 for x in series) / max(n - 1, 1)
        std = math.sqrt(var)
        sorted_s = sorted(series)
        median = (
            sorted_s[n // 2]
            if n % 2 == 1
            else (sorted_s[n // 2 - 1] + sorted_s[n // 2]) / 2
        )
        return {
            "mean": mean,
            "std": std,
            "min": min(series),
            "max": max(series),
            "median": median,
            "count": n,
        }

    def to_dict(self) -> dict:
        """将滚动原点 CV 报告序列化为字典。"""
        return {
            "strategy_name": self.strategy_name,
            "n_origins": self.n_origins,
            "expanding": self.expanding,
            "results": [
                {
                    "origin_idx": r.origin_idx,
                    "train_size": r.train_size,
                    "test_size": r.test_size,
                    "metrics": r.metrics,
                }
                for r in self.results
            ],
        }


# ============================================================================
# 组合规模鲁棒性
# ============================================================================


@dataclass(frozen=True, slots=True)
class ScaleTestPoint:
    """单个规模点的测试结果。

    Attributes:
        n_instruments: 标的数量
        metrics: 绩效指标
    """

    n_instruments: int
    metrics: dict[str, float]


@dataclass(slots=True)
class ScaleTestReport:
    """组合规模鲁棒性测试报告。

    Attributes:
        strategy_name: 策略名称
        points: 各规模点的结果
    """

    strategy_name: str
    points: list[ScaleTestPoint] = field(default_factory=list)

    def metric_vs_scale(self, metric_name: str) -> list[tuple[int, float]]:
        """返回 (n_instruments, metric_value) 序列。"""
        return [
            (p.n_instruments, p.metrics.get(metric_name, 0.0))
            for p in self.points
            if metric_name in p.metrics
        ]

    def is_monotonic_degradation(
        self, metric_name: str, tolerance: float = 0.1
    ) -> bool:
        """检查随规模增大，指标是否单调恶化超过容忍度。

        若最大规模的指标相对最小规模衰减超过容忍度则返回 True。
        """
        pairs = self.metric_vs_scale(metric_name)
        if len(pairs) < 2:
            return False
        pairs_sorted = sorted(pairs, key=lambda x: x[0])
        first_val = pairs_sorted[0][1]
        last_val = pairs_sorted[-1][1]
        if abs(first_val) < 1e-10:
            return False
        degradation = (first_val - last_val) / abs(first_val)
        return degradation > tolerance

    def to_dict(self) -> dict:
        """将组合规模鲁棒性测试报告序列化为字典。"""
        return {
            "strategy_name": self.strategy_name,
            "points": [
                {"n_instruments": p.n_instruments, "metrics": p.metrics}
                for p in self.points
            ],
        }


# ============================================================================
# 综合稳健性报告
# ============================================================================


@dataclass(slots=True)
class RobustnessReport:
    """综合稳健性评估报告。

    汇聚 Bootstrap CI、滚动原点 CV、组合规模测试的结果，
    给出综合稳健性评分和结论。

    Attributes:
        strategy_name: 策略名称
        bootstrap_results: Bootstrap 置信区间结果
        rolling_origin: 滚动原点 CV 报告（可选）
        scale_test: 组合规模测试（可选）
        overall_score: 综合稳健性评分 (0~10)
        conclusion: 文字结论
    """

    strategy_name: str
    bootstrap_results: dict[str, BootstrapCI] = field(default_factory=dict)
    rolling_origin: RollingOriginReport | None = None
    scale_test: ScaleTestReport | None = None
    overall_score: float = 0.0
    conclusion: str = ""

    def compute_score(self) -> float:
        """计算综合稳健性评分。

        评分维度（各 10 分满分，加权平均）：
        1. Sharpe CI 不含 0 → +3 分权重
        2. Return CI 不含 0 → +2 分权重
        3. 滚动原点 Sharpe > 0 比例 → +3 分权重
        4. 组合规模不单调恶化 → +2 分权重
        """
        scores: list[tuple[float, float]] = []  # (weight, score)

        # 1. Sharpe CI
        sharpe_ci = self.bootstrap_results.get("sharpe")
        if sharpe_ci is not None:
            if not sharpe_ci.contains_zero() and sharpe_ci.observed > 0:
                scores.append((3.0, 10.0))
            elif not sharpe_ci.contains_zero():
                scores.append((3.0, 5.0))
            else:
                scores.append((3.0, 2.0))

        # 2. Return CI
        return_ci = self.bootstrap_results.get("return")
        if return_ci is not None:
            if not return_ci.contains_zero() and return_ci.observed > 0:
                scores.append((2.0, 10.0))
            elif not return_ci.contains_zero():
                scores.append((2.0, 5.0))
            else:
                scores.append((2.0, 2.0))

        # 3. 滚动原点
        if self.rolling_origin and self.rolling_origin.results:
            sharpe_series = self.rolling_origin.metric_series("sharpe")
            if sharpe_series:
                pos_ratio = sum(1 for s in sharpe_series if s > 0) / len(sharpe_series)
                scores.append((3.0, pos_ratio * 10.0))

        # 4. 组合规模
        if self.scale_test and self.scale_test.points:
            if not self.scale_test.is_monotonic_degradation("sharpe"):
                scores.append((2.0, 10.0))
            else:
                scores.append((2.0, 3.0))

        if not scores:
            self.overall_score = 0.0
            return 0.0

        total_weight = sum(w for w, _ in scores)
        weighted = sum(w * s for w, s in scores)
        self.overall_score = round(weighted / total_weight, 2)
        return self.overall_score

    def generate_conclusion(self) -> str:
        """生成文字结论。"""
        parts: list[str] = [f"策略 '{self.strategy_name}' 稳健性评估："]

        sharpe_ci = self.bootstrap_results.get("sharpe")
        if sharpe_ci:
            if not sharpe_ci.contains_zero() and sharpe_ci.observed > 0:
                parts.append(
                    f"  ✓ Sharpe 显著为正 "
                    f"[{sharpe_ci.ci_lower:.3f}, {sharpe_ci.ci_upper:.3f}]"
                )
            else:
                parts.append(
                    f"  ✗ Sharpe 不显著 "
                    f"[{sharpe_ci.ci_lower:.3f}, {sharpe_ci.ci_upper:.3f}]"
                )

        return_ci = self.bootstrap_results.get("return")
        if return_ci:
            if not return_ci.contains_zero() and return_ci.observed > 0:
                parts.append(
                    f"  ✓ 收益显著为正 "
                    f"[{return_ci.ci_lower:.4f}, {return_ci.ci_upper:.4f}]"
                )
            else:
                parts.append(
                    f"  ✗ 收益不显著 "
                    f"[{return_ci.ci_lower:.4f}, {return_ci.ci_upper:.4f}]"
                )

        if self.rolling_origin and self.rolling_origin.results:
            stats = self.rolling_origin.summary_stats("sharpe")
            parts.append(
                f"  滚动原点: Sharpe 均值={stats['mean']:.3f}, "
                f"标准差={stats['std']:.3f}, 正比例={stats.get('count', 0)} 个原点"
            )

        if self.scale_test and self.scale_test.points:
            degraded = self.scale_test.is_monotonic_degradation("sharpe")
            if degraded:
                parts.append("  ✗ 组合规模增大时 Sharpe 显著恶化")
            else:
                parts.append("  ✓ 组合规模扩展未显著恶化 Sharpe")

        score = self.compute_score()
        parts.append(f"  综合稳健性评分: {score:.1f}/10")

        self.conclusion = "\n".join(parts)
        return self.conclusion

    def to_dict(self) -> dict:
        """将综合稳健性评估报告序列化为字典（含全部子报告）。"""
        return {
            "strategy_name": self.strategy_name,
            "bootstrap": {
                k: v.to_dict() for k, v in self.bootstrap_results.items()
            },
            "rolling_origin": self.rolling_origin.to_dict()
            if self.rolling_origin
            else None,
            "scale_test": self.scale_test.to_dict() if self.scale_test else None,
            "overall_score": self.overall_score,
            "conclusion": self.conclusion,
        }
