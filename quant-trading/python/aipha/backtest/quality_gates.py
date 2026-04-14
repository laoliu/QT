"""回测质量门禁与迭代追踪模块。

提供可配置的质量阈值检查（Quality Gates）和参数变更追踪，
确保每次回测迭代都可验证、可追溯、可复现。

核心组件:
- QualityGate: 单个质量指标阈值
- QualityReport: 质量门禁检查结果
- QualityChecker: 执行质量检查
- WalkForwardGateConfig / WalkForwardGateReport: Walk-Forward 门禁
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── 质量门禁数据结构 ─────────────────────────────────────

@dataclass(frozen=True)
class QualityGate:
    metric: str
    operator: str        # ">=", "<=", ">", "<", "==", "!="
    threshold: float
    severity: str = "error"   # "error"=硬门禁, "warning"=软告警
    description: str = ""


@dataclass(frozen=True)
class GateResult:
    gate: QualityGate
    actual_value: float | None
    passed: bool
    message: str


@dataclass(frozen=True)
class QualityReport:
    strategy_name: str
    checked_at: str
    all_passed: bool
    results: list[GateResult]
    errors: list[GateResult]
    warnings: list[GateResult]


# ── 默认门禁 ─────────────────────────────────────────────

DEFAULT_QUALITY_GATES: list[dict] = [
    {"metric": "sharpe_ratio", "operator": ">=", "threshold": 0.3,
     "severity": "error", "description": "夏普比率不低于0.3"},
    {"metric": "max_drawdown_pct", "operator": "<=", "threshold": 0.30,
     "severity": "error", "description": "最大回撤不超过30%"},
    {"metric": "win_rate", "operator": ">=", "threshold": 0.35,
     "severity": "warning", "description": "胜率建议不低于35%"},
    {"metric": "total_return", "operator": ">", "threshold": 0.0,
     "severity": "error", "description": "总收益率必须为正"},
    {"metric": "calmar_ratio", "operator": ">=", "threshold": 0.5,
     "severity": "warning", "description": "卡玛比率建议不低于0.5"},
    {"metric": "annualized_return", "operator": ">=", "threshold": 0.05,
     "severity": "warning", "description": "年化收益率建议不低于5%"},
    {"metric": "profit_factor", "operator": ">=", "threshold": 1.0,
     "severity": "error", "description": "盈亏比必须大于1.0"},
    {"metric": "avg_positions", "operator": ">=", "threshold": 3.0,
     "severity": "warning", "description": "平均持仓数建议不低于3只"},
    {"metric": "turnover_rate", "operator": "<=", "threshold": 50.0,
     "severity": "warning", "description": "年化换手率建议不超过50倍"},
    {"metric": "max_concentration", "operator": "<=", "threshold": 0.50,
     "severity": "warning", "description": "单票集中度建议不超过50%"},
]


# ── 质量检查器 ───────────────────────────────────────────

class QualityChecker:
    """回测质量门禁检查器"""

    METRIC_ALIASES: dict[str, str] = {
        "annual_return": "annualized_return",
        "annualised_return": "annualized_return",
        "mdd": "max_drawdown_pct",
        "max_dd": "max_drawdown_pct",
    }

    def __init__(self, gates: list[QualityGate]):
        self.gates = gates

    @classmethod
    def from_config(cls, config: list[dict]) -> "QualityChecker":
        gates = [QualityGate(**g) for g in config]
        return cls(gates)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "QualityChecker":
        import yaml
        p = Path(path)
        if not p.exists():
            logger.warning(f"质量门禁配置不存在: {p}, 使用默认配置")
            return cls.from_config(DEFAULT_QUALITY_GATES)
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            return cls.from_config(DEFAULT_QUALITY_GATES)
        if isinstance(data, list):
            return cls.from_config(data)
        gates_config = data.get("quality_gates", data) if isinstance(data, dict) else None
        if isinstance(gates_config, list):
            return cls.from_config(gates_config)
        return cls.from_config(DEFAULT_QUALITY_GATES)

    @classmethod
    def default(cls) -> "QualityChecker":
        return cls.from_config(DEFAULT_QUALITY_GATES)

    def check(self, metrics: dict[str, Any], strategy_name: str = "") -> QualityReport:
        results: list[GateResult] = []
        errors: list[GateResult] = []
        warnings: list[GateResult] = []

        for gate in self.gates:
            actual = metrics.get(gate.metric)
            if actual is None:
                alias = self.METRIC_ALIASES.get(gate.metric)
                if alias:
                    actual = metrics.get(alias)
                else:
                    for alias_name, canonical in self.METRIC_ALIASES.items():
                        if canonical == gate.metric and alias_name in metrics:
                            actual = metrics[alias_name]
                            break

            if actual is None:
                gr = GateResult(gate=gate, actual_value=None, passed=False,
                                message=f"指标 '{gate.metric}' 不存在于结果中")
                results.append(gr)
                (errors if gate.severity == "error" else warnings).append(gr)
                continue

            try:
                actual_float = float(actual)
            except (TypeError, ValueError):
                gr = GateResult(gate=gate, actual_value=None, passed=False,
                                message=f"指标 '{gate.metric}' 值无法转为数值")
                results.append(gr)
                (errors if gate.severity == "error" else warnings).append(gr)
                continue

            passed = self._compare(actual_float, gate.operator, gate.threshold)
            msg = (f"{'✓' if passed else '✗'} {gate.description}: "
                   f"{gate.metric}={actual_float:.4f} "
                   f"{'满足' if passed else '不满足'} {gate.operator} {gate.threshold}")
            gr = GateResult(gate=gate, actual_value=actual_float, passed=passed, message=msg)
            results.append(gr)
            if not passed:
                (errors if gate.severity == "error" else warnings).append(gr)

        return QualityReport(
            strategy_name=strategy_name,
            checked_at=datetime.now().isoformat(),
            all_passed=len(errors) == 0,
            results=results,
            errors=errors,
            warnings=warnings,
        )

    @staticmethod
    def _compare(actual: float, operator: str, threshold: float) -> bool:
        if operator == ">=": return actual >= threshold
        if operator == "<=": return actual <= threshold
        if operator == ">": return actual > threshold
        if operator == "<": return actual < threshold
        _EPS = 1e-9
        if operator == "==": return abs(actual - threshold) < _EPS
        if operator == "!=": return abs(actual - threshold) >= _EPS
        return False


def format_quality_report(report: QualityReport) -> str:
    lines = [
        "=" * 60,
        f"质量门禁报告 — {report.strategy_name}",
        f"检查时间: {report.checked_at}",
        "=" * 60,
        f"总体结果: {'全部通过 ✓' if report.all_passed else '存在失败 ✗'}",
        f"  硬门禁错误: {len(report.errors)}  软告警: {len(report.warnings)}",
        "",
    ]
    for gr in report.results:
        lines.append(f"  {'  ' if gr.passed else '*** '}{gr.message}")
    if report.errors:
        lines.append("\n--- 硬门禁失败 ---")
        for gr in report.errors:
            lines.append(f"  {gr.message}")
    if report.warnings:
        lines.append("\n--- 软告警 ---")
        for gr in report.warnings:
            lines.append(f"  {gr.message}")
    lines.append("=" * 60)
    return "\n".join(lines)


# ── Walk-Forward 门禁 ────────────────────────────────────

@dataclass(frozen=True)
class WalkForwardGateConfig:
    max_sharpe_decay: float = 0.60
    max_return_decay: float = 0.80
    min_test_win_rate: float = 0.50
    min_test_sharpe: float = 0.0
    min_wf_score: float = 5.0
    severity: str = "warning"


@dataclass(frozen=True)
class WalkForwardGateResult:
    metric: str
    actual_value: float
    threshold: float
    passed: bool
    severity: str
    message: str


@dataclass(frozen=True)
class WalkForwardGateReport:
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
    """对 Walk-Forward 验证结果执行质量门禁检查"""
    cfg = config or WalkForwardGateConfig()
    results: list[WalkForwardGateResult] = []
    errors: list[WalkForwardGateResult] = []
    warnings: list[WalkForwardGateResult] = []

    checks = [
        ("sharpe_decay", wf_report_dict.get("avg_sharpe_decay", 0), cfg.max_sharpe_decay, "<=",
         f"Sharpe 衰减 {wf_report_dict.get('avg_sharpe_decay', 0):.2%}"),
        ("return_decay", wf_report_dict.get("avg_return_decay", 0), cfg.max_return_decay, "<=",
         f"收益衰减 {wf_report_dict.get('avg_return_decay', 0):.2%}"),
        ("test_win_rate", wf_report_dict.get("test_win_rate", 0), cfg.min_test_win_rate, ">=",
         f"OOS 盈利比 {wf_report_dict.get('test_win_rate', 0):.2%}"),
        ("test_sharpe", wf_report_dict.get("avg_test_sharpe", 0), cfg.min_test_sharpe, ">=",
         f"OOS 平均 Sharpe {wf_report_dict.get('avg_test_sharpe', 0):.2f}"),
    ]

    for metric, actual, threshold, op, desc in checks:
        if op == "<=":
            passed = actual <= threshold
        else:
            passed = actual >= threshold
        msg = f"{'✓' if passed else '✗'} {desc} {'满足' if passed else '不满足'} {op} {threshold}"
        gr = WalkForwardGateResult(metric=metric, actual_value=actual,
                                    threshold=threshold, passed=passed,
                                    severity=cfg.severity, message=msg)
        results.append(gr)
        if not passed:
            (errors if cfg.severity == "error" else warnings).append(gr)

    wf_score = wf_report_dict.get("score", wf_report_dict.get("wf_score", 0))
    wf_verdict = wf_report_dict.get("verdict", "NO_DATA")

    return WalkForwardGateReport(
        strategy_name=strategy_name,
        checked_at=datetime.now().isoformat(),
        all_passed=len(errors) == 0,
        wf_verdict=wf_verdict,
        wf_score=wf_score,
        results=results,
        errors=errors,
        warnings=warnings,
    )
