"""Evaluation report template validator.

Validates that evaluation reports follow the standard template
(docs/templates/evaluation_report_template.md) with required sections.

Per §12.2-12.7:
- P2 (backtest config validation) is MANDATORY for all evaluation reports
- P10 (five principles check) is MANDATORY
- P14 (rating & conclusion) is MANDATORY
- P16 (experiment fingerprint) is MANDATORY
- Single strategy: P0-P10, P13-P16
- Combo evaluation: P0-P11, P13-P16
- Expert review: P0-P16 (all)

Usage:
    from aipha.governance.report_validator import validate_report
    result = validate_report("docs/reports/formal_evaluations/xxx.md")
    if not result.passed:
        print(result.summary())
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Standard section headers (§12.1)
SECTION_HEADERS = {
    "P0": "报告元信息",
    "P1": "策略/模型基本信息",
    "P2": "回测配置验证清单",
    "P3": "数据完整性确认",
    "P4": "核心绩效指标",
    "P5": "风险分析",
    "P6": "因子/特征分析",
    "P7": "成本与容量分析",
    "P8": "统计验证与过拟合检测",
    "P9": "分段验证与制度变更分析",
    "P10": "五原则合规检查",
    "P11": "组合层分析",
    "P12": "专家评审意见",
    "P13": "风险矩阵与已知局限",
    "P14": "综合评级与结论",
    "P15": "后续行动项",
    "P16": "实验指纹与可复现性",
}

# Required sections by report type (§12.3)
REQUIRED_SECTIONS = {
    "single_strategy": ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P13", "P14", "P15", "P16"],
    "combo": ["P0", "P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11", "P13", "P14", "P15", "P16"],
    "expert_review": [f"P{i}" for i in range(17)],
    "deployment_review": ["P0", "P12", "P13", "P14", "P15", "P16"],
}

# Always required regardless of type (§12.2 core rules)
ALWAYS_REQUIRED = ["P2", "P10", "P14", "P16"]

# P2 must contain these cost/fee parameters (§12.4)
P2_REQUIRED_PARAMS = [
    "佣金",
    "印花税",
    "滑点",
    "成交",
    "Universe",
]

# Section header regex
_SECTION_RE = re.compile(r"^##\s+P(\d+)[.\s]", re.MULTILINE)


@dataclass
class ReportCheck:
    """Single report validation check."""
    check_id: str
    name: str
    passed: bool
    severity: str = "error"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ReportValidationResult:
    """Aggregate report validation result."""
    file_path: str
    report_type: str
    checks: list[ReportCheck] = field(default_factory=list)
    found_sections: list[str] = field(default_factory=list)
    missing_sections: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks if c.severity == "error")

    @property
    def error_count(self) -> int:
        return sum(1 for c in self.checks if not c.passed and c.severity == "error")

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        lines = [f"[{status}] {self.file_path} (type={self.report_type})"]
        lines.append(f"  Sections found: {len(self.found_sections)}/{len(self.found_sections) + len(self.missing_sections)}")
        if self.missing_sections:
            lines.append(f"  Missing: {', '.join(self.missing_sections)}")
        for c in self.checks:
            if not c.passed:
                mark = "✗"
                lines.append(f"  {mark} [{c.check_id}] {c.name}: {c.detail}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "report_type": self.report_type,
            "passed": self.passed,
            "error_count": self.error_count,
            "found_sections": self.found_sections,
            "missing_sections": self.missing_sections,
            "checks": [c.to_dict() for c in self.checks],
        }


def detect_report_type(content: str) -> str:
    """Auto-detect report type from content."""
    lower = content.lower()
    if "专家评审" in lower or "expert review" in lower:
        return "expert_review"
    if "组合" in lower and ("P11" in content or "组合层" in lower):
        return "combo"
    if "部署" in lower or "deployment" in lower:
        return "deployment_review"
    return "single_strategy"


def _extract_sections(content: str) -> list[str]:
    """Extract P-section identifiers found in the document."""
    matches = _SECTION_RE.findall(content)
    return [f"P{m}" for m in matches]


def _check_p2_content(content: str) -> list[ReportCheck]:
    """Validate P2 contains required cost/fee parameters (§12.4)."""
    checks = []
    # Find P2 section content (between ## P2 and next ## P)
    p2_match = re.search(r"##\s+P2[.\s].*?(?=##\s+P\d|$)", content, re.DOTALL)
    if not p2_match:
        checks.append(ReportCheck(
            check_id="P2-EXISTS", name="p2_section_present",
            passed=False, detail="P2 section not found",
        ))
        return checks

    p2_text = p2_match.group()
    for param in P2_REQUIRED_PARAMS:
        found = param in p2_text
        checks.append(ReportCheck(
            check_id=f"P2-{param[:4].upper()}", name=f"p2_has_{param}",
            passed=found,
            detail=f"'{param}' {'found' if found else 'MISSING'} in P2",
        ))
    return checks


def _check_p10_content(content: str) -> list[ReportCheck]:
    """Validate P10 contains five principles check (§12.2 rule 3)."""
    checks = []
    p10_match = re.search(r"##\s+P10[.\s].*?(?=##\s+P\d|$)", content, re.DOTALL)
    if not p10_match:
        checks.append(ReportCheck(
            check_id="P10-EXISTS", name="p10_section_present",
            passed=False, detail="P10 section not found",
        ))
        return checks

    p10_text = p10_match.group()
    for principle in ["A", "B", "C", "D", "E"]:
        pattern = rf"原则\s*{principle}|Principle\s*{principle}"
        found = bool(re.search(pattern, p10_text))
        checks.append(ReportCheck(
            check_id=f"P10-PRINC-{principle}", name=f"p10_principle_{principle}",
            passed=found,
            severity="error",
            detail=f"Principle {principle} {'covered' if found else 'MISSING'}",
        ))
    return checks


def _check_p9_segmented(content: str) -> list[ReportCheck]:
    """Validate P9 contains segmented analysis (§12.2 rule 4)."""
    checks = []
    p9_match = re.search(r"##\s+P9[.\s].*?(?=##\s+P\d|$)", content, re.DOTALL)
    if not p9_match:
        return checks  # P9 existence checked separately

    p9_text = p9_match.group()
    has_segment = bool(re.search(r"全量期|新规后|2022|分段|segment", p9_text))
    checks.append(ReportCheck(
        check_id="P9-SEGMENT", name="p9_segmented_analysis",
        passed=has_segment,
        severity="error",
        detail="segmented analysis present" if has_segment else "no segmented/period analysis found",
    ))
    return checks


def validate_report(
    file_path: str | Path,
    *,
    report_type: str | None = None,
) -> ReportValidationResult:
    """Validate an evaluation report against the standard template.

    Args:
        file_path: Path to the markdown report file
        report_type: Override auto-detection ("single_strategy"|"combo"|"expert_review"|"deployment_review")

    Returns:
        ReportValidationResult with pass/fail and detailed checks
    """
    path = Path(file_path)
    content = path.read_text(encoding="utf-8")

    if report_type is None:
        report_type = detect_report_type(content)

    result = ReportValidationResult(
        file_path=str(path),
        report_type=report_type,
    )

    # Check required sections present
    found = _extract_sections(content)
    result.found_sections = found
    required = REQUIRED_SECTIONS.get(report_type, ALWAYS_REQUIRED)

    for section in required:
        if section not in found:
            result.missing_sections.append(section)
            severity = "error" if section in ALWAYS_REQUIRED else "warning"
            result.checks.append(ReportCheck(
                check_id=f"SECTION-{section}", name=f"section_{section}_present",
                passed=False, severity=severity,
                detail=f"Required section {section} ({SECTION_HEADERS.get(section, '')}) missing",
            ))

    # Content-level checks for key sections
    result.checks.extend(_check_p2_content(content))
    result.checks.extend(_check_p10_content(content))
    result.checks.extend(_check_p9_segmented(content))

    return result


def validate_reports_in_dir(
    directory: str | Path,
    *,
    pattern: str = "*.md",
) -> list[ReportValidationResult]:
    """Validate all reports in a directory."""
    results = []
    for path in sorted(Path(directory).glob(pattern)):
        try:
            results.append(validate_report(path))
        except Exception as e:
            logger.warning("Failed to validate %s: %s", path, e)
    return results
