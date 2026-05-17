"""指数增强策略框架 — CSI300/500/1000 增强。

基于 Barra 风格约束构建指数增强组合，目标跟踪误差 2-8%，
通过因子 alpha 信号获取超额收益。

理论基础：
- Grinold-Kahn (1999): IR = TC × IC × √BR
- Barra CNE6 风格约束：行业偏离 ±5%，风格偏离 ±0.5σ
- Fama-MacBeth (1973): 截面回归因子归因
- 知识库 N1-N3: 指数增强策略框架

与现有架构关系：
- 继承 EquityRotationStrategy 的调仓/止损基础设施
- 因子打分来自 factors/ 模块
- 风控约束来自 risk/ 模块

使用示例::

    config = IndexEnhanceConfig(
        benchmark="csi1000",
        tracking_error_target=0.05,
    )
    strategy = IndexEnhanceStrategy(config)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


# ── 配置 ──


@dataclass
class IndexEnhanceConfig:
    """指数增强配置"""

    benchmark: str = "csi1000"
    tracking_error_target: float = 0.05
    tracking_error_max: float = 0.08
    industry_deviation_max: float = 0.05
    style_deviation_max: float = 0.5
    top_k: int = 100
    rebalance_days: int = 5
    min_weight: float = 0.001
    max_weight: float = 0.05
    turnover_limit: float = 0.30
    ir_target: float = 1.0


@dataclass
class BenchmarkWeight:
    """基准权重"""
    code: str
    weight: float
    industry: str = ""


@dataclass
class EnhanceResult:
    """增强结果"""
    target_weights: dict[str, float] = field(default_factory=dict)
    active_weights: dict[str, float] = field(default_factory=dict)
    predicted_te: float = 0.0
    industry_deviations: dict[str, float] = field(default_factory=dict)
    alpha_exposure: float = 0.0


# ── Barra 风格约束 ──


class BarraConstraint:
    """Barra 风格约束检查器"""

    def __init__(self, config: IndexEnhanceConfig) -> None:
        self.config = config

    def check_industry(
        self, active_w: dict, benchmark_industries: dict,
    ) -> dict[str, float]:
        """检查行业偏离"""
        deviations: dict[str, float] = {}
        all_industries = set(benchmark_industries.keys())
        for ind in all_industries:
            port_w = sum(
                w for c, w in active_w.items()
                if benchmark_industries.get(c, "") == ind
            )
            bench_w = sum(
                bw.weight for bw in benchmark_industries.get(ind, [])
            ) if isinstance(next(iter(benchmark_industries.values()), None), list) else 0.0
            deviations[ind] = port_w - bench_w
        return deviations

    def check_tracking_error(
        self, active_w: dict, cov_matrix: pd.DataFrame,
    ) -> float:
        """估算追踪误差: TE = sqrt(w_a' Σ w_a) * sqrt(TRADING_DAYS_PER_YEAR)"""
        codes = list(active_w.keys())
        w = np.array([active_w.get(c, 0.0) for c in codes])
        if cov_matrix is None or len(codes) == 0:
            return 0.0
        shared = [c for c in codes if c in cov_matrix.columns]
        if not shared:
            return 0.0
        cov = cov_matrix.loc[shared, shared].values
        w_sub = np.array([active_w[c] for c in shared])
        var = w_sub @ cov @ w_sub
        return float(np.sqrt(max(var, 0.0)) * np.sqrt(TRADING_DAYS_PER_YEAR))

    def is_feasible(
        self, active_w: dict, benchmark_industries: dict,
        cov_matrix: Optional[pd.DataFrame] = None,
    ) -> tuple[bool, list[str]]:
        """综合可行性检查"""
        violations: list[str] = []
        # 行业偏离
        devs = self.check_industry(active_w, benchmark_industries)
        for ind, dev in devs.items():
            if abs(dev) > self.config.industry_deviation_max:
                violations.append(
                    f"Industry {ind} deviation {dev:.2%} "
                    f"> {self.config.industry_deviation_max:.2%}"
                )
        # 追踪误差
        if cov_matrix is not None:
            te = self.check_tracking_error(active_w, cov_matrix)
            if te > self.config.tracking_error_max:
                violations.append(
                    f"TE {te:.2%} > {self.config.tracking_error_max:.2%}"
                )
        return len(violations) == 0, violations


class IndexEnhanceOptimizer:
    """指数增强组合优化器

    基于 alpha 信号和 Barra 约束构建增强组合。
    简化版：等权偏离 + 约束裁剪。
    """

    def __init__(self, config: IndexEnhanceConfig) -> None:
        self.config = config
        self.constraint = BarraConstraint(config)

    def optimize(
        self,
        alpha_scores: dict[str, float],
        benchmark_weights: dict[str, float],
        industries: Optional[dict[str, str]] = None,
    ) -> EnhanceResult:
        """构建增强组合"""
        if not alpha_scores:
            return EnhanceResult()

        # 选择 alpha 最高的 top_k 只股票
        sorted_codes = sorted(
            alpha_scores.keys(),
            key=lambda c: alpha_scores[c],
            reverse=True,
        )[: self.config.top_k]

        # 基准权重归一化
        total_bw = sum(benchmark_weights.values()) or 1.0
        bench_norm = {
            c: w / total_bw for c, w in benchmark_weights.items()
        }

        # 初始权重：基准权重 + alpha 倾斜
        target: dict[str, float] = {}
        for code in sorted_codes:
            base = bench_norm.get(code, 1.0 / len(sorted_codes))
            alpha_tilt = alpha_scores[code] * 0.01  # 缩放
            w = max(base + alpha_tilt, self.config.min_weight)
            w = min(w, self.config.max_weight)
            target[code] = w

        # 归一化
        total = sum(target.values())
        if total > 0:
            target = {c: w / total for c, w in target.items()}

        # 主动权重
        active = {
            c: target.get(c, 0.0) - bench_norm.get(c, 0.0)
            for c in set(target) | set(bench_norm)
        }

        return EnhanceResult(
            target_weights=target,
            active_weights=active,
        )
