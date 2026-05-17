"""Unified evaluation workflow — orchestrates backtest → validation → report → archive.

Single entry point for evaluating any strategy or combo:
    result = run_evaluation(yaml_path, report_type='single_strategy')

Steps:
1. Run backtest via ``run_from_config()``
2. Run statistical validation via ``ComprehensiveValidator``
3. Generate evaluation report (Markdown, P0-P16 structure)
4. Update catalog lifecycle_stage and eval_version
5. Register in archive index
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EvaluationResult:
    """Output of the unified evaluation workflow."""
    strategy_name: str
    yaml_path: str
    run_id: str = ""
    # Backtest metrics
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    calmar_ratio: float = 0.0
    # Validation
    validation_verdict: str = "PENDING"
    validation_score: float = 0.0
    dsr_significant: bool = False
    pbo_value: float | None = None
    wf_passed: bool = False
    # Segments
    post_rule_ar: float | None = None
    post_rule_sr: float | None = None
    # Report
    report_path: str = ""
    # Catalog update
    catalog_updated: bool = False
    lifecycle_stage: str = ""
    # Issues
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}


def run_evaluation(
    yaml_path: str | Path,
    report_type: str = "single_strategy",
    output_dir: str | Path | None = None,
    skip_backtest: bool = False,
    existing_result_dir: str | Path | None = None,
    param_overrides: dict | None = None,
) -> EvaluationResult:
    """Run unified evaluation workflow.

    Args:
        yaml_path: Path to backtest YAML config
        report_type: 'single_strategy' or 'combo'
        output_dir: Override output directory
        skip_backtest: If True, load existing results instead of running
        existing_result_dir: Path to existing backtest output (when skip_backtest=True)
        param_overrides: Dict of parameter overrides for backtest

    Returns:
        EvaluationResult with all metrics, validation, and report path
    """
    yaml_path = Path(yaml_path)
    strategy_name = yaml_path.stem.replace("_8y", "").replace("_11y", "")

    out = Path(output_dir) if output_dir else Path("output") / f"eval_{strategy_name}"
    out.mkdir(parents=True, exist_ok=True)

    result = EvaluationResult(strategy_name=strategy_name, yaml_path=str(yaml_path))

    # Step 1: Backtest
    if skip_backtest and existing_result_dir:
        summary_path = Path(existing_result_dir) / "summary.json"
        if summary_path.exists():
            bt_result = json.loads(summary_path.read_text(encoding="utf-8"))
        else:
            result.issues.append(f"summary.json not found in {existing_result_dir}")
            return result
    else:
        logger.info("Step 1: Running backtest for %s", strategy_name)
        bt_result = _run_backtest_step(yaml_path, out, param_overrides)

    # Extract core metrics
    metrics = bt_result.get("metrics", bt_result)
    result.annual_return = metrics.get("annual_return", metrics.get("annualized_return", 0))
    result.sharpe_ratio = metrics.get("sharpe_ratio", 0)
    result.max_drawdown = metrics.get("max_drawdown", 0)
    result.calmar_ratio = metrics.get("calmar_ratio", 0)
    result.run_id = bt_result.get("run_id", "")

    # Step 2: Validation
    logger.info("Step 2: Running statistical validation")
    val_result = _run_validation_step(bt_result, out)
    result.validation_verdict = val_result.get("verdict", "PENDING")
    result.validation_score = val_result.get("score", 0)
    result.dsr_significant = val_result.get("dsr", {}).get("is_significant", False)
    result.wf_passed = val_result.get("wf_gates", {}).get("passed", False)

    # Extract post-rule segment metrics
    for seg in val_result.get("segments", []):
        if "post" in seg.get("label", "").lower() or "新规后" in seg.get("label", ""):
            result.post_rule_ar = seg.get("annual_return")
            result.post_rule_sr = seg.get("sharpe_ratio")
            break

    # Determine lifecycle stage
    if result.validation_verdict == "PASS" and result.sharpe_ratio >= 0.8:
        result.lifecycle_stage = "backtest_passed"
    elif result.annual_return > 0 and result.sharpe_ratio > 0:
        result.lifecycle_stage = "research"
    else:
        result.lifecycle_stage = "research"

    # Step 3: Generate report
    logger.info("Step 3: Generating evaluation report")
    report_path = _generate_report(
        strategy_name, bt_result, val_result, yaml_path, out, report_type
    )
    result.report_path = str(report_path)

    # Step 4: Update catalog
    logger.info("Step 4: Updating catalog")
    result.catalog_updated = _update_catalog(strategy_name, result)
    result.issues = val_result.get("issues", [])

    logger.info("Evaluation complete: %s verdict=%s score=%.1f",
                strategy_name, result.validation_verdict, result.validation_score)
    return result


def _run_backtest_step(
    yaml_path: Path,
    output_dir: Path | None,
    param_overrides: dict | None,
) -> dict[str, Any]:
    """Step 1: Run backtest via config_runner."""
    from aipha.backtest.config_runner import run_from_config

    kwargs: dict[str, Any] = {}
    if output_dir:
        kwargs["output_dir"] = str(output_dir)
    if param_overrides:
        kwargs["param_overrides"] = param_overrides

    result = run_from_config(str(yaml_path), **kwargs)

    # Extract metrics from result
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result

    # Try to load from output summary.json
    if output_dir:
        summary_path = Path(output_dir) / "summary.json"
        if summary_path.exists():
            return json.loads(summary_path.read_text(encoding="utf-8"))

    return {"error": "could not extract backtest result"}


def _run_validation_step(
    result: dict[str, Any],
    output_dir: Path,
) -> dict[str, Any]:
    """Step 2: Run statistical validation using ComprehensiveValidator."""
    from aipha.backtest.comprehensive_validation import (
        ComprehensiveValidator,
        ValidationConfig,
    )

    metrics = result.get("metrics", result)
    daily_returns = result.get("daily_returns", [])
    nav_dates = result.get("nav_dates", [])
    strategy_name = result.get("strategy_name", "unknown")

    if not daily_returns:
        logger.warning("No daily_returns in result, validation limited")
        return {"verdict": "SKIP", "reason": "no daily returns"}

    validator = ComprehensiveValidator(ValidationConfig())
    report = validator.validate(
        strategy_name=strategy_name,
        daily_returns=daily_returns,
        nav_dates=nav_dates,
    )

    val_dict = report.to_dict()
    # Save validation report
    val_path = output_dir / "validation_report.json"
    val_path.write_text(
        json.dumps(val_dict, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return val_dict


def _generate_report(
    strategy_name: str,
    bt_result: dict[str, Any],
    val_result: dict[str, Any],
    yaml_path: Path,
    output_dir: Path,
    report_type: str,
) -> Path:
    """Step 3: Generate evaluation report markdown (P0-P16 structure)."""
    today = date.today().isoformat()
    metrics = bt_result.get("metrics", bt_result)
    ar = metrics.get("annual_return", metrics.get("annualized_return", 0))
    sr = metrics.get("sharpe_ratio", 0)
    mdd = metrics.get("max_drawdown", 0)
    calmar = metrics.get("calmar_ratio", 0)

    verdict = val_result.get("verdict", "PENDING")
    score = val_result.get("score", 0)
    dsr = val_result.get("dsr", {})
    segments = val_result.get("segments", [])
    issues = val_result.get("issues", [])

    # Build report sections
    lines = [
        f"# {strategy_name} 策略评估报告",
        "",
        "## P0: 元信息",
        f"- 报告日期: {today}",
        f"- 报告类型: {report_type}",
        f"- 配置文件: `{yaml_path.name}`",
        f"- 综合评分: {score:.1f}/10",
        f"- 综合判定: **{verdict}**",
        "",
        "## P1: 策略信息",
        f"- 策略名称: {strategy_name}",
        f"- 资产类别: {bt_result.get('asset_type', 'unknown')}",
        "",
        "## P4: 核心绩效指标",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 年化收益 (AR) | {ar:.2%} |",
        f"| 夏普比率 (SR) | {sr:.3f} |",
        f"| 最大回撤 (MDD) | {mdd:.2%} |",
        f"| 卡尔马比率 | {calmar:.3f} |",
        "",
    ]

    # P8: Statistical validation
    lines.extend([
        "## P8: 统计验证",
        "",
        f"- DSR 显著: {dsr.get('is_significant', 'N/A')}",
        f"- DSR p值: {dsr.get('p_value', 'N/A')}",
        f"- 排列检验: {val_result.get('permutation', {}).get('is_significant', 'N/A')}",
        "",
    ])

    # P9: Segmented performance
    if segments:
        lines.extend([
            "## P9: 分段绩效",
            "",
            "| 阶段 | AR | SR | MDD |",
            "|------|-----|-----|------|",
        ])
        for seg in segments:
            lines.append(
                f"| {seg.get('label', '?')} "
                f"| {seg.get('annual_return', 0):.2%} "
                f"| {seg.get('sharpe_ratio', 0):.3f} "
                f"| {seg.get('max_drawdown', 0):.2%} |"
            )
        lines.append("")

    # P15: Issues
    if issues:
        lines.extend(["## P15: 问题与风险", ""])
        for iss in issues:
            lines.append(f"- {iss}")
        lines.append("")

    # P16: Verdict
    lines.extend([
        "## P16: 综合评级",
        "",
        f"- 评分: {score:.1f}/10",
        f"- 判定: **{verdict}**",
        "",
    ])

    report_path = output_dir / f"{strategy_name}_eval_{today}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def _update_catalog(
    strategy_name: str,
    eval_result: EvaluationResult,
) -> bool:
    """Step 4: Update strategy_catalog.json with evaluation results."""
    catalog_path = Path(__file__).resolve().parents[3] / "configs" / "strategy_catalog.json"
    if not catalog_path.exists():
        logger.warning("Catalog not found at %s", catalog_path)
        return False

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    updated = False
    today = date.today().isoformat()

    for group in catalog.values():
        if not isinstance(group, list):
            continue
        for strat in group:
            if strat.get("name") != strategy_name:
                continue
            strat["eval_version"] = eval_result.run_id or today
            strat["last_review_date"] = today
            strat["lifecycle_stage"] = eval_result.lifecycle_stage
            strat["evidence_level"] = (
                "validated" if eval_result.validation_verdict == "PASS"
                else "backtest_only"
            )
            if eval_result.run_id:
                strat["evidence_run_id"] = eval_result.run_id
            if eval_result.lifecycle_stage == "paper_candidate":
                strat["s4_entry_date"] = today
            updated = True
            break
        if updated:
            break

    if updated:
        catalog_path.write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return updated
