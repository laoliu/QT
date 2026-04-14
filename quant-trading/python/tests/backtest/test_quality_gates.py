"""
质量门禁模块单元测试

测试覆盖：
1. QualityGate 数据结构
2. QualityChecker 检查逻辑
3. 默认配置
4. YAML 加载
5. IterationTracker 迭代记录
6. format_quality_report 格式化
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from aipha.backtest.quality_gates import (
    DEFAULT_QUALITY_GATES,
    GateResult,
    IterationRecord,
    IterationTracker,
    QualityChecker,
    QualityGate,
    QualityReport,
    format_quality_report,
)


# ============================================================================
# QualityChecker
# ============================================================================

class TestQualityChecker:
    def test_default_gates(self):
        checker = QualityChecker.default()
        assert len(checker.gates) > 0

    def test_all_pass(self):
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 0.3,
             "severity": "error", "description": "Sharpe >= 0.3"},
            {"metric": "max_drawdown_pct", "operator": "<=", "threshold": 0.30,
             "severity": "error", "description": "MDD <= 30%"},
        ])

        metrics = {"sharpe_ratio": 1.5, "max_drawdown_pct": 0.10}
        report = checker.check(metrics, strategy_name="test")

        assert report.all_passed is True
        assert len(report.errors) == 0

    def test_hard_gate_fail(self):
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 1.0,
             "severity": "error", "description": "Sharpe >= 1.0"},
        ])

        metrics = {"sharpe_ratio": 0.5}
        report = checker.check(metrics, strategy_name="test")

        assert report.all_passed is False
        assert len(report.errors) == 1
        assert "不满足" in report.errors[0].message

    def test_soft_warning_does_not_fail(self):
        checker = QualityChecker.from_config([
            {"metric": "win_rate", "operator": ">=", "threshold": 0.50,
             "severity": "warning", "description": "Win rate >= 50%"},
        ])

        metrics = {"win_rate": 0.40}
        report = checker.check(metrics, strategy_name="test")

        # warnings don't block
        assert report.all_passed is True
        assert len(report.warnings) == 1

    def test_missing_metric(self):
        checker = QualityChecker.from_config([
            {"metric": "nonexistent_metric", "operator": ">=", "threshold": 0.5,
             "severity": "error", "description": "test"},
        ])

        metrics = {"sharpe_ratio": 1.0}
        report = checker.check(metrics, strategy_name="test")

        assert report.all_passed is False  # missing metric = error

    def test_operators(self):
        checker = QualityChecker.default()

        # Test individual operators
        assert checker._compare(5, ">=", 5) is True
        assert checker._compare(5, ">=", 6) is False
        assert checker._compare(5, "<=", 5) is True
        assert checker._compare(5, "<=", 4) is False
        assert checker._compare(5, ">", 4) is True
        assert checker._compare(5, "<", 6) is True
        assert checker._compare(5, "==", 5) is True
        assert checker._compare(5, "!=", 6) is True

        # 浮点 epsilon 容差测试（M-3 修复）
        assert checker._compare(0.1 + 0.2, "==", 0.3) is True   # 经典浮点陷阱
        assert checker._compare(1.0000000001, "==", 1.0) is True # 差 < 1e-9
        assert checker._compare(1.001, "==", 1.0) is False       # 差 > 1e-9
        assert checker._compare(0.1 + 0.2, "!=", 0.3) is False
        assert checker._compare(1.5, "!=", 2.5) is True

    def test_from_yaml(self, tmp_path):
        yaml_content = """
quality_gates:
  - metric: sharpe_ratio
    operator: ">="
    threshold: 0.5
    severity: error
    description: "Test Sharpe"
"""
        yaml_file = tmp_path / "quality_gates.yml"
        yaml_file.write_text(yaml_content, encoding="utf-8")

        checker = QualityChecker.from_yaml(yaml_file)
        assert len(checker.gates) == 1
        assert checker.gates[0].metric == "sharpe_ratio"

    def test_from_yaml_missing_file(self, tmp_path):
        # Should use defaults when file doesn't exist
        checker = QualityChecker.from_yaml(tmp_path / "nonexistent.yml")
        assert len(checker.gates) == len(DEFAULT_QUALITY_GATES)


# ============================================================================
# format_quality_report
# ============================================================================

class TestFormatQualityReport:
    def test_format_passed(self):
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 0.3,
             "severity": "error", "description": "Sharpe"},
        ])
        report = checker.check({"sharpe_ratio": 1.0}, "test")
        text = format_quality_report(report)

        assert "全部通过" in text
        assert "test" in text

    def test_format_failed(self):
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 2.0,
             "severity": "error", "description": "Sharpe"},
        ])
        report = checker.check({"sharpe_ratio": 0.5}, "test")
        text = format_quality_report(report)

        assert "存在失败" in text
        assert "硬门禁失败" in text


# ============================================================================
# IterationTracker
# ============================================================================

class TestIterationTracker:
    def test_add_and_get(self, tmp_path):
        path = tmp_path / "iterations.json"
        tracker = IterationTracker(path)

        record = tracker.add_iteration(
            strategy_name="small_size",
            description="放宽 max_price 115→130",
            changes=[{"param": "max_price", "before": 115, "after": 130}],
            metrics_after={"sharpe_ratio": 1.2, "max_drawdown_pct": 0.15},
            quality_passed=True,
        )

        assert record.iteration_id == 1
        assert record.strategy_name == "small_size"

        # Get history
        history = tracker.get_history()
        assert len(history) == 1

    def test_persistence(self, tmp_path):
        path = tmp_path / "iterations.json"

        # Write
        tracker1 = IterationTracker(path)
        tracker1.add_iteration(
            strategy_name="test", description="iter 1",
            metrics_after={"sharpe_ratio": 1.0},
        )

        # Reload
        tracker2 = IterationTracker(path)
        history = tracker2.get_history()
        assert len(history) == 1
        assert history[0].description == "iter 1"

    def test_increment_id(self, tmp_path):
        path = tmp_path / "iterations.json"
        tracker = IterationTracker(path)

        r1 = tracker.add_iteration("s1", "first")
        r2 = tracker.add_iteration("s1", "second")

        assert r1.iteration_id == 1
        assert r2.iteration_id == 2

    def test_filter_by_strategy(self, tmp_path):
        path = tmp_path / "iterations.json"
        tracker = IterationTracker(path)

        tracker.add_iteration("strategy_a", "change 1")
        tracker.add_iteration("strategy_b", "change 2")
        tracker.add_iteration("strategy_a", "change 3")

        a_history = tracker.get_history(strategy_name="strategy_a")
        assert len(a_history) == 2

    def test_get_latest(self, tmp_path):
        path = tmp_path / "iterations.json"
        tracker = IterationTracker(path)

        tracker.add_iteration("s1", "first", metrics_after={"sr": 0.5})
        tracker.add_iteration("s1", "second", metrics_after={"sr": 1.0})

        latest = tracker.get_latest("s1")
        assert latest is not None
        assert latest.description == "second"

    def test_compare_iterations(self, tmp_path):
        path = tmp_path / "iterations.json"
        tracker = IterationTracker(path)

        tracker.add_iteration("s1", "v1", metrics_after={"sr": 0.5, "mdd": 0.20})
        tracker.add_iteration("s1", "v2", metrics_after={"sr": 1.0, "mdd": 0.15})

        comparison = tracker.compare_iterations(1, 2)
        assert "metrics_comparison" in comparison
        assert comparison["metrics_comparison"]["sr"]["delta"] == 0.5
        assert comparison["metrics_comparison"]["mdd"]["delta"] == pytest.approx(-0.05)

    def test_get_metric_trend(self, tmp_path):
        path = tmp_path / "iterations.json"
        tracker = IterationTracker(path)

        tracker.add_iteration("s1", "v1", metrics_after={"sr": 0.5})
        tracker.add_iteration("s1", "v2", metrics_after={"sr": 0.8})
        tracker.add_iteration("s1", "v3", metrics_after={"sr": 1.2})

        trend = tracker.get_metric_trend("sr")
        assert len(trend) == 3
        assert trend[0][2] == 0.5
        assert trend[2][2] == 1.2

    def test_format_history(self, tmp_path):
        path = tmp_path / "iterations.json"
        tracker = IterationTracker(path)

        tracker.add_iteration(
            "s1", "放宽 max_price",
            changes=[{"param": "max_price", "before": 115, "after": 130}],
            metrics_after={"sharpe_ratio": 1.2},
            quality_passed=True,
        )

        text = tracker.format_history()
        assert "迭代变更历史" in text
        assert "max_price" in text
        assert "115" in text
        assert "130" in text

    def test_empty_file(self, tmp_path):
        path = tmp_path / "iterations.json"
        tracker = IterationTracker(path)
        assert tracker.get_history() == []
        assert tracker.get_latest() is None
