"""
样本外验证模块

提供策略回测的样本外验证功能：
- 数据分割：训练集/验证集/测试集
- 滚动窗口验证（Walk-Forward Analysis）
- 交叉验证
- 样本外衰减分析

使用示例：
    validator = WalkForwardValidator(
        data=data,
        train_period=252,
        test_period=63,
        n_splits=4,
    )
    results = validator.run(strategy)
    print(validator.summary())
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Protocol

import logging

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 交易日偏移工具（M-2: 避免使用自然日切分窗口）
# ---------------------------------------------------------------------------

def _offset_by_trading_days(
    base: date,
    n_days: int,
    trading_dates: list[date] | None,
    *,
    direction: int = 1,
) -> date:
    """从 base 偏移 n_days（交易日或自然日）后返回目标日期。

    若 ``trading_dates`` 非 None，则按交易日索引做偏移（更精确）；
    否则回退到 ``timedelta(days=n_days)``（兼容旧行为）。

    Args:
        base: 起始日期
        n_days: 偏移的天数（正整数）
        trading_dates: 已排序的交易日列表（None 表示使用自然日）
        direction: 1=向前，-1=向后
    """
    if trading_dates is None or n_days == 0:
        return base + timedelta(days=n_days * direction)

    import bisect
    idx = bisect.bisect_left(trading_dates, base)
    # 若 base 不在交易日中，idx 指向下一个交易日
    target_idx = idx + n_days * direction
    target_idx = max(0, min(target_idx, len(trading_dates) - 1))
    return trading_dates[target_idx]


@dataclass
class WalkForwardThresholds:
    """Walk-Forward 验收门槛配置

    可从 YAML 配置文件加载，也可直接代码构造。

    Attributes:
        decay_threshold: 最大允许 IS→OOS Sharpe 衰减（默认 50%）
        win_rate_threshold: 最低测试期胜率（默认 50%）
        sharpe_threshold: 最低测试期平均 Sharpe（默认 0）
    """

    decay_threshold: float = 0.50
    win_rate_threshold: float = 0.50
    sharpe_threshold: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> "WalkForwardThresholds":
        """从字典构造"""
        return cls(
            decay_threshold=float(d.get("decay_threshold", 0.50)),
            win_rate_threshold=float(d.get("win_rate_threshold", 0.50)),
            sharpe_threshold=float(d.get("sharpe_threshold", 0.0)),
        )

    @classmethod
    def from_yaml(cls, path: str) -> "WalkForwardThresholds":
        """从 YAML 文件加载

        YAML 示例::

            walk_forward:
              decay_threshold: 0.40
              win_rate_threshold: 0.60
              sharpe_threshold: 0.1
        """
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        wf_cfg = cfg.get("walk_forward", cfg)
        return cls.from_dict(wf_cfg)


@dataclass
class SplitPeriod:
    """数据分割期间"""

    train_start: date
    train_end: date
    test_start: date
    test_end: date

    def __repr__(self) -> str:
        return (
            f"SplitPeriod(train={self.train_start}~{self.train_end}, "
            f"test={self.test_start}~{self.test_end})"
        )


@dataclass
class SplitResult:
    """单次分割验证结果"""

    split_id: int
    period: SplitPeriod

    # 训练集绩效
    train_return: float
    train_sharpe: float
    train_max_drawdown: float

    # 测试集绩效
    test_return: float
    test_sharpe: float
    test_max_drawdown: float

    # 衰减率
    return_decay: float  # (train - test) / train
    sharpe_decay: float


@dataclass
class ValidationReport:
    """样本外验证报告"""

    split_results: list[SplitResult]
    strategy_name: str

    # 汇总统计
    avg_train_return: float
    avg_test_return: float
    avg_train_sharpe: float
    avg_test_sharpe: float
    avg_return_decay: float
    avg_sharpe_decay: float

    # 一致性指标
    test_win_rate: float  # 测试集盈利比例
    decay_consistency: float  # 衰减稳定性（标准差）

    # 折级离散度指标（R94-V13 伪好检测）
    test_sharpe_cv: float = 0.0  # OOS Sharpe 变异系数（CV = std/|mean|）
    outlier_fold_ids: tuple[int, ...] = ()  # SR > 2× 中位数的异常折 ID

    def summary(self) -> str:
        """生成摘要报告"""
        lines = [
            "=" * 60,
            "样本外验证报告",
            f"策略: {self.strategy_name}",
            "=" * 60,
            "",
            f"分割数: {len(self.split_results)}",
            "",
            "【训练集表现】",
            f"  平均收益: {self.avg_train_return:.2%}",
            f"  平均夏普: {self.avg_train_sharpe:.2f}",
            "",
            "【测试集表现】",
            f"  平均收益: {self.avg_test_return:.2%}",
            f"  平均夏普: {self.avg_test_sharpe:.2f}",
            f"  盈利比例: {self.test_win_rate:.2%}",
            "",
            "【样本外衰减】",
            f"  收益衰减: {self.avg_return_decay:.2%}",
            f"  夏普衰减: {self.avg_sharpe_decay:.2%}",
            f"  衰减稳定性: {self.decay_consistency:.4f}",
            "",
            "【折级离散度（R94-V13）】",
            f"  OOS Sharpe CV: {self.test_sharpe_cv:.2f}",
            f"  异常折: {list(self.outlier_fold_ids) if self.outlier_fold_ids else '无'}",
            "",
            "【各期明细】",
        ]

        for r in self.split_results:
            lines.append(
                f"  Split {r.split_id}: "
                f"Train={r.train_return:.2%}/{r.train_sharpe:.2f}, "
                f"Test={r.test_return:.2%}/{r.test_sharpe:.2f}, "
                f"Decay={r.return_decay:.2%}"
            )

        lines.extend(["", "=" * 60])
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """序列化为字典（可直接 JSON 导出）。"""
        return {
            "strategy_name": self.strategy_name,
            "n_splits": len(self.split_results),
            "avg_train_return": self.avg_train_return,
            "avg_test_return": self.avg_test_return,
            "avg_train_sharpe": self.avg_train_sharpe,
            "avg_test_sharpe": self.avg_test_sharpe,
            "avg_return_decay": self.avg_return_decay,
            "avg_sharpe_decay": self.avg_sharpe_decay,
            "test_win_rate": self.test_win_rate,
            "decay_consistency": self.decay_consistency,
            "test_sharpe_cv": self.test_sharpe_cv,
            "outlier_fold_ids": list(self.outlier_fold_ids),
            "splits": [
                {
                    "split_id": r.split_id,
                    "train_return": r.train_return,
                    "train_sharpe": r.train_sharpe,
                    "test_return": r.test_return,
                    "test_sharpe": r.test_sharpe,
                    "return_decay": r.return_decay,
                    "sharpe_decay": r.sharpe_decay,
                }
                for r in self.split_results
            ],
        }


def judge_walk_forward(
    report: ValidationReport,
    decay_threshold: float = 0.50,
    win_rate_threshold: float = 0.50,
    sharpe_threshold: float = 0.0,
    thresholds: WalkForwardThresholds | None = None,
) -> str:
    """根据验收门槛判定 Walk-Forward 结果 PASS/FAIL。

    判定标准：
    - IS→OOS Sharpe 衰减 < decay_threshold（负衰减=OOS 优于 IS，直接通过）
    - 测试期胜率 ≥ win_rate_threshold
    - 测试期平均 Sharpe > sharpe_threshold

    可通过 ``thresholds`` 参数传入 :class:`WalkForwardThresholds`
    配置对象（优先于关键字参数）。

    Args:
        report: Walk-Forward 验证报告
        decay_threshold: 最大允许衰减（默认 50%）
        win_rate_threshold: 最低测试期胜率（默认 50%）
        sharpe_threshold: 最低测试期 Sharpe（默认 0）
        thresholds: 可选的阈值配置对象（覆盖其余参数）

    Returns:
        "PASS" / "FAIL" / "NO_DATA"
    """
    if thresholds is not None:
        decay_threshold = thresholds.decay_threshold
        win_rate_threshold = thresholds.win_rate_threshold
        sharpe_threshold = thresholds.sharpe_threshold
    if not report.split_results:
        return "NO_DATA"

    # 负衰减（OOS > IS）表示没有过拟合，直接通过衰减检查
    decay_ok = (
        report.avg_sharpe_decay <= 0  # OOS 优于 IS
        or report.avg_sharpe_decay < decay_threshold
    )

    checks = [
        decay_ok,
        report.test_win_rate >= win_rate_threshold,
        report.avg_test_sharpe > sharpe_threshold,
    ]

    return "PASS" if all(checks) else "FAIL"


class StrategyRunner(Protocol):
    """策略运行接口"""

    def run(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[str, float]:
        """运行策略并返回绩效指标

        Returns:
            包含 return, sharpe, max_drawdown 的字典
        """
        ...


class DataSplitter(ABC):
    """数据分割器基类"""

    @abstractmethod
    def split(
        self,
        start_date: date,
        end_date: date,
    ) -> list[SplitPeriod]:
        """分割数据期间"""
        ...


class TimeSeriesSplitter(DataSplitter):
    """时间序列分割器（非交叉）"""

    def __init__(
        self,
        train_period_days: int = 252,
        test_period_days: int = 63,
        n_splits: int = 4,
        gap_days: int = 0,
        trading_dates: list[date] | None = None,
    ):
        """初始化分割器

        Args:
            train_period_days: 训练期天数（交易日数，若提供 trading_dates）
            test_period_days: 测试期天数
            n_splits: 分割次数
            gap_days: 训练集与测试集之间的间隔天数（避免前视偏差）
            trading_dates: 已排序的交易日列表（提供后按交易日偏移，否则按自然日）
        """
        self.train_period = train_period_days
        self.test_period = test_period_days
        self.n_splits = n_splits
        self.gap_days = gap_days
        self._trading_dates = trading_dates

    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]:
        """分割数据期间

        Args:
            start_date: 数据起始日期
            end_date: 数据结束日期

        Returns:
            分割期间列表
        """
        td = self._trading_dates

        splits = []
        for i in range(self.n_splits):
            offset = i * self.test_period

            train_start = _offset_by_trading_days(start_date, offset, td)
            train_end = _offset_by_trading_days(train_start, self.train_period - 1, td)
            test_start = _offset_by_trading_days(train_end, self.gap_days + 1, td)
            test_end = _offset_by_trading_days(test_start, self.test_period - 1, td)

            if test_end > end_date:
                break

            splits.append(
                SplitPeriod(
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )

        return splits


class ExpandingWindowSplitter(DataSplitter):
    """扩展窗口分割器"""

    def __init__(
        self,
        initial_train_days: int = 252,
        test_period_days: int = 63,
        n_splits: int = 4,
        gap_days: int = 0,
        trading_dates: list[date] | None = None,
    ):
        self.initial_train = initial_train_days
        self.test_period = test_period_days
        self.n_splits = n_splits
        self.gap_days = gap_days
        self._trading_dates = trading_dates

    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]:
        """分割数据：训练集逐步扩展"""
        td = self._trading_dates
        splits = []

        for i in range(self.n_splits):
            train_start = start_date
            train_days = self.initial_train + i * self.test_period
            train_end = _offset_by_trading_days(start_date, train_days - 1, td)
            test_start = _offset_by_trading_days(train_end, self.gap_days + 1, td)
            test_end = _offset_by_trading_days(test_start, self.test_period - 1, td)

            if test_end > end_date:
                break

            splits.append(
                SplitPeriod(
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )

        return splits


class WalkForwardValidator:
    """Walk-Forward验证器

    实现滚动窗口样本外验证，是量化策略回测的黄金标准。
    """

    def __init__(
        self,
        splitter: DataSplitter | None = None,
        train_period_days: int = 252,
        test_period_days: int = 63,
        n_splits: int = 4,
        gap_days: int = 0,
    ):
        """初始化验证器

        Args:
            splitter: 自定义分割器（如提供则忽略其他参数）
            train_period_days: 训练期天数（默认1年）
            test_period_days: 测试期天数（默认1季度）
            n_splits: 分割次数
            gap_days: 训练与测试间隔
        """
        if splitter:
            self.splitter = splitter
        else:
            self.splitter = TimeSeriesSplitter(
                train_period_days,
                test_period_days,
                n_splits,
                gap_days,
            )

        self.strategy_name = "Strategy"

    def _compute_decay(self, train_value: float, test_value: float) -> float:
        """计算衰减率（IS→OOS 的相对变化，已裁剪到 [-2, 2]）。(H6 fix)

        - 当 IS 值极度接近零（|train_value| < 1e-6）时，改用符号比较，
          避免数值除零：OOS ≥ 0 → 0.0，OOS < 0 → 1.0
        - 对于较小但有意义的 IS 值（如 0.02-0.05），正常计算衰减率，
          再裁剪到 [-2, 2] 防止 IS 极小值导致数值爆炸（如 66x）
        """
        if abs(train_value) < 1e-6:
            # IS 值极接近零（除零风险）：用正负符号判断
            return 0.0 if test_value >= 0 else 1.0
        raw = (train_value - test_value) / abs(train_value)
        return float(max(-2.0, min(2.0, raw)))

    def validate(
        self,
        runner: StrategyRunner,
        start_date: date,
        end_date: date,
        strategy_name: str = "Strategy",
    ) -> ValidationReport:
        """运行样本外验证

        Args:
            runner: 策略运行器
            start_date: 数据起始期
            end_date: 数据结束期
            strategy_name: 策略名称

        Returns:
            验证报告
        """
        self.strategy_name = strategy_name
        splits = self.splitter.split(start_date, end_date)

        results = []
        for i, period in enumerate(splits):
            # 运行训练期
            train_metrics = runner.run(period.train_start, period.train_end)

            # 运行测试期
            test_metrics = runner.run(period.test_start, period.test_end)

            # 计算衰减
            return_decay = self._compute_decay(
                train_metrics.get("return", 0),
                test_metrics.get("return", 0),
            )
            sharpe_decay = self._compute_decay(
                train_metrics.get("sharpe", 0),
                test_metrics.get("sharpe", 0),
            )

            results.append(
                SplitResult(
                    split_id=i + 1,
                    period=period,
                    train_return=train_metrics.get("return", 0),
                    train_sharpe=train_metrics.get("sharpe", 0),
                    train_max_drawdown=train_metrics.get("max_drawdown", 0),
                    test_return=test_metrics.get("return", 0),
                    test_sharpe=test_metrics.get("sharpe", 0),
                    test_max_drawdown=test_metrics.get("max_drawdown", 0),
                    return_decay=return_decay,
                    sharpe_decay=sharpe_decay,
                )
            )

        return self._generate_report(results, strategy_name)

    def validate_with_callable(
        self,
        run_fn: Callable[[date, date], dict[str, float]],
        start_date: date,
        end_date: date,
        strategy_name: str = "Strategy",
    ) -> ValidationReport:
        """使用可调用对象运行验证（便捷接口）

        Args:
            run_fn: 运行函数 (start_date, end_date) -> metrics
            start_date: 起始日期
            end_date: 结束日期
            strategy_name: 策略名称

        Returns:
            验证报告
        """

        class CallableRunner:
            """将可调用对象包装为具有 run 方法的运行器。"""

            def __init__(self, fn: Callable):
                self.fn = fn

            def run(self, start: date, end: date) -> dict[str, float]:
                """在指定日期范围内运行策略并返回指标字典。"""
                return self.fn(start, end)

        return self.validate(CallableRunner(run_fn), start_date, end_date, strategy_name)

    def _generate_report(
        self,
        results: list[SplitResult],
        strategy_name: str,
    ) -> ValidationReport:
        """生成验证报告"""
        if not results:
            return ValidationReport(
                split_results=[],
                strategy_name=strategy_name,
                avg_train_return=0,
                avg_test_return=0,
                avg_train_sharpe=0,
                avg_test_sharpe=0,
                avg_return_decay=0,
                avg_sharpe_decay=0,
                test_win_rate=0,
                decay_consistency=0,
            )

        train_returns = [r.train_return for r in results]
        test_returns = [r.test_return for r in results]
        train_sharpes = [r.train_sharpe for r in results]
        test_sharpes = [r.test_sharpe for r in results]
        return_decays = [r.return_decay for r in results]
        sharpe_decays = [r.sharpe_decay for r in results]

        test_win_rate = sum(1 for r in test_returns if r > 0) / len(test_returns)
        decay_consistency = float(np.std(return_decays)) if len(return_decays) > 1 else 0

        # R94-V13: 折级 OOS Sharpe 离散度 + 异常折检测
        ts_std = float(np.std(test_sharpes)) if len(test_sharpes) > 1 else 0.0
        ts_mean = float(np.mean(test_sharpes))
        test_sharpe_cv = ts_std / abs(ts_mean) if abs(ts_mean) > 1e-8 else 0.0

        ts_median = float(np.median(test_sharpes))
        outlier_ids: list[int] = []
        if abs(ts_median) > 1e-8:
            for r in results:
                if r.test_sharpe > 2.0 * abs(ts_median):
                    outlier_ids.append(r.split_id)

        return ValidationReport(
            split_results=results,
            strategy_name=strategy_name,
            avg_train_return=float(np.mean(train_returns)),
            avg_test_return=float(np.mean(test_returns)),
            avg_train_sharpe=float(np.mean(train_sharpes)),
            avg_test_sharpe=float(np.mean(test_sharpes)),
            avg_return_decay=float(np.mean(return_decays)),
            # 使用中位数而非均值，对单折极端衰减值具有鲁棒性（H6 fix）
            avg_sharpe_decay=float(np.median(sharpe_decays)) if sharpe_decays else 0.0,
            test_win_rate=test_win_rate,
            decay_consistency=decay_consistency,
            test_sharpe_cv=round(test_sharpe_cv, 4),
            outlier_fold_ids=tuple(outlier_ids),
        )


class PurgedWalkForwardSplitter(DataSplitter):
    """Purged Walk-Forward 分割器。

    在标准 Walk-Forward 基础上增加 purge（清洗）和 embargo（禁用）机制：
    - purge_days: 训练集末尾删除的天数，避免训练/测试边界处的信息泄露
      （例如策略使用 lookback 窗口 → 训练集最后 N 天的特征可能依赖测试期数据）
    - embargo_days: 测试集之后需要跳过的天数，避免测试集结果影响下一折训练集

    参考: Marcos López de Prado, "Advances in Financial Machine Learning", Ch.7

    时间线:
        |-- train --|purge|-- test --|embargo|-- next train --|...

    Args:
        train_period_days: 原始训练期天数（purge 从末尾削减）
        test_period_days: 测试期天数
        n_splits: 分割次数
        purge_days: 训练集末尾清洗天数
        embargo_days: 测试集后禁用天数（下一折训练集跳过）
    """

    def __init__(
        self,
        train_period_days: int = 252,
        test_period_days: int = 63,
        n_splits: int = 4,
        purge_days: int = 5,
        embargo_days: int = 2,
        trading_dates: list[date] | None = None,
    ):
        self.train_period = train_period_days
        self.test_period = test_period_days
        self.n_splits = n_splits
        self.purge_days = purge_days
        self.embargo_days = embargo_days
        self._trading_dates = trading_dates

    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]:
        """分割数据期间（含 purge 和 embargo）。

        训练集实际结束日 = 原始结束日 - purge_days
        测试集起始日 = 原始结束日 + 1
        下一折偏移 = test_period + embargo_days

        Args:
            start_date: 数据起始日期
            end_date: 数据结束日期

        Returns:
            分割期间列表
        """
        td = self._trading_dates
        splits: list[SplitPeriod] = []
        step = self.test_period + self.embargo_days

        for i in range(self.n_splits):
            offset = i * step

            train_start = _offset_by_trading_days(start_date, offset, td)
            raw_train_end = _offset_by_trading_days(
                train_start, self.train_period - 1, td,
            )
            effective_train_end = _offset_by_trading_days(
                raw_train_end, self.purge_days, td, direction=-1,
            )

            if effective_train_end <= train_start:
                continue

            test_start = _offset_by_trading_days(raw_train_end, 1, td)
            test_end = _offset_by_trading_days(test_start, self.test_period - 1, td)

            if test_end > end_date:
                break

            splits.append(
                SplitPeriod(
                    train_start=train_start,
                    train_end=effective_train_end,
                    test_start=test_start,
                    test_end=test_end,
                )
            )

        return splits


class CrossValidator:
    """交叉验证器

    注意：时间序列交叉验证需要保持时间顺序，不能像普通CV那样随机分割。
    此实现使用时间序列兼容的K-Fold方法。
    """

    def __init__(
        self,
        n_folds: int = 5,
        gap_days: int = 0,
        trading_dates: list[date] | None = None,
    ):
        """初始化交叉验证器

        Args:
            n_folds: 折数
            gap_days: 样本间隔
            trading_dates: 已排序的交易日列表（提供后按交易日偏移）
        """
        self.n_folds = n_folds
        self.gap_days = gap_days
        self._trading_dates = trading_dates

    def split(self, start_date: date, end_date: date) -> list[SplitPeriod]:
        """分割数据为K折"""
        td = self._trading_dates
        if td is not None:
            # 按交易日数计算 fold 大小
            import bisect
            i_start = bisect.bisect_left(td, start_date)
            i_end = bisect.bisect_right(td, end_date)
            total_days = i_end - i_start
        else:
            total_days = (end_date - start_date).days
        fold_days = total_days // self.n_folds

        splits = []
        for i in range(self.n_folds):
            # 测试折
            test_start = _offset_by_trading_days(start_date, i * fold_days, td)
            test_end = _offset_by_trading_days(start_date, (i + 1) * fold_days - 1, td)

            # 训练折：测试折之前的所有数据
            if i == 0:
                # 第一折没有历史数据，跳过
                continue

            train_start = start_date
            train_end = _offset_by_trading_days(test_start, self.gap_days + 1, td, direction=-1)

            if train_end <= train_start:
                continue

            splits.append(
                SplitPeriod(
                    train_start=train_start,
                    train_end=train_end,
                    test_start=test_start,
                    test_end=min(test_end, end_date),
                )
            )

        return splits


def walk_forward_validate(
    run_fn: Callable[[date, date], dict[str, float]],
    start_date: date,
    end_date: date,
    train_days: int = 252,
    test_days: int = 63,
    n_splits: int = 4,
    strategy_name: str = "Strategy",
) -> ValidationReport:
    """便捷函数：执行Walk-Forward验证

    Args:
        run_fn: 策略运行函数
        start_date: 起始日期
        end_date: 结束日期
        train_days: 训练期天数
        test_days: 测试期天数
        n_splits: 分割次数
        strategy_name: 策略名称

    Returns:
        验证报告
    """
    validator = WalkForwardValidator(
        train_period_days=train_days,
        test_period_days=test_days,
        n_splits=n_splits,
    )
    return validator.validate_with_callable(run_fn, start_date, end_date, strategy_name)


def purged_walk_forward_validate(
    run_fn: Callable[[date, date], dict[str, float]],
    start_date: date,
    end_date: date,
    train_days: int = 252,
    test_days: int = 63,
    n_splits: int = 4,
    purge_days: int = 5,
    embargo_days: int = 2,
    strategy_name: str = "Strategy",
) -> ValidationReport:
    """便捷函数：执行 Purged Walk-Forward 验证。

    在标准 Walk-Forward 基础上增加 purge 和 embargo，
    减少训练/测试边界处的信息泄露风险。

    Args:
        run_fn: 策略运行函数 ``(start_date, end_date) -> metrics_dict``
        start_date: 起始日期
        end_date: 结束日期
        train_days: 训练期天数
        test_days: 测试期天数
        n_splits: 分割次数
        purge_days: 训练集末尾清洗天数
        embargo_days: 测试集后禁用天数
        strategy_name: 策略名称

    Returns:
        ValidationReport
    """
    splitter = PurgedWalkForwardSplitter(
        train_period_days=train_days,
        test_period_days=test_days,
        n_splits=n_splits,
        purge_days=purge_days,
        embargo_days=embargo_days,
    )
    validator = WalkForwardValidator(splitter=splitter)
    return validator.validate_with_callable(
        run_fn, start_date, end_date, strategy_name
    )


# ============================================================================
# 综合评分与序列化
# ============================================================================


def compute_wf_score(
    report: ValidationReport,
    *,
    w_decay: float = 0.30,
    w_win_rate: float = 0.25,
    w_sharpe: float = 0.25,
    w_consistency: float = 0.20,
) -> float:
    """将 Walk-Forward 报告量化为 0~10 综合评分。

    融合 4 个维度（默认权重可覆盖）：

    1. **衰减维度** (w_decay=30%)
       - decay ≤ 0（OOS 优于 IS）→ 满分 10
       - decay ≥ 1.0→ 0 分
       - 线性插值

    2. **胜率维度** (w_win_rate=25%)
       - win_rate ≥ 0.8 → 10
       - win_rate = 0.0 → 0
       - 线性

    3. **Sharpe 维度** (w_sharpe=25%)
       - avg_test_sharpe ≥ 2.0 → 10
       - avg_test_sharpe ≤ 0 → 0
       - 线性

    4. **一致性维度** (w_consistency=20%)
       - decay_consistency ≤ 0.05 → 10
       - decay_consistency ≥ 0.5 → 0
       - 线性

    Args:
        report: Walk-Forward 验证报告
        w_decay: 衰减权重
        w_win_rate: 胜率权重
        w_sharpe: Sharpe 权重
        w_consistency: 一致性权重

    Returns:
        0.0~10.0 综合评分
    """
    if not report.split_results:
        return 0.0

    # 1. 衰减评分
    decay = report.avg_sharpe_decay
    if decay <= 0:
        decay_score = 10.0
    elif decay >= 1.0:
        decay_score = 0.0
    else:
        decay_score = 10.0 * (1.0 - decay)

    # 2. 胜率评分
    wr = report.test_win_rate
    wr_score = min(10.0, max(0.0, wr / 0.8 * 10.0))

    # 3. Sharpe 评分
    sharpe = report.avg_test_sharpe
    if sharpe <= 0:
        sharpe_score = 0.0
    elif sharpe >= 2.0:
        sharpe_score = 10.0
    else:
        sharpe_score = sharpe / 2.0 * 10.0

    # 4. 一致性评分
    cons = report.decay_consistency
    if cons <= 0.05:
        cons_score = 10.0
    elif cons >= 0.5:
        cons_score = 0.0
    else:
        cons_score = 10.0 * (0.5 - cons) / 0.45

    # 加权
    total = w_decay + w_win_rate + w_sharpe + w_consistency
    score = (
        w_decay * decay_score
        + w_win_rate * wr_score
        + w_sharpe * sharpe_score
        + w_consistency * cons_score
    ) / total

    return round(min(10.0, max(0.0, score)), 2)


def auto_n_splits(
    total_days: int,
    train_days: int,
    test_days: int,
    gap_days: int = 0,
) -> int:
    """根据总数据长度、IS/OOS 窗口大小自动计算最大分折数。

    公式：
        n_splits = floor((total_days - train_days) / (test_days + gap_days))

    至少返回 1。

    Args:
        total_days: 总可用数据天数
        train_days: 训练期天数
        test_days: 测试期天数
        gap_days: 间隔天数

    Returns:
        最大可分折数（≥ 1）
    """
    if total_days <= train_days:
        return 1
    step = test_days + gap_days
    if step <= 0:
        return 1
    n = (total_days - train_days) // step
    return max(1, n)
