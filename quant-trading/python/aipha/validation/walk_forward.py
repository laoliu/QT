"""Walk-Forward 验证框架 — 时间序列交叉验证 + 样本外检验

实现：
1. WalkForwardSplit — 滚动窗口/扩展窗口切分
2. WalkForwardValidator — 多折验证 + 汇总
3. ComboPBO — 组合对称性检验（Probability of Backtest Overfitting）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── 数据切分 ────────────────────────────────────────────────────────

@dataclass
class FoldSpec:
    fold_id: int
    train_start: int    # 行索引
    train_end: int
    test_start: int
    test_end: int

    @property
    def train_size(self) -> int:
        return self.train_end - self.train_start

    @property
    def test_size(self) -> int:
        return self.test_end - self.test_start


class WalkForwardSplit:
    """时间序列 Walk-Forward 切分器。

    支持两种模式：
    - rolling: 固定训练窗口滚动
    - expanding: 训练窗口逐步扩大
    """

    def __init__(
        self,
        n_splits: int = 5,
        train_size: int = 500,
        test_size: int = 60,
        purge_gap: int = 5,           # 训练/测试间的净化间隔
        mode: str = "rolling",        # rolling / expanding
    ) -> None:
        self._n_splits = n_splits
        self._train_size = train_size
        self._test_size = test_size
        self._purge_gap = purge_gap
        self._mode = mode

    def split(self, total_size: int) -> list[FoldSpec]:
        """生成折叠规格"""
        folds: list[FoldSpec] = []
        step = self._test_size

        for i in range(self._n_splits):
            test_end = total_size - i * step
            test_start = test_end - self._test_size

            if test_start < 0:
                break

            train_end = test_start - self._purge_gap

            if self._mode == "expanding":
                train_start = 0
            else:
                train_start = max(0, train_end - self._train_size)

            if train_start >= train_end or train_end <= 0:
                break

            folds.append(FoldSpec(
                fold_id=self._n_splits - 1 - i,
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            ))

        folds.sort(key=lambda f: f.fold_id)
        return folds


# ── 单折结果 ────────────────────────────────────────────────────────

@dataclass
class FoldResult:
    fold_id: int
    train_metric: float        # 样本内指标
    test_metric: float         # 样本外指标
    train_sharpe: float = 0.0
    test_sharpe: float = 0.0
    overfit_ratio: float = 0.0  # test_metric / train_metric
    extra: dict[str, Any] = field(default_factory=dict)


# ── 汇总结果 ────────────────────────────────────────────────────────

@dataclass
class WalkForwardReport:
    n_folds: int
    fold_results: list[FoldResult]
    avg_train_metric: float
    avg_test_metric: float
    avg_overfit_ratio: float
    degradation: float            # 1 - test/train
    pbo_estimate: float           # PBO 估计值
    is_overfit: bool              # PBO > 0.5

    def to_dict(self) -> dict:
        return {
            "n_folds": self.n_folds,
            "avg_train_metric": round(self.avg_train_metric, 4),
            "avg_test_metric": round(self.avg_test_metric, 4),
            "degradation": round(self.degradation, 4),
            "pbo_estimate": round(self.pbo_estimate, 4),
            "is_overfit": self.is_overfit,
            "folds": [
                {
                    "fold": fr.fold_id,
                    "train": round(fr.train_metric, 4),
                    "test": round(fr.test_metric, 4),
                    "overfit_ratio": round(fr.overfit_ratio, 4),
                }
                for fr in self.fold_results
            ],
        }


# ── Walk-Forward 验证器 ─────────────────────────────────────────────

class WalkForwardValidator:
    """Walk-Forward 验证器。

    用法：
        validator = WalkForwardValidator(splitter, train_fn, eval_fn)
        report = validator.run(data)

    其中：
    - train_fn(train_data) → model
    - eval_fn(model, data) → metric (如 Sharpe)
    """

    def __init__(
        self,
        splitter: WalkForwardSplit,
        train_fn: Callable[[pd.DataFrame], Any],
        eval_fn: Callable[[Any, pd.DataFrame], float],
    ) -> None:
        self._splitter = splitter
        self._train_fn = train_fn
        self._eval_fn = eval_fn

    def run(self, data: pd.DataFrame) -> WalkForwardReport:
        """执行 Walk-Forward 验证"""
        folds = self._splitter.split(len(data))
        if not folds:
            raise ValueError("Not enough data for walk-forward splits")

        fold_results: list[FoldResult] = []

        for fold in folds:
            train_data = data.iloc[fold.train_start:fold.train_end]
            test_data = data.iloc[fold.test_start:fold.test_end]

            # 训练
            model = self._train_fn(train_data)

            # 评估
            train_metric = self._eval_fn(model, train_data)
            test_metric = self._eval_fn(model, test_data)

            overfit_ratio = test_metric / train_metric if abs(train_metric) > 1e-8 else 0.0

            fold_results.append(FoldResult(
                fold_id=fold.fold_id,
                train_metric=train_metric,
                test_metric=test_metric,
                overfit_ratio=overfit_ratio,
            ))

            logger.info(
                f"Fold {fold.fold_id}: train={train_metric:.4f} test={test_metric:.4f} "
                f"ratio={overfit_ratio:.2f}"
            )

        # 汇总
        avg_train = np.mean([fr.train_metric for fr in fold_results])
        avg_test = np.mean([fr.test_metric for fr in fold_results])
        avg_ratio = np.mean([fr.overfit_ratio for fr in fold_results])
        degradation = 1.0 - (avg_test / avg_train if abs(avg_train) > 1e-8 else 0.0)

        # 简化 PBO 估计: 样本外比样本内差的折数占比
        n_worse = sum(1 for fr in fold_results if fr.test_metric < fr.train_metric)
        pbo = n_worse / len(fold_results)

        return WalkForwardReport(
            n_folds=len(fold_results),
            fold_results=fold_results,
            avg_train_metric=float(avg_train),
            avg_test_metric=float(avg_test),
            avg_overfit_ratio=float(avg_ratio),
            degradation=float(degradation),
            pbo_estimate=pbo,
            is_overfit=pbo > 0.5,
        )


# ── Combinatorial PBO (Bailey et al. 2014) ──────────────────────────

class CombinatorialPBO:
    """组合概率回测过拟合检验。

    基于 S 个子样本的所有 C(S, S/2) 组合，
    检验最优策略在样本外是否仍然最优。
    """

    def __init__(self, n_subsets: int = 10) -> None:
        self._n_subsets = n_subsets

    def estimate(self, performance_matrix: np.ndarray) -> float:
        """估计 PBO。

        Args:
            performance_matrix: shape (S, N) 其中 S=子样本数, N=策略数

        Returns:
            PBO 估计值 [0, 1]，>0.5 表示过拟合
        """
        S, N = performance_matrix.shape
        if S < 4 or N < 2:
            return 0.0

        half = S // 2
        n_trials = min(100, S)  # 限制组合数
        n_overfit = 0

        rng = np.random.default_rng(42)
        for _ in range(n_trials):
            perm = rng.permutation(S)
            in_sample = perm[:half]
            out_sample = perm[half:2 * half]

            is_perf = performance_matrix[in_sample].mean(axis=0)
            oos_perf = performance_matrix[out_sample].mean(axis=0)

            best_is = int(np.argmax(is_perf))
            # PBO: 样本内最优在样本外排名中位数以下
            rank_oos = np.argsort(np.argsort(-oos_perf))
            if rank_oos[best_is] > N // 2:
                n_overfit += 1

        return n_overfit / n_trials
