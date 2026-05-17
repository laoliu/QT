"""PBO 回测过拟合概率框架 — Probability of Backtest Overfitting

基于 López de Prado (2018) 的 CSCV (Combinatorially Symmetric Cross-Validation)
方法，评估回测策略过拟合风险。

算法原理:
1. 将回测收益序列按时间切割为 S 个不重叠子样本 (sub-samples)
2. 从 S 个子样本中取 S/2 个组成 "训练集"(IS)，剩余 S/2 组成 "测试集"(OOS)
3. 对每种 C(S, S/2) 划分方式:
   a. 在 IS 上选出表现 "最优" 的策略 (或参数组合) k*
   b. 在 OOS 上评估 k* 的排名: rank_oos(k*) / N
   c. 如果 OOS 排名 > 0.5（即低于中位数），记为一次过拟合
4. PBO = #{OOS 排名 > 0.5} / #{总划分数}
5. PBO > 0.5 → 策略过拟合风险极高

该框架可应用于:
- 同一策略的多参数组合（参数扫描后验证）
- 多策略选择（从 N 个候选策略中选最优时的过拟合风险）

References:
- Bailey, Borwein, López de Prado, Zhu (2017) "The Probability of Backtest Overfitting"
- López de Prado (2018) "Advances in Financial Machine Learning" Ch. 11-12
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================================
# Constants
# ============================================================================

DEFAULT_NUM_SUBSAMPLES = 16  # 子样本数 (须为偶数)
MAX_COMBINATIONS = 100_000   # 组合数上限 (超过时随机抽样)
PBO_OVERFIT_THRESHOLD = 0.50  # PBO > 0.5 → 标记为过拟合


# ============================================================================
# Data Classes
# ============================================================================


@dataclass
class PBOConfig:
    """PBO 计算配置。

    Attributes:
        num_subsamples: 子样本切割数 (须为偶数，默认 16)
        max_combinations: 最大组合数 (超过时随机抽样)
        performance_metric: 用于排名的绩效指标 ('sharpe', 'return', 'calmar')
        random_seed: 随机种子 (用于组合抽样)
        overfit_threshold: 过拟合判定阈值 (默认 0.5)
    """
    num_subsamples: int = DEFAULT_NUM_SUBSAMPLES
    max_combinations: int = MAX_COMBINATIONS
    performance_metric: str = "sharpe"
    random_seed: int = 42
    overfit_threshold: float = PBO_OVERFIT_THRESHOLD

    def __post_init__(self) -> None:
        if self.num_subsamples < 4 or self.num_subsamples % 2 != 0:
            raise ValueError(
                f"num_subsamples 须为 >= 4 的偶数, got {self.num_subsamples}"
            )


@dataclass
class PBOResult:
    """PBO 计算结果。

    Attributes:
        pbo: 回测过拟合概率 [0, 1]
        is_overfit: 是否标记为过拟合 (pbo > threshold)
        num_combinations_tested: 实际测试的组合数
        num_combinations_total: 理论组合总数
        logit_distribution: 各划分的 logit(rank_oos) 分布
        best_strategy_idx: IS 中最常被选为最优的策略索引
        oos_rank_distribution: OOS 排名分布 [0,1]
        stochastic_dominance_ratio: OOS 均值收益 / IS 均值收益
    """
    pbo: float
    is_overfit: bool
    num_combinations_tested: int
    num_combinations_total: int
    logit_distribution: list[float] = field(default_factory=list)
    best_strategy_idx: int | None = None
    oos_rank_distribution: list[float] = field(default_factory=list)
    stochastic_dominance_ratio: float | None = None


# ============================================================================
# Core Class
# ============================================================================


class PBOAnalyzer:
    """PBO 过拟合概率分析器。

    用法:
        analyzer = PBOAnalyzer(config)
        result = analyzer.compute(performance_matrix)

    其中 performance_matrix 形状 (T, N):
        T = 总期数 (交易日/月), N = 策略/参数组合数
    """

    def __init__(self, config: PBOConfig | None = None):
        self.config = config or PBOConfig()
        self._rng = np.random.default_rng(self.config.random_seed)

    def compute(self, performance_matrix: np.ndarray) -> PBOResult:
        """执行 CSCV PBO 分析。

        Args:
            performance_matrix: 收益矩阵 (T, N)
                T = 时间期数, N = 策略/参数组合数

        Returns:
            PBOResult
        """
        if performance_matrix.ndim != 2:
            raise ValueError("performance_matrix 须为 2D 数组 (T, N)")

        t_rows, n_strategies = performance_matrix.shape
        if n_strategies < 2:
            raise ValueError("至少需要 2 个策略/参数组合")

        s = self.config.num_subsamples

        # 切割子样本
        subsamples = self._split_subsamples(performance_matrix)
        half_s = s // 2

        # 理论组合总数 C(S, S/2)
        total_combos = math.comb(s, half_s)
        logger.info(
            "PBO CSCV: S=%d, N=%d, C(S,S/2)=%d",
            s, n_strategies, total_combos,
        )

        # 确定要测试的划分
        all_indices = list(range(s))
        if total_combos <= self.config.max_combinations:
            # 枚举所有
            is_combos = list(combinations(all_indices, half_s))
        else:
            # 随机抽样
            logger.info(
                "组合数 %d 超过上限 %d，随机抽样",
                total_combos, self.config.max_combinations,
            )
            is_combos = []
            seen: set[tuple[int, ...]] = set()
            while len(is_combos) < self.config.max_combinations:
                perm = tuple(sorted(
                    self._rng.choice(all_indices, half_s, replace=False)
                ))
                if perm not in seen:
                    seen.add(perm)
                    is_combos.append(perm)

        n_tested = len(is_combos)
        overfit_count = 0
        logit_values: list[float] = []
        oos_ranks: list[float] = []
        best_counts = np.zeros(n_strategies, dtype=int)
        is_mean_perf_sum = 0.0
        oos_mean_perf_sum = 0.0

        for is_indices in is_combos:
            oos_indices = tuple(
                i for i in all_indices if i not in is_indices
            )

            # 拼接 IS / OOS 收益矩阵
            is_returns = np.vstack([subsamples[i] for i in is_indices])
            oos_returns = np.vstack([subsamples[i] for i in oos_indices])

            # IS 上选最优策略
            is_scores = self._compute_metric(is_returns)
            best_k = int(np.argmax(is_scores))
            best_counts[best_k] += 1

            # OOS 上评估 best_k 的排名
            oos_scores = self._compute_metric(oos_returns)
            # 排名: 从 0 (最差) 到 N-1 (最好)
            rank_order = np.argsort(np.argsort(oos_scores))
            rank_k = rank_order[best_k]
            # 归一化到 [0, 1]
            relative_rank = rank_k / (n_strategies - 1) if n_strategies > 1 else 0.5

            oos_ranks.append(float(relative_rank))

            # logit 值: log(rank / (1 - rank))
            rank_clipped = np.clip(relative_rank, 1e-6, 1.0 - 1e-6)
            logit_val = float(np.log(rank_clipped / (1.0 - rank_clipped)))
            logit_values.append(logit_val)

            # 过拟合判定: OOS 排名低于中位数 → 过拟合
            if relative_rank < 0.5:
                overfit_count += 1

            # 累计均值绩效
            is_mean_perf_sum += float(is_scores[best_k])
            oos_mean_perf_sum += float(oos_scores[best_k])

        pbo = overfit_count / n_tested if n_tested > 0 else 0.0

        # 随机优势比: OOS 均值 / IS 均值
        sd_ratio = None
        if n_tested > 0 and abs(is_mean_perf_sum) > 1e-12:
            sd_ratio = oos_mean_perf_sum / is_mean_perf_sum

        result = PBOResult(
            pbo=pbo,
            is_overfit=pbo > self.config.overfit_threshold,
            num_combinations_tested=n_tested,
            num_combinations_total=total_combos,
            logit_distribution=logit_values,
            best_strategy_idx=int(np.argmax(best_counts)),
            oos_rank_distribution=oos_ranks,
            stochastic_dominance_ratio=sd_ratio,
        )

        logger.info(
            "PBO = %.4f (%s), tested %d/%d combos",
            result.pbo,
            "OVERFIT" if result.is_overfit else "OK",
            n_tested, total_combos,
        )
        return result

    def _split_subsamples(
        self, matrix: np.ndarray,
    ) -> list[np.ndarray]:
        """将 (T, N) 矩阵按时间切为 S 个子样本。

        丢弃尾部无法整除的部分。
        """
        t_rows, _ = matrix.shape
        s = self.config.num_subsamples
        chunk_size = t_rows // s
        if chunk_size < 1:
            raise ValueError(
                f"数据行数 {t_rows} 不足以切割为 {s} 个子样本"
            )
        subsamples = []
        for i in range(s):
            start = i * chunk_size
            end = start + chunk_size
            subsamples.append(matrix[start:end])
        return subsamples

    def _compute_metric(
        self, returns: np.ndarray,
    ) -> np.ndarray:
        """对每列（策略）计算绩效指标，返回长度为 N 的数组。"""
        n_cols = returns.shape[1] if returns.ndim > 1 else 1
        scores = np.zeros(n_cols)
        for j in range(n_cols):
            col = returns[:, j] if returns.ndim > 1 else returns
            if self.config.performance_metric == "sharpe":
                scores[j] = self._sharpe_ratio(col)
            elif self.config.performance_metric == "return":
                scores[j] = float(np.sum(col))
            elif self.config.performance_metric == "calmar":
                total = float(np.sum(col))
                cum = np.cumsum(col)
                peak = np.maximum.accumulate(cum)
                dd = peak - cum
                max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0
                scores[j] = total / max_dd if max_dd > 1e-12 else total
            else:
                scores[j] = float(np.sum(col))
        return scores

    def _sharpe_ratio(self, returns: np.ndarray) -> float:
        """计算年化夏普比率 — §13.2 委托共享模块。"""
        from aipha.utils.metrics import compute_sharpe_ratio
        return compute_sharpe_ratio(returns)


# ============================================================================
# Convenience Functions
# ============================================================================


def compute_pbo(
    performance_matrix: np.ndarray,
    config: PBOConfig | None = None,
) -> PBOResult:
    """快捷函数：计算 PBO。

    Args:
        performance_matrix: 收益矩阵 (T, N)
        config: 可选配置

    Returns:
        PBOResult
    """
    analyzer = PBOAnalyzer(config)
    return analyzer.compute(performance_matrix)


def pbo_from_sweep_results(
    sweep_returns: dict[str, np.ndarray],
    config: PBOConfig | None = None,
) -> PBOResult:
    """从参数扫描结果计算 PBO。

    Args:
        sweep_returns: {param_label: daily_returns_array} 字典
            每个 value 须为 1D 日收益数组，长度应一致
        config: 可选配置

    Returns:
        PBOResult

    Raises:
        ValueError: 如果收益序列长度不一致
    """
    if not sweep_returns:
        raise ValueError("sweep_returns 不能为空")

    labels = sorted(sweep_returns.keys())
    arrays = [sweep_returns[k] for k in labels]

    # 检查长度一致性
    lengths = {len(a) for a in arrays}
    if len(lengths) > 1:
        raise ValueError(
            f"收益序列长度不一致: {lengths}。"
            "所有参数组合须覆盖相同时间范围。"
        )

    # 组成 (T, N) 矩阵
    matrix = np.column_stack(arrays)
    return compute_pbo(matrix, config)
