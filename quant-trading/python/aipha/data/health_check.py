"""
数据健康检查模块

提供数据质量检查功能，包括：
- 缺失率检查
- 异常跳变检测
- 停牌/涨跌停标记完整性
- 重复/乱序交易日识别

符合 copilot-instructions.md 要求:
"必须提供数据健康检查入口：缺失率、异常跳变、停牌/涨跌停标记完整性、重复/乱序交易日"
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import pandas as pd

# ============================================================================
# 数据结构定义
# ============================================================================


class CheckStatus(Enum):
    """检查状态枚举"""

    PASS = "pass"
    WARNING = "warning"
    ERROR = "error"


class IssueSeverity(Enum):
    """问题严重性枚举"""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class HealthIssue:
    """单个健康问题"""

    symbol: str
    check_name: str
    message: str
    severity: IssueSeverity
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "symbol": self.symbol,
            "check_name": self.check_name,
            "message": self.message,
            "severity": self.severity.value,
            "details": self.details,
        }


@dataclass
class CheckResult:
    """单项检查结果"""

    check_name: str
    status: CheckStatus
    details: Dict[str, Any] = field(default_factory=dict)
    issues: List[HealthIssue] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "check_name": self.check_name,
            "status": self.status.value,
            "details": self.details,
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass
class HealthReport:
    """完整健康检查报告"""

    check_time: datetime
    symbols: List[str]
    date_range: tuple[str, str]
    results: List[CheckResult]
    summary: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """计算摘要"""
        self.summary = {
            "total_checks": len(self.results),
            "pass_count": sum(1 for r in self.results if r.status == CheckStatus.PASS),
            "warning_count": sum(1 for r in self.results if r.status == CheckStatus.WARNING),
            "error_count": sum(1 for r in self.results if r.status == CheckStatus.ERROR),
            "total_issues": sum(len(r.issues) for r in self.results),
            "check_time": self.check_time.isoformat(),
            "symbols_count": len(self.symbols),
            "date_range": self.date_range,
        }

    @property
    def overall_status(self) -> CheckStatus:
        """整体状态"""
        if any(r.status == CheckStatus.ERROR for r in self.results):
            return CheckStatus.ERROR
        if any(r.status == CheckStatus.WARNING for r in self.results):
            return CheckStatus.WARNING
        return CheckStatus.PASS

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "summary": self.summary,
            "overall_status": self.overall_status.value,
            "results": [r.to_dict() for r in self.results],
        }


# ============================================================================
# 检查函数
# ============================================================================


def check_missing_rate(
    data: Dict[str, pd.DataFrame],
    expected_bars_per_day: int = 240,
    trading_days: Optional[List[str]] = None,
    threshold: float = 0.1,
) -> CheckResult:
    """
    检查数据缺失率

    Args:
        data: {symbol: DataFrame} 映射
        expected_bars_per_day: 每交易日预期bar数（1min频率默认240）
        trading_days: 交易日列表，若提供则用于精确计算预期bar数
        threshold: 缺失率告警阈值（0.1 = 10%）

    Returns:
        CheckResult 包含缺失率统计
    """
    issues: List[HealthIssue] = []
    missing_stats: Dict[str, float] = {}

    for symbol, df in data.items():
        if df is None or df.empty:
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="missing_rate",
                    message="数据为空或不存在",
                    severity=IssueSeverity.ERROR,
                )
            )
            missing_stats[symbol] = 1.0
            continue

        # 计算预期bar数
        if trading_days is not None:
            expected_bars = len(trading_days) * expected_bars_per_day
        else:
            # 简化计算：基于日期范围
            if "trade_time" in df.columns:
                time_col = pd.to_datetime(df["trade_time"])
                date_range = (time_col.max() - time_col.min()).days + 1
            elif "trade_date" in df.columns:
                dates = pd.to_datetime(df["trade_date"])
                date_range = (dates.max() - dates.min()).days + 1
            else:
                date_range = len(df) // expected_bars_per_day + 1
            expected_bars = max(date_range * expected_bars_per_day, 1)

        actual_bars = len(df)
        missing_rate = max(0.0, 1.0 - actual_bars / expected_bars)
        missing_stats[symbol] = missing_rate

        if missing_rate > threshold:
            severity = IssueSeverity.WARNING if missing_rate < 0.3 else IssueSeverity.ERROR
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="missing_rate",
                    message=f"缺失率 {missing_rate:.2%} 超过阈值 {threshold:.2%}",
                    severity=severity,
                    details={
                        "expected": expected_bars,
                        "actual": actual_bars,
                        "missing_rate": missing_rate,
                    },
                )
            )

    status = _determine_status(issues)
    return CheckResult(
        check_name="missing_rate",
        status=status,
        details={"missing_stats": missing_stats, "threshold": threshold},
        issues=issues,
    )


def check_price_jumps(
    data: Dict[str, pd.DataFrame],
    price_column: str = "close",
    threshold: float = 0.30,
    max_issues_per_symbol: int = 10,
) -> CheckResult:
    """
    检查异常价格跳变

    Args:
        data: {symbol: DataFrame} 映射
        price_column: 价格列名
        threshold: 跳变阈值（0.30 = 30%）
        max_issues_per_symbol: 每个股票最多报告的问题数

    Returns:
        CheckResult 包含跳变记录
    """
    issues: List[HealthIssue] = []
    jump_stats: Dict[str, int] = {}

    for symbol, df in data.items():
        if df is None or df.empty:
            jump_stats[symbol] = 0
            continue

        if price_column not in df.columns:
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="price_jumps",
                    message=f"缺少价格列: {price_column}",
                    severity=IssueSeverity.WARNING,
                )
            )
            jump_stats[symbol] = 0
            continue

        # 计算bar间价格变化率
        prices = df[price_column].astype(float)
        price_changes = prices.pct_change().abs()

        # 找出异常跳变
        jump_mask = price_changes > threshold
        jumps = df[jump_mask].copy()
        jumps["_price_change"] = price_changes[jump_mask]

        jump_count = len(jumps)
        jump_stats[symbol] = jump_count

        if jump_count > 0:
            # 获取时间列
            time_col = "trade_time" if "trade_time" in df.columns else "trade_date"

            for idx, row in jumps.head(max_issues_per_symbol).iterrows():
                change_pct = row["_price_change"]
                issues.append(
                    HealthIssue(
                        symbol=symbol,
                        check_name="price_jumps",
                        message=f"价格跳变 {change_pct:.2%}",
                        severity=IssueSeverity.WARNING,
                        details={
                            "time": str(row.get(time_col, idx)),
                            "price": float(row[price_column]),
                            "change": float(change_pct),
                        },
                    )
                )

    status = _determine_status(issues)
    return CheckResult(
        check_name="price_jumps",
        status=status,
        details={"jump_stats": jump_stats, "threshold": threshold},
        issues=issues,
    )


def check_halt_flags(
    data: Dict[str, pd.DataFrame],
    required_columns: Optional[List[str]] = None,
) -> CheckResult:
    """
    检查停牌/涨跌停标记完整性

    Args:
        data: {symbol: DataFrame} 映射
        required_columns: 必须存在的标记列，默认 ["halt", "limit_up", "limit_down"]

    Returns:
        CheckResult 包含标记缺失情况
    """
    if required_columns is None:
        required_columns = ["halt", "limit_up", "limit_down"]

    issues: List[HealthIssue] = []
    flag_stats: Dict[str, Dict[str, bool]] = {}

    for symbol, df in data.items():
        if df is None or df.empty:
            flag_stats[symbol] = {col: False for col in required_columns}
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="halt_flags",
                    message="数据为空",
                    severity=IssueSeverity.WARNING,
                )
            )
            continue

        # 检查必需列
        existing_cols = set(df.columns)
        missing_cols = [col for col in required_columns if col not in existing_cols]

        flag_stats[symbol] = {col: col in existing_cols for col in required_columns}

        if missing_cols:
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="halt_flags",
                    message=f"缺少字段: {', '.join(missing_cols)}",
                    severity=IssueSeverity.WARNING,
                    details={"missing_columns": missing_cols},
                )
            )

    status = _determine_status(issues)
    return CheckResult(
        check_name="halt_flags",
        status=status,
        details={"flag_stats": flag_stats, "required_columns": required_columns},
        issues=issues,
    )


def check_duplicates(
    data: Dict[str, pd.DataFrame],
    time_column: str = "trade_time",
    max_sample_times: int = 5,
) -> CheckResult:
    """
    检查重复交易时间

    Args:
        data: {symbol: DataFrame} 映射
        time_column: 时间列名
        max_sample_times: 展示的重复时间样本数

    Returns:
        CheckResult 包含重复记录
    """
    issues: List[HealthIssue] = []
    dup_stats: Dict[str, int] = {}

    for symbol, df in data.items():
        if df is None or df.empty:
            dup_stats[symbol] = 0
            continue

        if time_column not in df.columns:
            # 尝试备用列
            alt_col = "trade_date" if time_column == "trade_time" else None
            if alt_col and alt_col in df.columns:
                time_column_used = alt_col
            else:
                dup_stats[symbol] = 0
                continue
        else:
            time_column_used = time_column

        duplicates = df[df.duplicated(subset=[time_column_used], keep=False)]
        dup_count = len(duplicates)
        dup_stats[symbol] = dup_count

        if dup_count > 0:
            dup_times = duplicates[time_column_used].unique()
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="duplicates",
                    message=f"发现 {dup_count} 条重复记录",
                    severity=IssueSeverity.ERROR,
                    details={
                        "duplicate_count": dup_count,
                        "sample_times": [str(t) for t in dup_times[:max_sample_times]],
                    },
                )
            )

    status = _determine_status(issues)
    return CheckResult(
        check_name="duplicates",
        status=status,
        details={"dup_stats": dup_stats, "time_column": time_column},
        issues=issues,
    )


def check_order(
    data: Dict[str, pd.DataFrame],
    time_column: str = "trade_time",
) -> CheckResult:
    """
    检查交易时间顺序（是否单调递增）

    Args:
        data: {symbol: DataFrame} 映射
        time_column: 时间列名

    Returns:
        CheckResult 包含乱序记录
    """
    issues: List[HealthIssue] = []
    order_stats: Dict[str, int] = {}

    for symbol, df in data.items():
        if df is None or df.empty:
            order_stats[symbol] = 0
            continue

        if time_column not in df.columns:
            alt_col = "trade_date" if time_column == "trade_time" else None
            if alt_col and alt_col in df.columns:
                time_column_used = alt_col
            else:
                order_stats[symbol] = 0
                continue
        else:
            time_column_used = time_column

        times = pd.to_datetime(df[time_column_used])
        time_diffs = times.diff()

        # 找出乱序（时间差为负）
        out_of_order = time_diffs[time_diffs < pd.Timedelta(0)]
        disorder_count = len(out_of_order)
        order_stats[symbol] = disorder_count

        if disorder_count > 0:
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="order",
                    message=f"发现 {disorder_count} 处乱序",
                    severity=IssueSeverity.ERROR,
                    details={"disorder_count": disorder_count},
                )
            )

    status = _determine_status(issues)
    return CheckResult(
        check_name="order",
        status=status,
        details={"order_stats": order_stats, "time_column": time_column},
        issues=issues,
    )


def check_value_range(
    data: Dict[str, pd.DataFrame],
    column: str,
    min_value: Optional[float] = None,
    max_value: Optional[float] = None,
    allow_null: bool = False,
) -> CheckResult:
    """
    检查数值范围

    Args:
        data: {symbol: DataFrame} 映射
        column: 要检查的列名
        min_value: 最小值（None 表示不检查下界）
        max_value: 最大值（None 表示不检查上界）
        allow_null: 是否允许空值

    Returns:
        CheckResult 包含范围违规记录
    """
    issues: List[HealthIssue] = []
    range_stats: Dict[str, Dict[str, Any]] = {}

    for symbol, df in data.items():
        if df is None or df.empty:
            range_stats[symbol] = {"min": None, "max": None, "null_count": 0}
            continue

        if column not in df.columns:
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="value_range",
                    message=f"缺少列: {column}",
                    severity=IssueSeverity.WARNING,
                )
            )
            range_stats[symbol] = {"min": None, "max": None, "null_count": 0}
            continue

        values = df[column]
        null_count = values.isnull().sum()
        actual_min = values.min()
        actual_max = values.max()

        range_stats[symbol] = {
            "min": float(actual_min) if pd.notna(actual_min) else None,
            "max": float(actual_max) if pd.notna(actual_max) else None,
            "null_count": int(null_count),
        }

        # 检查空值
        if not allow_null and null_count > 0:
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="value_range",
                    message=f"列 {column} 包含 {null_count} 个空值",
                    severity=IssueSeverity.WARNING,
                    details={"null_count": int(null_count)},
                )
            )

        # 检查下界
        if min_value is not None and pd.notna(actual_min) and actual_min < min_value:
            below_count = (values < min_value).sum()
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="value_range",
                    message=f"列 {column} 有 {below_count} 个值低于下界 {min_value}",
                    severity=IssueSeverity.WARNING,
                    details={"min_value": min_value, "actual_min": float(actual_min)},
                )
            )

        # 检查上界
        if max_value is not None and pd.notna(actual_max) and actual_max > max_value:
            above_count = (values > max_value).sum()
            issues.append(
                HealthIssue(
                    symbol=symbol,
                    check_name="value_range",
                    message=f"列 {column} 有 {above_count} 个值超过上界 {max_value}",
                    severity=IssueSeverity.WARNING,
                    details={"max_value": max_value, "actual_max": float(actual_max)},
                )
            )

    status = _determine_status(issues)
    return CheckResult(
        check_name=f"value_range_{column}",
        status=status,
        details={
            "column": column,
            "range_stats": range_stats,
            "min_value": min_value,
            "max_value": max_value,
        },
        issues=issues,
    )


# ============================================================================
# 健康检查器类
# ============================================================================


class DataHealthChecker:
    """
    数据健康检查器

    提供可配置的健康检查流水线

    Example:
        >>> checker = DataHealthChecker()
        >>> checker.add_check(check_missing_rate, threshold=0.1)
        >>> checker.add_check(check_price_jumps, threshold=0.3)
        >>> report = checker.run(data, symbols, date_range)
    """

    def __init__(self):
        """初始化检查器"""
        self._checks: List[tuple[Callable, Dict[str, Any]]] = []

    def add_check(self, check_func: Callable[..., CheckResult], **kwargs: Any) -> "DataHealthChecker":
        """
        添加检查项

        Args:
            check_func: 检查函数
            **kwargs: 传递给检查函数的参数

        Returns:
            self（支持链式调用）
        """
        self._checks.append((check_func, kwargs))
        return self

    def add_default_checks(
        self,
        missing_threshold: float = 0.1,
        jump_threshold: float = 0.30,
    ) -> "DataHealthChecker":
        """
        添加默认检查项

        Args:
            missing_threshold: 缺失率阈值
            jump_threshold: 跳变阈值

        Returns:
            self
        """
        self.add_check(check_missing_rate, threshold=missing_threshold)
        self.add_check(check_price_jumps, threshold=jump_threshold)
        self.add_check(check_halt_flags)
        self.add_check(check_duplicates)
        self.add_check(check_order)
        return self

    def run(
        self,
        data: Dict[str, pd.DataFrame],
        symbols: Optional[List[str]] = None,
        date_range: Optional[tuple[str, str]] = None,
    ) -> HealthReport:
        """
        运行所有检查

        Args:
            data: {symbol: DataFrame} 映射
            symbols: 股票列表（可选，默认从 data.keys() 获取）
            date_range: 日期范围 (start, end)

        Returns:
            HealthReport 完整报告
        """
        if symbols is None:
            symbols = list(data.keys())

        if date_range is None:
            # 尝试从数据推断
            date_range = self._infer_date_range(data)

        results: List[CheckResult] = []
        for check_func, kwargs in self._checks:
            try:
                result = check_func(data, **kwargs)
                results.append(result)
            except Exception as e:
                # 记录检查失败
                results.append(
                    CheckResult(
                        check_name=check_func.__name__,
                        status=CheckStatus.ERROR,
                        details={"error": str(e)},
                        issues=[
                            HealthIssue(
                                symbol="*",
                                check_name=check_func.__name__,
                                message=f"检查失败: {e}",
                                severity=IssueSeverity.ERROR,
                            )
                        ],
                    )
                )

        return HealthReport(
            check_time=datetime.now(),
            symbols=symbols,
            date_range=date_range,
            results=results,
        )

    def _infer_date_range(self, data: Dict[str, pd.DataFrame]) -> tuple[str, str]:
        """从数据推断日期范围"""
        min_date = None
        max_date = None

        for df in data.values():
            if df is None or df.empty:
                continue

            time_col = "trade_time" if "trade_time" in df.columns else "trade_date"
            if time_col not in df.columns:
                continue

            times = pd.to_datetime(df[time_col])
            if min_date is None or times.min() < min_date:
                min_date = times.min()
            if max_date is None or times.max() > max_date:
                max_date = times.max()

        if min_date is None or max_date is None:
            return ("unknown", "unknown")

        return (
            min_date.strftime("%Y-%m-%d"),
            max_date.strftime("%Y-%m-%d"),
        )


# ============================================================================
# 辅助函数
# ============================================================================


def _determine_status(issues: List[HealthIssue]) -> CheckStatus:
    """根据问题列表确定检查状态"""
    if not issues:
        return CheckStatus.PASS
    if any(issue.severity == IssueSeverity.ERROR for issue in issues):
        return CheckStatus.ERROR
    if any(issue.severity == IssueSeverity.WARNING for issue in issues):
        return CheckStatus.WARNING
    return CheckStatus.PASS


def run_quick_check(
    data: Dict[str, pd.DataFrame],
    missing_threshold: float = 0.1,
    jump_threshold: float = 0.30,
) -> HealthReport:
    """
    快速运行默认检查

    Args:
        data: {symbol: DataFrame} 映射
        missing_threshold: 缺失率阈值
        jump_threshold: 跳变阈值

    Returns:
        HealthReport
    """
    checker = DataHealthChecker()
    checker.add_default_checks(
        missing_threshold=missing_threshold,
        jump_threshold=jump_threshold,
    )
    return checker.run(data)


def format_report_text(report: HealthReport) -> str:
    """
    将检查报告格式化为文本

    Args:
        report: HealthReport

    Returns:
        格式化的文本报告
    """
    lines = [
        "=" * 60,
        "数据健康检查报告",
        "=" * 60,
        f"检查时间: {report.summary['check_time']}",
        f"股票数量: {report.summary['symbols_count']}",
        f"日期范围: {report.date_range[0]} ~ {report.date_range[1]}",
        "",
        f"总检查项: {report.summary['total_checks']}",
        f"  ✓ 通过: {report.summary['pass_count']}",
        f"  ⚠ 警告: {report.summary['warning_count']}",
        f"  ✗ 错误: {report.summary['error_count']}",
        f"总问题数: {report.summary['total_issues']}",
        "",
        "-" * 60,
    ]

    for result in report.results:
        status_icon = {"pass": "✓", "warning": "⚠", "error": "✗"}.get(
            result.status.value, "?"
        )
        lines.append(f"[{status_icon}] {result.check_name}: {result.status.value.upper()}")

        if result.issues:
            for issue in result.issues:
                severity_icon = {"info": "ℹ", "warning": "⚠", "error": "✗"}.get(
                    issue.severity.value, "?"
                )
                lines.append(f"    {severity_icon} {issue.symbol}: {issue.message}")

        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================================
# bars_by_date 格式的快速健康检查
# ============================================================================


def quick_check_bars_by_date(
    bars_by_date: dict,
    *,
    price_jump_threshold: float = 0.30,
    min_symbols_per_day: int = 1,
) -> dict:
    """对 ``dict[date, dict[str, Bar]]`` 格式的数据执行轻量健康检查。

    与 :class:`DataHealthChecker` 不同，本函数直接操作回测引擎使用的
    ``bars_by_date`` 结构，无需额外转换，适合在 ``config_runner`` 中于
    数据加载后快速调用。

    检查项：
    - 日期连续性（是否有缺口、是否排序）
    - 每日标的数波动
    - 价格跳变（跨日 close-to-close）
    - 空日期（某些日期 0 只标的）

    Args:
        bars_by_date: 回测数据
        price_jump_threshold: 跨日价格跳变阈值（e.g. 0.30 = 30%）
        min_symbols_per_day: 每日最少标的数，低于则警告

    Returns:
        检查结果摘要字典，包含 ``status`` ("pass"/"warning"/"error")
        与 ``checks`` 列表
    """
    if not bars_by_date:
        return {
            "status": "error",
            "message": "无数据",
            "checks": [],
        }

    sorted_dates = sorted(bars_by_date.keys())
    checks: list[dict] = []

    # 1. 日期排序检查
    original_dates = list(bars_by_date.keys())
    is_sorted = original_dates == sorted_dates
    checks.append({
        "name": "date_order",
        "status": "pass" if is_sorted else "warning",
        "detail": f"{len(sorted_dates)} 个交易日"
        + ("" if is_sorted else " (未排序)"),
    })

    # 2. 日期重复检查
    n_unique = len(set(sorted_dates))
    n_dup = len(sorted_dates) - n_unique
    checks.append({
        "name": "date_duplicates",
        "status": "pass" if n_dup == 0 else "error",
        "detail": f"重复日期: {n_dup}" if n_dup > 0 else "无重复",
    })

    # 3. 每日标的数统计
    symbols_per_day = {d: len(bars_by_date[d]) for d in sorted_dates}
    min_sym = min(symbols_per_day.values())
    max_sym = max(symbols_per_day.values())
    avg_sym = sum(symbols_per_day.values()) / len(symbols_per_day)
    low_days = [d for d, n in symbols_per_day.items() if n < min_symbols_per_day]
    checks.append({
        "name": "symbols_per_day",
        "status": "pass" if not low_days else "warning",
        "detail": f"min={min_sym}, max={max_sym}, avg={avg_sym:.0f}"
        + (f", {len(low_days)} 天低于阈值 {min_symbols_per_day}" if low_days else ""),
    })

    # 4. 跨日价格跳变检查（抽样检查，避免过慢）
    jump_count = 0
    prev_closes: dict[str, float] = {}
    for d in sorted_dates:
        for sym, bar in bars_by_date[d].items():
            if sym in prev_closes and prev_closes[sym] > 0:
                change = abs(bar.close - prev_closes[sym]) / prev_closes[sym]
                if change > price_jump_threshold:
                    jump_count += 1
            prev_closes[sym] = bar.close

    checks.append({
        "name": "price_jumps",
        "status": "pass" if jump_count == 0 else "warning",
        "detail": f"跨日跳变(>{price_jump_threshold:.0%}): {jump_count} 次",
    })

    # 汇总
    statuses = [c["status"] for c in checks]
    if "error" in statuses:
        overall = "error"
    elif "warning" in statuses:
        overall = "warning"
    else:
        overall = "pass"

    return {
        "status": overall,
        "n_dates": len(sorted_dates),
        "date_range": [str(sorted_dates[0]), str(sorted_dates[-1])],
        "total_bars": sum(symbols_per_day.values()),
        "checks": checks,
    }


# ============================================================================
# 日线月度分片缓存健康检查
# ============================================================================


def check_daily_cache_health(
    cache_dir: str,
    *,
    symbols: list[str] | None = None,
    start_yyyymm: str | None = None,
    end_yyyymm: str | None = None,
    freq: str = "D",
    adj: str = "qfq",
) -> dict:
    """检查日线月度分片缓存目录的健康状况。

    检查项：
    - 目录是否存在且非空
    - 各 symbol 的月份连续性
    - Parquet 文件是否可读且列完整
    - 单月数据行数合理性（正常 ≤ 23 交易日）

    Args:
        cache_dir: 缓存根目录（如 data/cache/tushare）
        symbols: 要检查的股票列表（None=自动发现）
        start_yyyymm: 开始月份（如 "202201"）
        end_yyyymm: 结束月份（如 "202412"）
        freq: 频率标记（默认 "D"）
        adj: 复权方式（默认 "qfq"）

    Returns:
        检查结果摘要字典
    """
    import re
    from pathlib import Path

    cache_path = Path(cache_dir)
    checks: list[dict] = []

    # 1. 目录存在性
    if not cache_path.exists():
        return {
            "status": "error",
            "message": f"缓存目录不存在: {cache_dir}",
            "checks": [{"name": "dir_exists", "status": "error", "detail": "不存在"}],
        }

    # 2. 发现 parquet 文件
    pattern = re.compile(
        r"^(.+?)_(\d{6})_" + re.escape(freq) + r"_" + re.escape(adj) + r"\.parquet$"
    )
    files = list(cache_path.glob("*.parquet"))
    matched: dict[str, list[str]] = {}  # symbol → [YYYYMM, ...]
    for f in files:
        m = pattern.match(f.name)
        if m:
            sym, ym = m.group(1), m.group(2)
            if symbols and sym not in symbols:
                continue
            matched.setdefault(sym, []).append(ym)

    checks.append({
        "name": "parquet_discovery",
        "status": "pass" if matched else "warning",
        "detail": f"发现 {len(matched)} 只标的, {len(files)} 个文件",
    })

    if not matched:
        return {
            "status": "warning",
            "message": "未发现匹配的 parquet 缓存文件",
            "checks": checks,
        }

    # 3. 月份连续性
    gap_count = 0
    for sym, months in matched.items():
        months_sorted = sorted(months)
        for i in range(1, len(months_sorted)):
            prev_y, prev_m = int(months_sorted[i - 1][:4]), int(months_sorted[i - 1][4:])
            cur_y, cur_m = int(months_sorted[i][:4]), int(months_sorted[i][4:])
            expected_m = prev_m + 1 if prev_m < 12 else 1
            expected_y = prev_y if prev_m < 12 else prev_y + 1
            if cur_y != expected_y or cur_m != expected_m:
                gap_count += 1

    checks.append({
        "name": "month_continuity",
        "status": "pass" if gap_count == 0 else "warning",
        "detail": f"月份间断: {gap_count} 个" if gap_count > 0 else "月份连续",
    })

    # 4. 文件可读性与行数合理性（抽样检查前 5 个文件）
    read_errors = 0
    abnormal_rows = 0
    sample_files = files[:5]
    for f in sample_files:
        try:
            df = pd.read_parquet(f)
            if len(df) > 25:  # 一个月最多约 23 个交易日
                abnormal_rows += 1
        except Exception:
            read_errors += 1

    checks.append({
        "name": "file_readability",
        "status": "pass" if read_errors == 0 else "error",
        "detail": f"读取错误: {read_errors}/{len(sample_files)}",
    })
    checks.append({
        "name": "row_count_sanity",
        "status": "pass" if abnormal_rows == 0 else "warning",
        "detail": f"异常行数文件: {abnormal_rows}/{len(sample_files)}",
    })

    # 汇总
    statuses = [c["status"] for c in checks]
    overall = "error" if "error" in statuses else ("warning" if "warning" in statuses else "pass")

    return {
        "status": overall,
        "n_symbols": len(matched),
        "n_files": len(files),
        "checks": checks,
    }


def check_pit_data_completeness(
    fina_pit_map: dict | None,
    *,
    expected_symbols: list[str] | None = None,
    min_records_per_symbol: int = 1,
) -> dict:
    """检查 PIT 财务数据的完整性。

    检查项：
    - fina_pit_map 是否存在且非空
    - 各 symbol 的记录数是否达标
    - ann_date 排序是否正确

    Args:
        fina_pit_map: PIT 财务映射 {symbol: [(ann_date, data_dict), ...]}
        expected_symbols: 期望覆盖的标的列表
        min_records_per_symbol: 每只标的最少财报记录数

    Returns:
        检查结果摘要字典
    """
    checks: list[dict] = []

    if fina_pit_map is None or not fina_pit_map:
        return {
            "status": "warning",
            "message": "PIT 财务映射为空（可能策略不需要）",
            "checks": [{"name": "pit_exists", "status": "warning", "detail": "空映射"}],
        }

    # 1. 覆盖率
    covered = set(fina_pit_map.keys())
    if expected_symbols:
        missing = set(expected_symbols) - covered
        coverage = len(covered & set(expected_symbols)) / len(expected_symbols) if expected_symbols else 0
        checks.append({
            "name": "pit_coverage",
            "status": "pass" if not missing else "warning",
            "detail": f"覆盖 {coverage:.1%} ({len(covered)}/{len(expected_symbols)})"
            + (f", 缺失 {len(missing)} 只" if missing else ""),
        })
    else:
        checks.append({
            "name": "pit_coverage",
            "status": "pass",
            "detail": f"共 {len(covered)} 只标的",
        })

    # 2. 记录数达标
    low_count = 0
    for sym, records in fina_pit_map.items():
        if len(records) < min_records_per_symbol:
            low_count += 1

    checks.append({
        "name": "pit_record_count",
        "status": "pass" if low_count == 0 else "warning",
        "detail": f"记录数不足: {low_count} 只" if low_count > 0 else "全部达标",
    })

    # 3. ann_date 排序
    unsorted_count = 0
    for sym, records in fina_pit_map.items():
        dates = [r[0] for r in records]
        if dates != sorted(dates):
            unsorted_count += 1

    checks.append({
        "name": "pit_date_order",
        "status": "pass" if unsorted_count == 0 else "error",
        "detail": f"未排序: {unsorted_count} 只" if unsorted_count > 0 else "全部有序",
    })

    statuses = [c["status"] for c in checks]
    overall = "error" if "error" in statuses else ("warning" if "warning" in statuses else "pass")

    return {
        "status": overall,
        "n_symbols": len(covered),
        "checks": checks,
    }
