"""Paper/Live YAML 配置审计器 (§4.3.2 / §13.17 / §14.1)

审计 Paper 和 Live 配置是否符合以下架构约束:
- §4.3.2: risk 节必须存在 (至少 enabled: true + max_drawdown: 1.0)
- §13.17: 成本字段名必须使用规范名称
- §14.1: Fat-finger 防护参数必须存在
- §14.2: MDD 三级响应参数必须存在
- 组件权重须在合理范围内 (求和 ≈ 1.0)
- fill_model 节推荐存在
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# ── 审计结果数据结构 ───────────────────────────────────────────


@dataclass
class AuditFinding:
    """单条审计发现。"""

    level: str  # "ERROR" | "WARNING" | "INFO"
    rule: str  # 规则编号 e.g. "§4.3.2"
    message: str
    file: str = ""


@dataclass
class AuditReport:
    """审计报告汇总。"""

    findings: list[AuditFinding] = field(default_factory=list)

    @property
    def errors(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.level == "ERROR"]

    @property
    def warnings(self) -> list[AuditFinding]:
        return [f for f in self.findings if f.level == "WARNING"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        return (
            f"Audit: {len(self.errors)} errors, "
            f"{len(self.warnings)} warnings, "
            f"{'PASS' if self.passed else 'FAIL'}"
        )


# ── 规范化常量 ─────────────────────────────────────────────────

# §13.17: 规范成本字段名
CANONICAL_COST_FIELDS = {
    "commission_rate",
    "stamp_tax_rate",
    "transfer_fee_rate",
    "slippage_bps",
}

# §13.17: 旧字段名到新字段名映射
LEGACY_COST_FIELD_MAP = {
    "stamp_tax_sell": "stamp_tax_rate",
    "transfer_fee": "transfer_fee_rate",
    "stamp_tax": "stamp_tax_rate",
}


# ── 主审计函数 ─────────────────────────────────────────────────


def audit_paper_yaml(yaml_path: Path | str) -> AuditReport:
    """审计单个 Paper/Live YAML 配置文件。"""
    yaml_path = Path(yaml_path)
    report = AuditReport()
    fname = yaml_path.name

    try:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception as e:
        report.findings.append(AuditFinding(
            level="ERROR", rule="PARSE", message=f"YAML 解析失败: {e}", file=fname,
        ))
        return report

    if not raw or not isinstance(raw, dict):
        report.findings.append(AuditFinding(
            level="ERROR", rule="PARSE", message="YAML 内容为空或非 dict", file=fname,
        ))
        return report

    _check_risk_section(raw, report, fname)
    _check_cost_field_names(raw, report, fname)
    _check_fat_finger(raw, report, fname)
    _check_mdd_response(raw, report, fname)
    _check_component_weights(raw, report, fname)
    _check_fill_model(raw, report, fname)

    return report


def audit_paper_directory(
    paper_dir: Path | str,
    *,
    active_only: bool = False,
) -> dict[str, AuditReport]:
    """审计整个 Paper 配置目录。

    Args:
        paper_dir: configs/paper/ 目录路径
        active_only: 仅审计 system_settings.json 中的活跃配置

    Returns:
        {文件名: AuditReport} 映射
    """
    paper_dir = Path(paper_dir)
    results: dict[str, AuditReport] = {}

    if active_only:
        import json
        settings_path = paper_dir / "system_settings.json"
        if settings_path.exists():
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            active_names = settings.get("daily_batch_portfolios", [])
        else:
            active_names = []
            logger.warning("system_settings.json not found, auditing all")
            active_only = False

    for yml in sorted(paper_dir.glob("*.yml")):
        if active_only:
            # 匹配: v5a_shadow_combo.yml → "v5a" in active_names
            stem = yml.stem
            matched = any(
                stem == name or stem.startswith(name + "_") or name.startswith(stem)
                for name in active_names
            )
            if not matched:
                continue
        results[yml.name] = audit_paper_yaml(yml)

    return results


# ── 内部检查函数 ───────────────────────────────────────────────


def _check_risk_section(data: dict[str, Any], report: AuditReport, fname: str) -> None:
    """§4.3.2: risk 节必须存在。"""
    risk = data.get("risk")
    if risk is None:
        report.findings.append(AuditFinding(
            level="ERROR", rule="§4.3.2",
            message="缺少 'risk:' 节 (必须至少包含 enabled: true)",
            file=fname,
        ))
        return
    if not isinstance(risk, dict):
        report.findings.append(AuditFinding(
            level="ERROR", rule="§4.3.2",
            message="'risk:' 节必须是 dict 类型",
            file=fname,
        ))
        return
    # §4.3.2: max_drawdown 默认值必须为 1.0（禁用渐进减仓）
    mdd = risk.get("max_drawdown")
    if mdd is not None and float(mdd) < 1.0:
        # 检查防护参数是否齐全
        has_rotation = risk.get("drawdown_allow_rotation", False)
        floor = risk.get("drawdown_scale_factor_floor", 0)
        reset = risk.get("drawdown_peak_reset_days", 0)
        missing = []
        if not has_rotation:
            missing.append("drawdown_allow_rotation: true")
        if floor < 0.5:
            missing.append("drawdown_scale_factor_floor >= 0.5")
        if reset < 60:
            missing.append("drawdown_peak_reset_days >= 60")
        if missing:
            report.findings.append(AuditFinding(
                level="ERROR", rule="§4.3.2",
                message=(
                    f"max_drawdown={mdd} < 1.0 但缺少防护参数: "
                    f"{', '.join(missing)}"
                ),
                file=fname,
            ))


def _check_cost_field_names(data: dict[str, Any], report: AuditReport, fname: str) -> None:
    """§13.17: 成本字段名规范化检查。"""
    # 检查顶层和 components 内的成本字段
    fields_to_scan: list[dict[str, Any]] = []

    # 顶层 cost 节
    if "cost" in data and isinstance(data["cost"], dict):
        fields_to_scan.append(data["cost"])

    # components 列表内的成本字段
    components = data.get("components", [])
    if isinstance(components, list):
        for comp in components:
            if isinstance(comp, dict):
                fields_to_scan.append(comp)
    # pillars dict 内的成本字段
    pillars = data.get("pillars", {})
    if isinstance(pillars, dict):
        for pname, pconf in pillars.items():
            if isinstance(pconf, dict):
                fields_to_scan.append(pconf)

    for block in fields_to_scan:
        for old_name, new_name in LEGACY_COST_FIELD_MAP.items():
            if old_name in block:
                report.findings.append(AuditFinding(
                    level="WARNING", rule="§13.17",
                    message=f"使用旧字段名 '{old_name}'，应迁移为 '{new_name}'",
                    file=fname,
                ))


def _check_fat_finger(data: dict[str, Any], report: AuditReport, fname: str) -> None:
    """§14.1: Fat-finger 防护参数检查。"""
    has_single = "fat_finger_single_pct" in data
    has_component = "fat_finger_component_pct" in data
    if not has_single and not has_component:
        report.findings.append(AuditFinding(
            level="WARNING", rule="§14.1",
            message="缺少 fat_finger_single_pct / fat_finger_component_pct 参数",
            file=fname,
        ))


def _check_mdd_response(data: dict[str, Any], report: AuditReport, fname: str) -> None:
    """§14.2: MDD 三级响应检查。"""
    has_alert = "mdd_alert_threshold" in data
    has_halt = "mdd_halt_threshold" in data
    if not has_alert:
        report.findings.append(AuditFinding(
            level="WARNING", rule="§14.2",
            message="缺少 mdd_alert_threshold (飞书告警阈值)",
            file=fname,
        ))
    if not has_halt:
        report.findings.append(AuditFinding(
            level="WARNING", rule="§14.2",
            message="缺少 mdd_halt_threshold (紧急熔断阈值)",
            file=fname,
        ))
    # 验证告警阈值 < 熔断阈值
    if has_alert and has_halt:
        alert_val = float(data["mdd_alert_threshold"])
        halt_val = float(data["mdd_halt_threshold"])
        if alert_val >= halt_val:
            report.findings.append(AuditFinding(
                level="ERROR", rule="§14.2",
                message=(
                    f"mdd_alert_threshold ({alert_val}) >= "
                    f"mdd_halt_threshold ({halt_val}), 级别倒置"
                ),
                file=fname,
            ))


def _check_component_weights(data: dict[str, Any], report: AuditReport, fname: str) -> None:
    """组件权重合理性检查。"""
    weights: list[float] = []

    # 格式 A: components 列表
    components = data.get("components", [])
    if isinstance(components, list):
        for comp in components:
            if isinstance(comp, dict) and "weight" in comp:
                weights.append(float(comp["weight"]))

    # 格式 B: portfolio_weights dict
    pw = data.get("portfolio_weights", {})
    if isinstance(pw, dict) and not weights:
        weights = [float(v) for v in pw.values()]

    if weights:
        total = sum(weights)
        if abs(total - 1.0) > 0.05:
            report.findings.append(AuditFinding(
                level="ERROR", rule="WEIGHTS",
                message=f"组件权重之和 = {total:.4f}，偏离 1.0 超过 5%",
                file=fname,
            ))
        elif abs(total - 1.0) > 0.01:
            report.findings.append(AuditFinding(
                level="WARNING", rule="WEIGHTS",
                message=f"组件权重之和 = {total:.4f}，偏离 1.0 约 {abs(total-1.0)*100:.1f}%",
                file=fname,
            ))


def _check_fill_model(data: dict[str, Any], report: AuditReport, fname: str) -> None:
    """fill_model 节推荐检查。"""
    if "fill_model" not in data:
        report.findings.append(AuditFinding(
            level="INFO", rule="§4.4",
            message="未声明 fill_model 节 (将使用默认 next_bar_open)",
            file=fname,
        ))
