"""回测验证框架 — Walk-Forward / 交叉验证 / Purged-CV"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Protocol

import numpy as np

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class WalkForwardThresholds:
    decay_threshold: float = 0.5
    win_rate_threshold: float = 0.5
    sharpe_threshold: float = 0.3


@dataclass
class SplitPeriod:
    train_start: date
    train_end: date
    test_start: date
    test_end: date


@dataclass
class SplitResult:
    split_id: int
    period: SplitPeriod
    train_return: float = 0.0
    train_sharpe: float = 0.0
    train_max_drawdown: float = 0.0
    test_return: float = 0.0
    test_sharpe: float = 0.0
    test_max_drawdown: float = 0.0
    return_decay: float = 0.0
    sharpe_decay: float = 0.0


@dataclass
class ValidationReport:
    split_results: list[SplitResult] = field(default_factory=list)
    strategy_name: str = ""
    avg_train_return: float = 0.0
    avg_test_return: float = 0.0
    avg_train_sharpe: float = 0.0
    avg_test_sharpe: float = 0.0
    avg_return_decay: float = 0.0
    avg_sharpe_decay: float = 0.0
    test_win_rate: float = 0.0
    decay_consistency: float = 0.0
    test_sharpe_cv: float = 0.0
    outlier_fold_ids: list[int] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            "=" * 60,
            f"Walk-Forward 验证: {self.strategy_name}",
            f"  折数: {len(self.split_results)}",
            f"  训练: 均收益={self.avg_train_return:.2%} Sharpe={self.avg_train_sharpe:.2f}",
            f"  测试: 均收益={self.avg_test_return:.2%} Sharpe={self.avg_test_sharpe:.2f}",
            f"  衰减: 收益={self.avg_return_decay:.2%} Sharpe={self.avg_sharpe_decay:.2%}",
            f"  测试胜率: {self.test_win_rate:.1%}  一致性: {self.decay_consistency:.2f}",
            "=" * 60,
        ]
        return "\n".join(lines)


# ============================================================================
# Protocol & ABC
# ============================================================================

class StrategyRunner(Protocol):
    def run(self, start_date: date, end_date: date) -> dict[str, float]: ...


class DataSplitter(ABC):
    @abstractmethod
    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]: ...


# ============================================================================
# 分割器实现
# ============================================================================

def _offset_days(base: date, n: int, direction: int = 1) -> date:
    return base + timedelta(days=n * direction)


class TimeSeriesSplitter(DataSplitter):
    """滚动窗口时序分割"""

    def __init__(self, train_period_days: int = 252, test_period_days: int = 63,
                 n_splits: int = 5, gap_days: int = 0):
        self.train_days = train_period_days
        self.test_days = test_period_days
        self.n_splits = n_splits
        self.gap = gap_days

    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]:
        total = (end_date - start_date).days
        step = self.train_days + self.test_days + self.gap
        if step <= 0:
            return []
        n = min(self.n_splits, total // step)
        # 从后往前排布
        periods = []
        for i in range(n):
            test_end = end_date - timedelta(days=i * step)
            test_start = test_end - timedelta(days=self.test_days)
            train_end = test_start - timedelta(days=self.gap)
            train_start = train_end - timedelta(days=self.train_days)
            if train_start < start_date:
                break
            periods.append(SplitPeriod(train_start, train_end, test_start, test_end))
        return list(reversed(periods))


class ExpandingWindowSplitter(DataSplitter):
    """扩展窗口分割"""

    def __init__(self, initial_train_days: int = 252, test_period_days: int = 63,
                 n_splits: int = 5, gap_days: int = 0):
        self.init_train = initial_train_days
        self.test_days = test_period_days
        self.n_splits = n_splits
        self.gap = gap_days

    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]:
        periods = []
        for i in range(self.n_splits):
            train_start = start_date
            train_end = start_date + timedelta(days=self.init_train + i * self.test_days)
            test_start = train_end + timedelta(days=self.gap)
            test_end = test_start + timedelta(days=self.test_days)
            if test_end > end_date:
                break
            periods.append(SplitPeriod(train_start, train_end, test_start, test_end))
        return periods


class PurgedWalkForwardSplitter(DataSplitter):
    """Purged Walk-Forward (purge + embargo 防止信息泄露)"""

    def __init__(self, train_period_days: int = 252, test_period_days: int = 63,
                 n_splits: int = 5, purge_days: int = 5, embargo_days: int = 5):
        self.train_days = train_period_days
        self.test_days = test_period_days
        self.n_splits = n_splits
        self.purge = purge_days
        self.embargo = embargo_days

    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]:
        gap = self.purge + self.embargo
        step = self.train_days + self.test_days + gap
        periods = []
        for i in range(self.n_splits):
            test_end = end_date - timedelta(days=i * step)
            test_start = test_end - timedelta(days=self.test_days)
            train_end = test_start - timedelta(days=gap)
            train_start = train_end - timedelta(days=self.train_days)
            if train_start < start_date:
                break
            periods.append(SplitPeriod(train_start, train_end, test_start, test_end))
        return list(reversed(periods))


class CrossValidator:
    """K-Fold 交叉验证"""

    def __init__(self, n_folds: int = 5, gap_days: int = 0):
        self.n_folds = n_folds
        self.gap = gap_days

    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]:
        total = (end_date - start_date).days
        fold_size = total // self.n_folds
        periods = []
        for i in range(self.n_folds):
            test_start = start_date + timedelta(days=i * fold_size)
            test_end = test_start + timedelta(days=fold_size)
            train_start = start_date
            train_end = test_start - timedelta(days=self.gap)
            if train_end <= train_start:
                continue
            periods.append(SplitPeriod(train_start, train_end,
                                       test_start, min(test_end, end_date)))
        return periods


# ============================================================================
# 验证器
# ============================================================================

class WalkForwardValidator:
    """Walk-Forward 验证器"""

    def __init__(self, splitter: DataSplitter | None = None,
                 train_period_days: int = 252, test_period_days: int = 63,
                 n_splits: int = 5, gap_days: int = 0):
        self.splitter = splitter or TimeSeriesSplitter(
            train_period_days, test_period_days, n_splits, gap_days)

    def validate(self, runner: StrategyRunner, start_date: date, end_date: date,
                 strategy_name: str = "") -> ValidationReport:
        return self.validate_with_callable(
            runner.run, start_date, end_date, strategy_name)

    def validate_with_callable(
        self, run_fn: Callable[[date, date], dict[str, float]],
        start_date: date, end_date: date, strategy_name: str = "",
    ) -> ValidationReport:
        splits = self.splitter.split(start_date, end_date)
        results = []
        for i, sp in enumerate(splits):
            try:
                train = run_fn(sp.train_start, sp.train_end)
                test = run_fn(sp.test_start, sp.test_end)
            except Exception as e:
                logger.warning("Fold %d 失败: %s", i, e)
                continue

            tr = train.get("total_return", 0)
            ts_r = test.get("total_return", 0)
            t_sharpe = train.get("sharpe_ratio", train.get("sharpe", 0))
            ts_sharpe = test.get("sharpe_ratio", test.get("sharpe", 0))
            rd = self._decay(tr, ts_r)
            sd = self._decay(t_sharpe, ts_sharpe)

            results.append(SplitResult(
                split_id=i, period=sp,
                train_return=tr, train_sharpe=t_sharpe,
                train_max_drawdown=train.get("max_drawdown", 0),
                test_return=ts_r, test_sharpe=ts_sharpe,
                test_max_drawdown=test.get("max_drawdown", 0),
                return_decay=rd, sharpe_decay=sd,
            ))

        return self._build_report(results, strategy_name)

    @staticmethod
    def _decay(train_val: float, test_val: float) -> float:
        if abs(train_val) < 1e-10:
            return 0.0
        return (train_val - test_val) / abs(train_val)

    @staticmethod
    def _build_report(results: list[SplitResult], name: str) -> ValidationReport:
        if not results:
            return ValidationReport(strategy_name=name)
        n = len(results)
        rpt = ValidationReport(
            split_results=results, strategy_name=name,
            avg_train_return=sum(r.train_return for r in results) / n,
            avg_test_return=sum(r.test_return for r in results) / n,
            avg_train_sharpe=sum(r.train_sharpe for r in results) / n,
            avg_test_sharpe=sum(r.test_sharpe for r in results) / n,
            avg_return_decay=sum(r.return_decay for r in results) / n,
            avg_sharpe_decay=sum(r.sharpe_decay for r in results) / n,
            test_win_rate=sum(1 for r in results if r.test_return > 0) / n,
        )
        # CV of test sharpe
        test_sharpes = [r.test_sharpe for r in results]
        mean_s = np.mean(test_sharpes)
        std_s = np.std(test_sharpes, ddof=1) if n > 1 else 0
        rpt.test_sharpe_cv = std_s / abs(mean_s) if abs(mean_s) > 1e-10 else 0
        # Decay consistency
        decays = [r.return_decay for r in results]
        rpt.decay_consistency = 1 - (np.std(decays) if len(decays) > 1 else 0)
        return rpt


# ============================================================================
# 便捷函数
# ============================================================================

def walk_forward_validate(
    run_fn: Callable[[date, date], dict[str, float]],
    start_date: date, end_date: date,
    train_days: int = 252, test_days: int = 63,
    n_splits: int = 5, strategy_name: str = "",
) -> ValidationReport:
    v = WalkForwardValidator(train_period_days=train_days, test_period_days=test_days, n_splits=n_splits)
    return v.validate_with_callable(run_fn, start_date, end_date, strategy_name)


def purged_walk_forward_validate(
    run_fn: Callable[[date, date], dict[str, float]],
    start_date: date, end_date: date,
    train_days: int = 252, test_days: int = 63,
    n_splits: int = 5, purge_days: int = 5, embargo_days: int = 5,
    strategy_name: str = "",
) -> ValidationReport:
    splitter = PurgedWalkForwardSplitter(train_days, test_days, n_splits, purge_days, embargo_days)
    v = WalkForwardValidator(splitter=splitter)
    return v.validate_with_callable(run_fn, start_date, end_date, strategy_name)


def judge_walk_forward(
    report: ValidationReport,
    thresholds: WalkForwardThresholds | None = None,
) -> str:
    th = thresholds or WalkForwardThresholds()
    if report.avg_return_decay > th.decay_threshold:
        return "FAIL: 过拟合 — 衰减过大"
    if report.test_win_rate < th.win_rate_threshold:
        return "FAIL: 测试期胜率不足"
    if report.avg_test_sharpe < th.sharpe_threshold:
        return "WEAK: 测试期 Sharpe 偏低"
    return "PASS"


def compute_wf_score(
    report: ValidationReport,
    w_decay: float = 0.3, w_win_rate: float = 0.3,
    w_sharpe: float = 0.2, w_consistency: float = 0.2,
) -> float:
    decay_score = max(0, 1 - report.avg_return_decay)
    return (w_decay * decay_score + w_win_rate * report.test_win_rate +
            w_sharpe * min(1, max(0, report.avg_test_sharpe)) +
            w_consistency * report.decay_consistency)


def auto_n_splits(total_days: int, train_days: int, test_days: int, gap_days: int = 0) -> int:
    step = train_days + test_days + gap_days
    return max(1, total_days // step)
