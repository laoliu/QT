"""回测质量门禁与迭代追踪模块。

提供可配置的质量阈值检查（Quality Gates）和参数变更追踪，
确保每次回测迭代都可验证、可追溯、可复现。

核心组件:
- QualityGate: 单个质量指标阈值
- QualityReport: 质量门禁检查结果
- QualityChecker: 执行质量检查
- IterationRecord: 迭代变更记录
- IterationTracker: 管理迭代历史

设计原则:
- 质量阈值通过 YAML 配置（configs/quality_gates.yml），不硬编码
- 迭代记录持久化到 JSON，支持回溯
- 与 ExperimentArchiver / ExperimentComparator 互补
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# 质量门禁（Quality Gates）
# ============================================================================


@dataclass(frozen=True, slots=True)
class QualityGate:
    """单个质量检查项。

    Attributes:
        metric: 指标名称（需与 BacktestReport 的字段对应）
        operator: 比较运算符: ">=", "<=", ">", "<", "==", "!="
        threshold: 阈值
        severity: 严重度: "error"=硬门禁(必须通过), "warning"=软告警
        description: 人类可读描述
        required: 指标缺失时是否算失败。``False`` 表示「未提供则跳过」——
            用于过拟合检验这类需要额外输入（试验次数 / CPCV 折）的指标，
            单次回测拿不到时不应被判为不合格，但必须在报告里显式列出
            「未检验」，否则缺检验和通过检验看起来一样。
    """

    metric: str
    operator: str
    threshold: float
    severity: str = "error"
    description: str = ""
    required: bool = True


@dataclass(frozen=True, slots=True)
class GateResult:
    """单个门禁的检查结果。

    Attributes:
        gate: 原始门禁定义
        actual_value: 实际值
        passed: 是否通过
        message: 描述信息
    """

    gate: QualityGate
    actual_value: float | None
    passed: bool
    message: str


@dataclass(frozen=True, slots=True)
class QualityReport:
    """质量门禁检查报告。

    Attributes:
        strategy_name: 策略名称
        checked_at: 检查时间
        all_passed: 所有硬门禁是否全部通过
        results: 各门禁检查结果列表
        errors: 硬门禁失败列表
        warnings: 软告警列表
        skipped: 因指标未提供而跳过的非必需门禁（``required=False``）。
            不计入 all_passed，但必须被读到 —— 「未做过拟合检验」
            和「过拟合检验通过」是两回事。
    """

    strategy_name: str
    checked_at: str
    all_passed: bool
    results: list[GateResult]
    errors: list[GateResult]
    warnings: list[GateResult]
    skipped: list[GateResult] = field(default_factory=list)


# ============================================================================
# 默认质量门禁配置
# ============================================================================

# 过拟合检验阈值 — 与 governance/statistical_gate.py 保持一致
# （那里做策略晋级门禁，这里做单次回测门禁；两处阈值必须相同，
#   由 tests/backtest/test_quality_gates_overfitting.py 断言守护）
DSR_P_MAX = 0.10   # DSR_P_SOFT
PBO_MAX = 0.50
OOS_SHARPE_DECAY_MAX = 0.50

# 样本外/多重检验门禁。
#
# 这一组全部 required=False: 单次回测本身算不出这些指标（DSR 需要
# 试验次数 N，PBO 需要 CPCV 折集合），必须由调用方喂进来。设为
# 必需会让所有现有回测直接变红；设为跳过则至少在报告里点名
# 「这条策略没做过拟合检验」，而不是让样本内高 Sharpe 冒充有效性。
#
# 这一组是「回测好看但实盘不行」最直接的统计防线 —— 样本内指标
# 再漂亮也不能证明信号在样本外还在。
OVERFITTING_QUALITY_GATES: list[dict] = [
    {
        "metric": "dsr_p_value",
        "operator": "<=",
        "threshold": DSR_P_MAX,
        "severity": "error",
        "description": f"DSR p 值不高于{DSR_P_MAX}（多重检验校正后仍显著）",
        "required": False,
    },
    {
        "metric": "dsr",
        "operator": ">",
        "threshold": 0.0,
        "severity": "warning",
        "description": "缩减夏普（DSR）统计量应为正（优于同批试验的期望最大值）",
        "required": False,
    },
    {
        "metric": "pbo",
        "operator": "<=",
        "threshold": PBO_MAX,
        "severity": "error",
        "description": f"回测过拟合概率不高于{PBO_MAX:.0%}",
        "required": False,
    },
    {
        "metric": "oos_sharpe_decay",
        "operator": "<=",
        "threshold": OOS_SHARPE_DECAY_MAX,
        "severity": "error",
        "description": f"IS→OOS 夏普衰减不超过{OOS_SHARPE_DECAY_MAX:.0%}",
        "required": False,
    },
]

DEFAULT_QUALITY_GATES: list[dict] = [
    {
        "metric": "sharpe_ratio",
        "operator": ">=",
        "threshold": 0.3,
        "severity": "error",
        "description": "夏普比率不低于0.3（最低要求）",
    },
    {
        "metric": "max_drawdown_pct",
        "operator": "<=",
        "threshold": 0.30,
        "severity": "error",
        "description": "最大回撤不超过30%",
    },
    {
        "metric": "win_rate",
        "operator": ">=",
        "threshold": 0.35,
        "severity": "warning",
        "description": "胜率建议不低于35%",
    },
    {
        "metric": "total_return",
        "operator": ">",
        "threshold": 0.0,
        "severity": "error",
        "description": "总收益率必须为正",
    },
    {
        "metric": "calmar_ratio",
        "operator": ">=",
        "threshold": 0.5,
        "severity": "warning",
        "description": "卡玛比率建议不低于0.5",
    },
    {
        "metric": "annualized_return",
        "operator": ">=",
        "threshold": 0.05,
        "severity": "warning",
        "description": "年化收益率建议不低于5%",
    },
    {
        "metric": "profit_factor",
        "operator": ">=",
        "threshold": 1.0,
        "severity": "error",
        "description": "盈亏比必须大于1.0（总盈利>总亏损）",
    },
    {
        "metric": "avg_positions",
        "operator": ">=",
        "threshold": 3.0,
        "severity": "warning",
        "description": "平均持仓数建议不低于3只（分散风险）",
    },
    {
        "metric": "turnover_rate",
        "operator": "<=",
        "threshold": 50.0,
        "severity": "warning",
        "description": "年化单边换手率建议不超过50倍（过高换手侵蚀收益）",
    },
    {
        "metric": "max_concentration",
        "operator": "<=",
        "threshold": 0.50,
        "severity": "warning",
        "description": "最大单票集中度建议不超过50%（过度集中风险）",
    },
    *OVERFITTING_QUALITY_GATES,
]


# ============================================================================
# 质量检查器
# ============================================================================


class QualityChecker:
    """回测质量门禁检查器。

    加载质量门禁配置，对回测指标进行阈值检查，
    生成结构化的质量报告。

    用法::

        checker = QualityChecker.from_yaml("configs/quality_gates.yml")
        report = checker.check(metrics_dict, strategy_name="small_size")
        if not report.all_passed:
            for err in report.errors:
                print(f"FAIL: {err.message}")
    """

    # 指标名称别名映射：解决两套 PerformanceMetrics 字段名不一致的问题
    # key = 门禁配置中可能出现的名称，value = extract_metrics_dict 中的标准名称
    METRIC_ALIASES: dict[str, str] = {
        "annual_return": "annualized_return",
        "annualised_return": "annualized_return",
        "mdd": "max_drawdown_pct",
        "max_dd": "max_drawdown_pct",
    }

    def __init__(self, gates: list[QualityGate]):
        self.gates = gates

    @classmethod
    def from_config(cls, config: list[dict]) -> QualityChecker:
        """从字典列表构建检查器。

        Args:
            config: 质量门禁配置列表

        Returns:
            QualityChecker 实例
        """
        gates = [QualityGate(**g) for g in config]
        return cls(gates)

    @classmethod
    def from_yaml(cls, path: str | Path) -> QualityChecker:
        """从 YAML 配置文件加载。

        Args:
            path: YAML 文件路径

        Returns:
            QualityChecker 实例
        """
        import yaml

        p = Path(path)
        if not p.exists():
            logger.warning(f"质量门禁配置文件不存在: {p}, 使用默认配置")
            return cls.from_config(DEFAULT_QUALITY_GATES)

        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if data is None:
            logger.warning(f"质量门禁配置文件为空: {p}, 使用默认配置")
            return cls.from_config(DEFAULT_QUALITY_GATES)

        # 支持两种格式: {quality_gates: [...]} 或直接 [...]
        if isinstance(data, list):
            return cls.from_config(data)

        gates_config = data.get("quality_gates", data) if isinstance(data, dict) else None
        if isinstance(gates_config, list):
            return cls.from_config(gates_config)

        logger.warning(f"质量门禁配置格式错误: {p}, 使用默认配置")
        return cls.from_config(DEFAULT_QUALITY_GATES)

    @classmethod
    def default(cls) -> QualityChecker:
        """使用默认配置。"""
        return cls.from_config(DEFAULT_QUALITY_GATES)

    def check(
        self,
        metrics: dict[str, Any],
        strategy_name: str = "",
    ) -> QualityReport:
        """执行质量门禁检查。

        Args:
            metrics: 指标字典，键名需与 QualityGate.metric 对应
            strategy_name: 策略名称

        Returns:
            QualityReport
        """
        results: list[GateResult] = []
        errors: list[GateResult] = []
        warnings: list[GateResult] = []
        skipped: list[GateResult] = []

        for gate in self.gates:
            # 通过别名映射解决指标名不一致问题
            metric_key = gate.metric
            actual = metrics.get(metric_key)
            if actual is None:
                # 尝试别名
                alias = self.METRIC_ALIASES.get(metric_key)
                if alias:
                    actual = metrics.get(alias)
                else:
                    # 反向查找：check if metric_key is a canonical name with alias pointing to it
                    for alias_name, canonical in self.METRIC_ALIASES.items():
                        if canonical == metric_key and alias_name in metrics:
                            actual = metrics[alias_name]
                            break

            if actual is None:
                if not gate.required:
                    # 未提供 ≠ 不合格，但也 ≠ 通过 —— 记为跳过并显式点名
                    gr = GateResult(
                        gate=gate,
                        actual_value=None,
                        passed=True,
                        message=(
                            f"○ 未检验: {gate.description or gate.metric} "
                            f"（指标 '{gate.metric}' 未提供）"
                        ),
                    )
                    results.append(gr)
                    skipped.append(gr)
                    continue
                gr = GateResult(
                    gate=gate,
                    actual_value=None,
                    passed=False,
                    message=f"指标 '{gate.metric}' 不存在于结果中",
                )
                results.append(gr)
                if gate.severity == "error":
                    errors.append(gr)
                else:
                    warnings.append(gr)
                continue

            try:
                actual_float = float(actual)
            except (TypeError, ValueError):
                gr = GateResult(
                    gate=gate,
                    actual_value=None,
                    passed=False,
                    message=f"指标 '{gate.metric}' 值 '{actual}' 无法转为数值",
                )
                results.append(gr)
                if gate.severity == "error":
                    errors.append(gr)
                else:
                    warnings.append(gr)
                continue

            passed = self._compare(actual_float, gate.operator, gate.threshold)

            if passed:
                msg = (
                    f"✓ {gate.description}: "
                    f"{gate.metric}={actual_float:.4f} "
                    f"{gate.operator} {gate.threshold}"
                )
            else:
                msg = (
                    f"✗ {gate.description}: "
                    f"{gate.metric}={actual_float:.4f} "
                    f"不满足 {gate.operator} {gate.threshold}"
                )

            gr = GateResult(
                gate=gate, actual_value=actual_float,
                passed=passed, message=msg,
            )
            results.append(gr)

            if not passed:
                if gate.severity == "error":
                    errors.append(gr)
                else:
                    warnings.append(gr)

        all_passed = len(errors) == 0

        return QualityReport(
            strategy_name=strategy_name,
            checked_at=datetime.now().isoformat(),
            all_passed=all_passed,
            results=results,
            errors=errors,
            warnings=warnings,
            skipped=skipped,
        )

    @staticmethod
    def _compare(actual: float, operator: str, threshold: float) -> bool:
        """执行比较运算。

        对 ``==`` 和 ``!=`` 使用 epsilon 容差（1e-9）避免浮点精度问题。
        """
        if operator == ">=":
            return actual >= threshold
        if operator == "<=":
            return actual <= threshold
        if operator == ">":
            return actual > threshold
        if operator == "<":
            return actual < threshold
        _EPS = 1e-9
        if operator == "==":
            return abs(actual - threshold) < _EPS
        if operator == "!=":
            return abs(actual - threshold) >= _EPS
        return False


def format_quality_report(report: QualityReport) -> str:
    """将质量报告格式化为可读文本。"""
    lines = [
        "=" * 60,
        f"质量门禁报告 — {report.strategy_name}",
        f"检查时间: {report.checked_at}",
        "=" * 60,
    ]

    status = "全部通过 ✓" if report.all_passed else "存在失败 ✗"
    lines.append(f"总体结果: {status}")
    lines.append(
        f"  硬门禁错误: {len(report.errors)}  软告警: {len(report.warnings)}"
        f"  未检验: {len(report.skipped)}"
    )
    lines.append("")

    for gr in report.results:
        prefix = "  " if gr.passed else "  *** "
        lines.append(f"{prefix}{gr.message}")

    if report.errors:
        lines.append("\n--- 硬门禁失败详情 ---")
        for gr in report.errors:
            lines.append(f"  {gr.message}")

    if report.warnings:
        lines.append("\n--- 软告警 ---")
        for gr in report.warnings:
            lines.append(f"  {gr.message}")

    if report.skipped:
        lines.append("\n--- 未检验（指标未提供，不计入判定）---")
        for gr in report.skipped:
            lines.append(f"  {gr.message}")
        lines.append(
            "  注: 「未检验」不等于「通过」。样本外/多重检验指标缺失时，"
            "样本内表现不足以支持上线。"
        )

    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================================
# 过拟合指标计算 — 把 DSR / PBO 变成可喂给门禁的数值
# ============================================================================


def check_overfitting_gates(
    metrics: dict[str, Any],
    strategy_name: str = "",
) -> QualityReport:
    """只跑过拟合门禁，不碰样本内指标。

    用于 Walk-Forward / CPCV 这类已经算出 DSR / PBO 的流程 ——
    那里本来就有这些数字，此前只是打了条日志、没有判定。

    Args:
        metrics: 含 ``dsr`` / ``dsr_p_value`` / ``pbo`` /
            ``oos_sharpe_decay`` 的字典（如
            ``wf_statistical_validation.summarize_wf_statistical_validation``
            的输出）。缺哪项就跳过哪项。
        strategy_name: 策略名称

    Returns:
        QualityReport
    """
    checker = QualityChecker.from_config(OVERFITTING_QUALITY_GATES)
    # None 值等同于「未提供」—— 上游用 None 表示算不出来
    cleaned = {k: v for k, v in metrics.items() if v is not None}
    return checker.check(cleaned, strategy_name=strategy_name)


def compute_overfitting_metrics(
    daily_returns: list[float],
    sharpe_ratio: float,
    n_trials: int,
    pbo: float | None = None,
    oos_sharpe: float | None = None,
) -> dict[str, float]:
    """计算可直接合并进门禁指标字典的过拟合指标。

    ``n_trials`` 是本策略所属那一批试验的总次数（调过的参数组合数、
    比较过的策略数）。它必须由调用方提供 —— 单次回测无从得知自己
    是第几次尝试，而这正是样本内 Sharpe 会虚高的原因: 试 100 组
    参数，最好那一组的 Sharpe 天然偏高，与信号是否真实无关。

    Args:
        daily_returns: 日收益序列（用于偏度/峰度/观测数）
        sharpe_ratio: 观测到的年化夏普
        n_trials: 试验次数 N（<2 时 DSR 无意义，返回空字典）
        pbo: 外部算好的过拟合概率（如 ComboCPCV 的输出），可选
        oos_sharpe: 样本外夏普，给出时计算 IS→OOS 衰减

    Returns:
        {"dsr", "dsr_p_value", "n_trials", ...} —— 缺输入的键直接不出现，
        以便门禁把它记为「未检验」而非「不合格」
    """
    metrics: dict[str, float] = {}

    if n_trials >= 2 and len(daily_returns) >= 20:
        try:
            import numpy as np
            from scipy import stats as sp_stats

            from aipha.factors.deflated_sharpe import deflated_sharpe_ratio

            arr = np.asarray(daily_returns, dtype=float)
            dsr = deflated_sharpe_ratio(
                observed_sr=float(sharpe_ratio),
                n_trials=int(n_trials),
                skewness=float(sp_stats.skew(arr, bias=False)),
                kurtosis=float(sp_stats.kurtosis(arr, bias=False, fisher=False)),
                t_observations=len(arr),
            )
            metrics["dsr"] = float(dsr["dsr"])
            metrics["dsr_p_value"] = float(dsr["p_value"])
            metrics["n_trials"] = float(n_trials)
        except Exception as exc:  # pragma: no cover - 防御性
            logger.warning("DSR 计算失败，该门禁记为未检验: %s", exc)

    if pbo is not None:
        metrics["pbo"] = float(pbo)

    if oos_sharpe is not None and sharpe_ratio > 0:
        metrics["oos_sharpe_decay"] = max(
            0.0, 1.0 - float(oos_sharpe) / float(sharpe_ratio),
        )

    return metrics


# ============================================================================
# Walk-Forward 质量门禁
# ============================================================================


@dataclass(frozen=True, slots=True)
class WalkForwardGateConfig:
    """Walk-Forward 质量门禁配置。

    可从 YAML ``walk_forward_gates`` 段加载。

    Attributes:
        max_sharpe_decay: 最大允许 IS→OOS Sharpe 衰减（默认 0.60）
        max_return_decay: 最大允许 IS→OOS 收益衰减（默认 0.80）
        min_test_win_rate: 最低 OOS 盈利折比例（默认 0.50）
        min_test_sharpe: 最低 OOS 平均 Sharpe（默认 0.0）
        min_wf_score: 最低 WF 评分 0~10（默认 5.0）
        severity: 严重度 "error" / "warning"（默认 "warning"）
    """

    max_sharpe_decay: float = 0.60
    max_return_decay: float = 0.80
    min_test_win_rate: float = 0.50
    min_test_sharpe: float = 0.0
    min_wf_score: float = 5.0
    severity: str = "warning"


@dataclass(frozen=True, slots=True)
class WalkForwardGateResult:
    """Walk-Forward 门禁单项检查结果。

    Attributes:
        metric: 检查项名称
        actual_value: 实际值
        threshold: 阈值
        passed: 是否通过
        severity: 严重度
        message: 描述信息
    """

    metric: str
    actual_value: float
    threshold: float
    passed: bool
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class WalkForwardGateReport:
    """Walk-Forward 质量门禁报告。

    Attributes:
        strategy_name: 策略名称
        checked_at: 检查时间
        all_passed: 所有硬门禁是否通过
        wf_verdict: WF 总判定 (PASS/FAIL/NO_DATA)
        wf_score: WF 评分 (0~10)
        results: 各项检查结果
        errors: 硬门禁失败
        warnings: 软告警
    """

    strategy_name: str
    checked_at: str
    all_passed: bool
    wf_verdict: str
    wf_score: float
    results: list[WalkForwardGateResult]
    errors: list[WalkForwardGateResult]
    warnings: list[WalkForwardGateResult]


def check_walk_forward_gates(
    wf_report_dict: dict[str, Any],
    strategy_name: str = "",
    config: WalkForwardGateConfig | None = None,
) -> WalkForwardGateReport:
    """对 Walk-Forward 验证结果执行质量门禁检查。

    Args:
        wf_report_dict: WF 报告字典，需包含以下键：
            - avg_sharpe_decay: float
            - avg_return_decay: float
            - test_win_rate: float
            - avg_test_sharpe: float
            以及可选的 verdict / score
        strategy_name: 策略名称
        config: WF 门禁配置（None=使用默认值）

    Returns:
        WalkForwardGateReport
    """
    from aipha.backtest.validation import compute_wf_score as _compute_score

    cfg = config or WalkForwardGateConfig()
    results: list[WalkForwardGateResult] = []
    errors: list[WalkForwardGateResult] = []
    warnings: list[WalkForwardGateResult] = []

    # 提取 WF 指标
    sharpe_decay = wf_report_dict.get("avg_sharpe_decay", 1.0)
    return_decay = wf_report_dict.get("avg_return_decay", 1.0)
    test_win_rate = wf_report_dict.get("test_win_rate", 0.0)
    test_sharpe = wf_report_dict.get("avg_test_sharpe", 0.0)

    # 如果有 score 直接用，否则需要从 ValidationReport 计算
    wf_score = wf_report_dict.get("score", 0.0)
    wf_verdict = wf_report_dict.get("verdict", "UNKNOWN")

    # 定义检查项: (metric_name, actual, threshold, operator, description)
    checks = [
        (
            "sharpe_decay", sharpe_decay, cfg.max_sharpe_decay, "<=",
            f"IS→OOS Sharpe衰减 ({sharpe_decay:.2%}) ≤ {cfg.max_sharpe_decay:.0%}",
        ),
        (
            "return_decay", return_decay, cfg.max_return_decay, "<=",
            f"IS→OOS 收益衰减 ({return_decay:.2%}) ≤ {cfg.max_return_decay:.0%}",
        ),
        (
            "test_win_rate", test_win_rate, cfg.min_test_win_rate, ">=",
            f"OOS 盈利折比例 ({test_win_rate:.2%}) ≥ {cfg.min_test_win_rate:.0%}",
        ),
        (
            "test_sharpe", test_sharpe, cfg.min_test_sharpe, ">",
            f"OOS 平均 Sharpe ({test_sharpe:.2f}) > {cfg.min_test_sharpe:.2f}",
        ),
        (
            "wf_score", wf_score, cfg.min_wf_score, ">=",
            f"WF 综合评分 ({wf_score:.1f}) ≥ {cfg.min_wf_score:.1f}",
        ),
    ]

    for metric, actual, threshold, op, desc in checks:
        if op == "<=":
            passed = actual <= threshold
        elif op == ">=":
            passed = actual >= threshold
        elif op == ">":
            passed = actual > threshold
        else:
            passed = actual < threshold

        msg = f"{'✓' if passed else '✗'} WF: {desc}"
        gr = WalkForwardGateResult(
            metric=metric,
            actual_value=actual,
            threshold=threshold,
            passed=passed,
            severity=cfg.severity,
            message=msg,
        )
        results.append(gr)
        if not passed:
            if cfg.severity == "error":
                errors.append(gr)
            else:
                warnings.append(gr)

    all_passed = len(errors) == 0

    return WalkForwardGateReport(
        strategy_name=strategy_name,
        checked_at=datetime.now().isoformat(),
        all_passed=all_passed,
        wf_verdict=wf_verdict,
        wf_score=wf_score,
        results=results,
        errors=errors,
        warnings=warnings,
    )


def load_wf_gate_config(yaml_path: str | Path) -> WalkForwardGateConfig | None:
    """从 YAML 配置加载 WF 门禁配置。

    YAML 格式::

        walk_forward_gates:
          max_sharpe_decay: 0.60
          max_return_decay: 0.80
          min_test_win_rate: 0.50
          min_test_sharpe: 0.0
          min_wf_score: 5.0
          severity: warning

    Args:
        yaml_path: YAML 配置文件路径

    Returns:
        WalkForwardGateConfig 或 None（配置不存在）
    """
    import yaml

    p = Path(yaml_path)
    if not p.exists():
        return None

    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            return None
        wf_cfg = data.get("walk_forward_gates")
        if not isinstance(wf_cfg, dict):
            return None
        return WalkForwardGateConfig(**{
            k: v for k, v in wf_cfg.items()
            if k in WalkForwardGateConfig.__dataclass_fields__
        })
    except Exception as e:
        logger.warning(f"加载 WF 门禁配置失败: {e}")
        return None


def format_wf_gate_report(report: WalkForwardGateReport) -> str:
    """将 WF 门禁报告格式化为可读文本。"""
    lines = [
        "=" * 60,
        f"Walk-Forward 质量门禁 — {report.strategy_name}",
        f"WF 判定: {report.wf_verdict}  评分: {report.wf_score:.1f}/10",
        "=" * 60,
    ]

    for gr in report.results:
        prefix = "  " if gr.passed else "  *** "
        lines.append(f"{prefix}{gr.message}")

    status = "全部通过 ✓" if report.all_passed else "存在失败 ✗"
    lines.append(f"\n总体: {status} (errors={len(report.errors)}, warnings={len(report.warnings)})")
    lines.append("=" * 60)
    return "\n".join(lines)


# ============================================================================
# 迭代变更记录
# ============================================================================


@dataclass
class IterationRecord:
    """一次迭代变更记录。

    Attributes:
        iteration_id: 迭代编号（自动递增）
        timestamp: 记录时间
        strategy_name: 策略名称
        description: 变更描述
        changes: 变更内容列表（如参数变更）
        metrics_before: 变更前指标（可选）
        metrics_after: 变更后指标
        quality_passed: 质量门禁是否通过
        git_commit: Git commit hash（可选）
        config_hash: 配置文件 hash（可选）
        notes: 备注
    """

    iteration_id: int
    timestamp: str
    strategy_name: str
    description: str
    changes: list[dict[str, Any]] = field(default_factory=list)
    metrics_before: dict[str, Any] | None = None
    metrics_after: dict[str, Any] = field(default_factory=dict)
    quality_passed: bool = False
    git_commit: str = ""
    config_hash: str = ""
    notes: str = ""


class IterationTracker:
    """迭代变更追踪器。

    持久化地记录每次策略/参数变更及其对指标的影响，
    形成可回溯的优化日志。

    用法::

        tracker = IterationTracker("iterations/small_size.json")

        # 记录一次迭代
        record = tracker.add_iteration(
            strategy_name="small_size",
            description="放宽 max_price 从 115 到 130",
            changes=[{"param": "max_price", "before": 115, "after": 130}],
            metrics_after={"sharpe_ratio": 1.2, "max_drawdown_pct": 0.15},
        )

        # 查看历史
        history = tracker.get_history()
        for r in history:
            print(f"#{r.iteration_id}: {r.description}")
    """

    def __init__(self, path: str | Path):
        """
        Args:
            path: 迭代记录 JSON 文件路径
        """
        self.path = Path(path)
        self._records: list[IterationRecord] = []
        self._load()

    def _load(self) -> None:
        """从磁盘加载历史记录。"""
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                self._records = [
                    IterationRecord(**r) for r in data.get("iterations", [])
                ]
            except (json.JSONDecodeError, TypeError, KeyError) as e:
                logger.warning(f"加载迭代记录失败: {e}, 将创建新文件")
                self._records = []

    def _save(self) -> None:
        """持久化到磁盘。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": "1.0",
            "last_updated": datetime.now().isoformat(),
            "total_iterations": len(self._records),
            "iterations": [asdict(r) for r in self._records],
        }
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def add_iteration(
        self,
        strategy_name: str,
        description: str,
        changes: list[dict[str, Any]] | None = None,
        metrics_before: dict[str, Any] | None = None,
        metrics_after: dict[str, Any] | None = None,
        quality_passed: bool = False,
        git_commit: str = "",
        config_hash: str = "",
        notes: str = "",
    ) -> IterationRecord:
        """添加一条迭代记录。

        Args:
            strategy_name: 策略名称
            description: 变更描述
            changes: 变更详情列表
            metrics_before: 变更前指标
            metrics_after: 变更后指标
            quality_passed: 质量门禁是否通过
            git_commit: Git commit hash
            config_hash: 配置文件 hash
            notes: 备注

        Returns:
            新增的迭代记录
        """
        next_id = max((r.iteration_id for r in self._records), default=0) + 1

        record = IterationRecord(
            iteration_id=next_id,
            timestamp=datetime.now().isoformat(),
            strategy_name=strategy_name,
            description=description,
            changes=changes or [],
            metrics_before=metrics_before,
            metrics_after=metrics_after or {},
            quality_passed=quality_passed,
            git_commit=git_commit,
            config_hash=config_hash,
            notes=notes,
        )

        self._records.append(record)
        self._save()
        logger.info(
            f"迭代 #{next_id} 已记录: {strategy_name} — {description}"
        )
        return record

    def get_history(
        self,
        strategy_name: str | None = None,
        limit: int | None = None,
    ) -> list[IterationRecord]:
        """获取迭代历史。

        Args:
            strategy_name: 按策略名过滤 (None=全部)
            limit: 返回最近 N 条

        Returns:
            迭代记录列表（按时间正序）
        """
        records = self._records
        if strategy_name:
            records = [r for r in records if r.strategy_name == strategy_name]
        if limit:
            records = records[-limit:]
        return records

    def get_latest(self, strategy_name: str | None = None) -> IterationRecord | None:
        """获取最新一条迭代记录。"""
        history = self.get_history(strategy_name)
        return history[-1] if history else None

    def get_metric_trend(
        self, metric: str, strategy_name: str | None = None,
    ) -> list[tuple[int, str, float | None]]:
        """获取某指标随迭代的变化趋势。

        Args:
            metric: 指标名称
            strategy_name: 按策略名过滤

        Returns:
            [(iteration_id, timestamp, metric_value), ...]
        """
        history = self.get_history(strategy_name)
        trend = []
        for r in history:
            val = r.metrics_after.get(metric)
            trend.append((r.iteration_id, r.timestamp, val))
        return trend

    def compare_iterations(
        self, id_a: int, id_b: int,
    ) -> dict[str, Any]:
        """对比两次迭代的指标变化。

        Args:
            id_a: 迭代 A 编号
            id_b: 迭代 B 编号

        Returns:
            指标对比字典
        """
        rec_a = next((r for r in self._records if r.iteration_id == id_a), None)
        rec_b = next((r for r in self._records if r.iteration_id == id_b), None)

        if not rec_a or not rec_b:
            return {"error": f"找不到迭代 #{id_a} 或 #{id_b}"}

        all_metrics = set(rec_a.metrics_after.keys()) | set(rec_b.metrics_after.keys())
        comparison = {}
        for m in sorted(all_metrics):
            val_a = rec_a.metrics_after.get(m)
            val_b = rec_b.metrics_after.get(m)
            delta = None
            if isinstance(val_a, (int, float)) and isinstance(val_b, (int, float)):
                delta = val_b - val_a
            comparison[m] = {"iteration_a": val_a, "iteration_b": val_b, "delta": delta}

        return {
            "iteration_a": {"id": id_a, "description": rec_a.description},
            "iteration_b": {"id": id_b, "description": rec_b.description},
            "metrics_comparison": comparison,
        }

    def format_history(self, strategy_name: str | None = None) -> str:
        """格式化迭代历史为可读文本。"""
        history = self.get_history(strategy_name)
        if not history:
            return "（无迭代记录）"

        lines = [
            "=" * 60,
            "迭代变更历史",
            "=" * 60,
        ]
        for r in history:
            status = "✓" if r.quality_passed else "✗"
            lines.append(
                f"\n[#{r.iteration_id}] {r.timestamp[:10]} — {r.strategy_name}"
            )
            lines.append(f"  描述: {r.description}")
            if r.changes:
                for c in r.changes:
                    param = c.get("param", "?")
                    before = c.get("before", "?")
                    after = c.get("after", "?")
                    lines.append(f"  变更: {param}: {before} → {after}")
            if r.metrics_after:
                key_metrics = ["sharpe_ratio", "annualized_return",
                               "max_drawdown_pct", "win_rate"]
                parts = []
                for m in key_metrics:
                    v = r.metrics_after.get(m)
                    if v is not None:
                        parts.append(f"{m}={v:.4f}" if isinstance(v, float) else f"{m}={v}")
                if parts:
                    lines.append(f"  关键指标: {', '.join(parts)}")
            lines.append(f"  质量门禁: {status}  commit: {r.git_commit or 'N/A'}")
            if r.notes:
                lines.append(f"  备注: {r.notes}")

        lines.append("\n" + "=" * 60)
        return "\n".join(lines)
