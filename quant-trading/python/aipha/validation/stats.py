"""统计验证工具

- DSR (Deflated Sharpe Ratio): 扣除多策略试错膨胀
- PBO (Probability of Backtest Overfitting): 过拟合概率
- HLZ (Harvey-Liu-Zhu): 多重检验 t-stat 阈值调整
- 质量门禁: 最小样本量/最低 Sharpe/最大 MDD
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


def deflated_sharpe_ratio(
    sharpe: float,
    n_trials: int,
    n_obs: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """Deflated Sharpe Ratio (Bailey & López de Prado, 2014)

    扣除多策略试错后的夏普比率显著性检验。

    Args:
        sharpe: 观测到的年化夏普比率
        n_trials: 尝试的策略/参数组合数
        n_obs: 观测数量（交易日数）
        skewness: 收益率偏度
        kurtosis: 收益率峰度（正态=3）

    Returns:
        DSR 值 (0-1, 类似 p-value 的补数)
    """
    if n_trials <= 1 or n_obs <= 1:
        return 0.5

    # Expected maximum Sharpe under i.i.d. N(0,1) — Euler-Mascheroni近似
    euler_mascheroni = 0.5772
    expected_max_sr = (
        (1 - euler_mascheroni) * _norm_ppf(1 - 1.0 / n_trials)
        + euler_mascheroni * _norm_ppf(1 - 1.0 / (n_trials * math.e))
    )

    # Sharpe ratio 的标准误 (考虑非正态性)
    sr_std = math.sqrt(
        (1 - skewness * sharpe + (kurtosis - 1) / 4 * sharpe**2) / n_obs
    )

    if sr_std <= 0:
        return 0.5

    # PSR: Prob(SR > expected_max_sr | observed)
    z = (sharpe - expected_max_sr) / sr_std
    return _norm_cdf(z)


def probability_of_backtest_overfitting(
    returns_matrix: np.ndarray,
    n_partitions: int = 10,
) -> float:
    """PBO — 过拟合概率 (Bailey et al., 2017)

    使用 Combinatorially Symmetric Cross-Validation (CSCV)。

    Args:
        returns_matrix: (n_obs, n_strategies) 收益率矩阵
        n_partitions: 划分数

    Returns:
        过拟合概率 (0-1)
    """
    n_obs, n_strats = returns_matrix.shape
    if n_strats < 2 or n_obs < n_partitions * 2:
        return 0.5

    partition_size = n_obs // n_partitions
    overfit_count = 0
    total_trials = 0

    # 简化版: 每次随机选一半 partition 作为 IS, 另一半作为 OOS
    rng = np.random.RandomState(42)
    for _ in range(min(100, n_partitions * 10)):
        shuffled = rng.permutation(n_partitions)
        half = n_partitions // 2
        is_parts = shuffled[:half]
        oos_parts = shuffled[half:half * 2]

        is_idx = np.concatenate([
            np.arange(p * partition_size, min((p + 1) * partition_size, n_obs))
            for p in is_parts
        ])
        oos_idx = np.concatenate([
            np.arange(p * partition_size, min((p + 1) * partition_size, n_obs))
            for p in oos_parts
        ])

        if len(is_idx) == 0 or len(oos_idx) == 0:
            continue

        # IS 中选最优策略
        is_sharpes = returns_matrix[is_idx].mean(axis=0) / (returns_matrix[is_idx].std(axis=0) + 1e-10)
        best_is = np.argmax(is_sharpes)

        # OOS 中该策略的排名
        oos_sharpes = returns_matrix[oos_idx].mean(axis=0) / (returns_matrix[oos_idx].std(axis=0) + 1e-10)
        rank = (oos_sharpes >= oos_sharpes[best_is]).sum()
        relative_rank = rank / n_strats

        if relative_rank > 0.5:
            overfit_count += 1
        total_trials += 1

    return overfit_count / max(total_trials, 1)


def hlz_threshold(n_trials: int, alpha: float = 0.05) -> float:
    """Harvey-Liu-Zhu (2016) 多重检验 t-stat 阈值

    给定试错次数，返回调整后的 t-stat 阈值。

    Args:
        n_trials: 尝试的策略数
        alpha: 显著性水平

    Returns:
        调整后的 t 阈值
    """
    if n_trials <= 1:
        return 1.96
    # Bonferroni 修正的近似
    adjusted_alpha = alpha / n_trials
    return _norm_ppf(1 - adjusted_alpha / 2)


@dataclass
class QualityGate:
    """质量门禁"""
    name: str
    passed: bool
    value: float
    threshold: float
    message: str


@dataclass
class QualityReport:
    """质量门禁报告"""
    gates: list[QualityGate]

    @property
    def all_passed(self) -> bool:
        return all(g.passed for g in self.gates)

    def summary(self) -> str:
        lines = ["=== 质量门禁报告 ==="]
        for g in self.gates:
            status = "✓" if g.passed else "✗"
            lines.append(f"  {status} {g.name}: {g.value:.4f} (阈值: {g.threshold:.4f}) — {g.message}")
        status = "PASS" if self.all_passed else "FAIL"
        lines.append(f"综合: {status}")
        return "\n".join(lines)


def run_quality_gates(
    sharpe: float,
    max_drawdown: float,
    total_trades: int,
    n_days: int,
    min_sharpe: float = 0.5,
    max_dd_limit: float = 0.30,
    min_trades: int = 20,
    min_days: int = 252,
    *,
    n_trials: int | None = None,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    returns_matrix: np.ndarray | None = None,
    dsr_threshold: float = 0.95,
    pbo_threshold: float = 0.50,
) -> QualityReport:
    """执行质量门禁检查。

    扩展参数 (可选, 传入时自动启用对应门禁):
        n_trials: 策略试错数 → 启用 DSR 门禁
        skewness/kurtosis: 收益率分布参数
        returns_matrix: (n_obs, n_strategies) → 启用 PBO 门禁
        dsr_threshold: DSR 通过阈值 (默认 0.95, 即 t_adj > ~1.65)
        pbo_threshold: PBO 通过阈值 (默认 0.50)
    """
    gates = [
        QualityGate(
            name="最低夏普比率",
            passed=sharpe >= min_sharpe,
            value=sharpe,
            threshold=min_sharpe,
            message="通过" if sharpe >= min_sharpe else f"夏普 {sharpe:.2f} < {min_sharpe}",
        ),
        QualityGate(
            name="最大回撤限制",
            passed=max_drawdown <= max_dd_limit,
            value=max_drawdown,
            threshold=max_dd_limit,
            message="通过" if max_drawdown <= max_dd_limit else f"回撤 {max_drawdown:.1%} > {max_dd_limit:.1%}",
        ),
        QualityGate(
            name="最少交易次数",
            passed=total_trades >= min_trades,
            value=float(total_trades),
            threshold=float(min_trades),
            message="通过" if total_trades >= min_trades else f"交易 {total_trades} < {min_trades}",
        ),
        QualityGate(
            name="最少交易日",
            passed=n_days >= min_days,
            value=float(n_days),
            threshold=float(min_days),
            message="通过" if n_days >= min_days else f"交易日 {n_days} < {min_days}",
        ),
    ]

    # DSR 门禁 (仅在 n_trials 提供时启用)
    if n_trials is not None and n_trials > 1:
        dsr_value = deflated_sharpe_ratio(
            sharpe=sharpe,
            n_trials=n_trials,
            n_obs=n_days,
            skewness=skewness,
            kurtosis=kurtosis,
        )
        gates.append(QualityGate(
            name="DSR (Deflated Sharpe Ratio)",
            passed=dsr_value >= dsr_threshold,
            value=dsr_value,
            threshold=dsr_threshold,
            message="通过" if dsr_value >= dsr_threshold else f"DSR {dsr_value:.3f} < {dsr_threshold}",
        ))

    # PBO 门禁 (仅在 returns_matrix 提供时启用)
    if returns_matrix is not None and returns_matrix.ndim == 2 and returns_matrix.shape[1] >= 2:
        pbo_value = probability_of_backtest_overfitting(returns_matrix)
        gates.append(QualityGate(
            name="PBO (过拟合概率)",
            passed=pbo_value < pbo_threshold,
            value=pbo_value,
            threshold=pbo_threshold,
            message="通过" if pbo_value < pbo_threshold else f"PBO {pbo_value:.2f} ≥ {pbo_threshold}",
        ))

    return QualityReport(gates=gates)


# ============================================================================
# 内部工具
# ============================================================================

def _norm_cdf(x: float) -> float:
    """标准正态 CDF（无 scipy 依赖）。"""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    """标准正态 PPF 近似（Rational approximation）。"""
    if p <= 0:
        return -6.0
    if p >= 1:
        return 6.0
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -_norm_ppf(1 - p)
    # Abramowitz & Stegun 26.2.23
    t = math.sqrt(-2 * math.log(1 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t**2) / (1 + d1 * t + d2 * t**2 + d3 * t**3)
