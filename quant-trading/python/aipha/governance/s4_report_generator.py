"""S4 验证报告生成器 — 组合 Paper Trading 阶段自动化报告。

将 go_live_gate 检查 + reconciliation 健康指标 + 配置审计结果
整合为一份结构化的 S4 验证报告，用于人工审批 S4→S5 转换。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from aipha.governance.go_live_gate import GoLiveReport, run_go_live_gate
from aipha.governance.paper_backtest_reconciliation import (
    check_nav_health,
    compute_s4_metrics,
    load_paper_nav,
    run_reconciliation,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class S4VerificationReport:
    """S4 阶段验证报告。"""

    combo_id: str
    generated_at: str = ""
    gate_report: GoLiveReport | None = None
    nav_health: dict[str, Any] = field(default_factory=dict)
    s4_metrics: dict[str, Any] = field(default_factory=dict)
    config_info: dict[str, Any] = field(default_factory=dict)
    verdict: str = "PENDING"  # PASS / BLOCKED / PENDING
    blocking_reasons: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """生成 Markdown 格式报告。"""
        lines = [
            f"# S4 验证报告 — {self.combo_id}",
            "",
            f"> 生成时间: {self.generated_at}",
            f"> 最终判定: **{self.verdict}**",
            "",
        ]
        lines.extend(self._section_gate())
        lines.extend(self._section_metrics())
        lines.extend(self._section_health())
        lines.extend(self._section_config())
        lines.extend(self._section_verdict())
        return "\n".join(lines)

    def _section_gate(self) -> list[str]:
        lines = ["## 1. Go-Live 门禁检查", ""]
        if not self.gate_report:
            lines.append("_未执行_")
            return lines + [""]
        lines.append(f"**汇总**: {self.gate_report.summary()}")
        lines.append("")
        lines.append("| 检查项 | 状态 | 说明 |")
        lines.append("|--------|------|------|")
        for c in self.gate_report.checks:
            icon = "✅" if c.passed else "❌"
            lines.append(f"| {c.name} | {icon} | {c.message} |")
        return lines + [""]

    def _section_metrics(self) -> list[str]:
        lines = ["## 2. S4 运行指标", ""]
        if not self.s4_metrics:
            lines.append("_Paper NAV 数据不足，无法计算指标_")
            return lines + [""]
        lines.append("| 指标 | 值 |")
        lines.append("|------|-----|")
        for k, v in self.s4_metrics.items():
            if isinstance(v, float):
                lines.append(f"| {k} | {v:.4f} |")
            else:
                lines.append(f"| {k} | {v} |")
        return lines + [""]

    def _section_health(self) -> list[str]:
        lines = ["## 3. NAV 健康度", ""]
        if not self.nav_health:
            lines.append("_无数据_")
            return lines + [""]
        lines.append(f"- 状态: **{self.nav_health.get('status', 'unknown')}**")
        lines.append(f"- 总天数: {self.nav_health.get('total_days', 0)}")
        lines.append(f"- 最大日跳幅: {self.nav_health.get('max_daily_jump_pct', 0):.2f}%")
        issues = self.nav_health.get("issues", [])
        if issues:
            lines.append("- 问题:")
            for issue in issues:
                lines.append(f"  - {issue}")
        return lines + [""]

    def _section_config(self) -> list[str]:
        lines = ["## 4. 配置信息", ""]
        if not self.config_info:
            lines.append("_未加载_")
            return lines + [""]
        for k, v in self.config_info.items():
            lines.append(f"- **{k}**: {v}")
        return lines + [""]

    def _section_verdict(self) -> list[str]:
        lines = ["## 5. 最终判定", ""]
        lines.append(f"**{self.verdict}**")
        if self.blocking_reasons:
            lines.append("")
            lines.append("### 阻塞原因")
            for r in self.blocking_reasons:
                lines.append(f"- ❌ {r}")
        if self.verdict == "PASS":
            lines.append("")
            lines.append("✅ 可以进入 S5 (小规模上线) 阶段")
        return lines + [""]


def generate_s4_report(
    combo_id: str,
    live_yaml_path: str | Path,
    *,
    min_paper_months: float = 3.0,
    output_path: Path | None = None,
) -> S4VerificationReport:
    """生成完整 S4 验证报告。"""
    report = S4VerificationReport(
        combo_id=combo_id,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )

    # 1. Go-Live 门禁
    report.gate_report = run_go_live_gate(
        combo_id, live_yaml_path, min_paper_months=min_paper_months
    )

    # 2. 加载 NAV 并计算指标
    nav = _load_nav_multi_alias(combo_id)
    if nav:
        health = check_nav_health(combo_id, nav)
        report.nav_health = {
            "status": health.status,
            "total_days": health.total_days,
            "max_daily_jump_pct": health.max_daily_jump_pct,
            "flat_streak_days": health.flat_streak_days,
            "issues": health.issues,
        }
        report.s4_metrics = compute_s4_metrics(combo_id, nav)

    # 3. 配置信息
    live_path = Path(live_yaml_path)
    if live_path.exists():
        report.config_info = {
            "live_config": str(live_path.name),
            "config_exists": True,
        }

    # 4. 判定
    blocking = []
    if not report.gate_report.all_passed:
        for c in report.gate_report.checks:
            if not c.passed:
                blocking.append(f"{c.name}: {c.message}")

    if report.nav_health.get("status") == "error":
        blocking.append(f"NAV 健康度异常: {report.nav_health.get('issues', [])}")

    report.blocking_reasons = blocking
    report.verdict = "PASS" if not blocking else "BLOCKED"

    # 5. 输出
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report.to_markdown(), encoding="utf-8")
        logger.info(f"S4 报告已生成: {output_path}")

    return report


def _load_nav_multi_alias(combo_id: str) -> dict[str, float]:
    """尝试多个别名加载 NAV。"""
    candidates = [combo_id, f"{combo_id}_shadow", f"live_{combo_id}"]
    for cid in candidates:
        nav = load_paper_nav(cid)
        if nav:
            return nav
    return {}
