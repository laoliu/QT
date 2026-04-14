"""PBO (Probability of Backtest Overfitting) — 样本外过拟合检验"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_NUM_SUBSAMPLES = 16
MAX_COMBINATIONS = 100_000
PBO_OVERFIT_THRESHOLD = 0.50


@dataclass
class PBOConfig:
    num_subsamples: int = DEFAULT_NUM_SUBSAMPLES
    max_combinations: int = MAX_COMBINATIONS
    performance_metric: str = "sharpe"
    random_seed: int = 42
    overfit_threshold: float = PBO_OVERFIT_THRESHOLD

    def __post_init__(self) -> None:
        if self.num_subsamples < 4 or self.num_subsamples % 2 != 0:
            raise ValueError("num_subsamples must be even and >= 4")


@dataclass
class PBOResult:
    pbo: float
    is_overfit: bool
    num_combinations_tested: int
    num_combinations_total: int
    logit_distribution: list[float]
    best_strategy_idx: int
    oos_rank_distribution: list[float]
    stochastic_dominance_ratio: float

    def to_dict(self) -> dict:
        return {
            "pbo": round(self.pbo, 4),
            "is_overfit": self.is_overfit,
            "num_tested": self.num_combinations_tested,
            "num_total": self.num_combinations_total,
            "best_strategy_idx": self.best_strategy_idx,
            "stochastic_dominance_ratio": round(self.stochastic_dominance_ratio, 4),
        }


class PBOAnalyzer:
    """Probability of Backtest Overfitting (Bailey et al.)

    将 T 个时间样本分为 S 组, 取 S/2 组作 IS, 剩余 OOS:
    1. IS 上选最优策略
    2. 在 OOS 上评估排名
    3. PBO = P(IS 最优策略在 OOS 排名低于中位)
    """

    def __init__(self, config: PBOConfig | None = None):
        self._config = config or PBOConfig()

    def compute(self, performance_matrix: np.ndarray) -> PBOResult:
        """
        Parameters
        ----------
        performance_matrix : shape (T, N) — T 时间段, N 策略
        """
        cfg = self._config
        T, N = performance_matrix.shape
        S = cfg.num_subsamples

        if T < S:
            raise ValueError(f"Need T >= S, got T={T}, S={S}")

        rng = np.random.RandomState(cfg.random_seed)
        subsample_size = T // S
        indices = np.arange(T)
        np.random.RandomState(cfg.random_seed).shuffle(indices)

        # 切分 S 组
        groups = [indices[i * subsample_size: (i + 1) * subsample_size] for i in range(S)]

        # 所有 C(S, S/2) 的组合
        half = S // 2
        all_combos = list(combinations(range(S), half))
        total_combos = len(all_combos)

        if total_combos > cfg.max_combinations:
            chosen = rng.choice(total_combos, cfg.max_combinations, replace=False)
            combos = [all_combos[i] for i in chosen]
        else:
            combos = all_combos

        logits: list[float] = []
        oos_ranks: list[float] = []

        for is_groups in combos:
            oos_groups = tuple(i for i in range(S) if i not in is_groups)
            is_idx = np.concatenate([groups[g] for g in is_groups])
            oos_idx = np.concatenate([groups[g] for g in oos_groups])

            is_perf = self._compute_metric(performance_matrix[is_idx])  # shape (N,)
            oos_perf = self._compute_metric(performance_matrix[oos_idx])

            best_is = int(np.argmax(is_perf))
            # OOS rank of IS-best (0 = worst, 1 = best)
            oos_sorted = np.argsort(oos_perf)
            rank = np.where(oos_sorted == best_is)[0][0] / max(N - 1, 1)
            oos_ranks.append(rank)

            # Logit: log(rank / (1-rank))
            r_clipped = np.clip(rank, 0.01, 0.99)
            logits.append(math.log(r_clipped / (1 - r_clipped)))

        pbo = sum(1 for r in oos_ranks if r < 0.5) / len(oos_ranks)
        sdr = sum(1 for l in logits if l > 0) / len(logits)

        # 全局最优
        full_perf = self._compute_metric(performance_matrix)
        best_overall = int(np.argmax(full_perf))

        return PBOResult(
            pbo=pbo,
            is_overfit=pbo > cfg.overfit_threshold,
            num_combinations_tested=len(combos),
            num_combinations_total=total_combos,
            logit_distribution=logits,
            best_strategy_idx=best_overall,
            oos_rank_distribution=oos_ranks,
            stochastic_dominance_ratio=sdr,
        )

    def _compute_metric(self, returns: np.ndarray) -> np.ndarray:
        """对每个策略(列)计算绩效指标"""
        if self._config.performance_metric == "sharpe":
            return self._sharpe_ratio(returns)
        # default: cumulative return
        return np.prod(1 + returns, axis=0) - 1

    @staticmethod
    def _sharpe_ratio(returns: np.ndarray) -> np.ndarray:
        mu = np.mean(returns, axis=0)
        std = np.std(returns, axis=0, ddof=1)
        std = np.where(std < 1e-10, 1e-10, std)
        return mu / std * np.sqrt(252)


def compute_pbo(performance_matrix: np.ndarray, config: PBOConfig | None = None) -> PBOResult:
    return PBOAnalyzer(config).compute(performance_matrix)


def pbo_from_sweep_results(
    sweep_returns: dict[str, np.ndarray],
    config: PBOConfig | None = None,
) -> PBOResult:
    """从参数扫描结果计算 PBO

    sweep_returns: {param_set_name -> daily_returns_array}
    """
    names = list(sweep_returns.keys())
    arrays = [sweep_returns[n] for n in names]
    min_len = min(len(a) for a in arrays)
    matrix = np.column_stack([a[:min_len] for a in arrays])
    return compute_pbo(matrix, config)
