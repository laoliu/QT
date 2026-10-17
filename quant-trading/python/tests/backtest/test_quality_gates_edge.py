"""Quality Gates 边界/异常场景测试。

覆盖:
- QualityChecker 边界: 空 gates, 空 metrics, 非数值, NaN/Inf, 未知 operator, 边界值
- YAML 加载边界: 格式错误, 空文件, 扁平列表, 额外字段, 真实配置
- 别名补充: annualised_return, max_dd
- format_quality_report 混合场景
- IterationTracker 边界: 损坏 JSON, compare 不存在, limit, trend 缺失指标
"""
from __future__ import annotations

import json
import math
import tempfile
from aipha.utils.paths import project_path

import pytest

from aipha.backtest.quality_gates import (
    DEFAULT_QUALITY_GATES,
    GateResult,
    IterationTracker,
    QualityChecker,
    QualityGate,
    QualityReport,
    format_quality_report,
)


# ============================================================================
# QualityChecker 边界
# ============================================================================

class TestQualityCheckerEdge:
    """QualityChecker 边界与异常场景。"""

    def test_empty_gates_all_pass(self):
        """空 gates 列表 → check() 应全部通过。"""
        checker = QualityChecker(gates=[])
        report = checker.check({"sharpe_ratio": 0.5}, "empty_gates")
        assert report.all_passed is True
        assert len(report.results) == 0

    def test_empty_metrics_all_missing(self):
        """空 metrics → 所有 gate 报缺失。"""
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 0.5,
             "severity": "error", "description": "Sharpe"},
            {"metric": "win_rate", "operator": ">=", "threshold": 0.4,
             "severity": "warning", "description": "WinRate"},
        ])
        report = checker.check({}, "empty_metrics")
        assert report.all_passed is False
        assert len(report.errors) == 1
        assert len(report.warnings) == 1
        assert "不存在" in report.errors[0].message

    def test_non_numeric_string_value(self):
        """字符串值无法转为数值 → 视为失败。"""
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 0.5,
             "severity": "error", "description": "Sharpe"},
        ])
        report = checker.check({"sharpe_ratio": "not_a_number"}, "str_val")
        assert report.all_passed is False
        assert "无法转为数值" in report.errors[0].message

    def test_none_value_treated_as_missing(self):
        """metrics 中值为 None → 视为缺失。"""
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 0.5,
             "severity": "error", "description": "Sharpe"},
        ])
        report = checker.check({"sharpe_ratio": None}, "none_val")
        assert report.all_passed is False
        assert "不存在" in report.errors[0].message

    def test_nan_value(self):
        """NaN 比较行为：NaN >= 0.5 → False。"""
        checker = QualityChecker.from_config([
            {"metric": "x", "operator": ">=", "threshold": 0.5,
             "severity": "error", "description": "NaN test"},
        ])
        report = checker.check({"x": float("nan")}, "nan")
        assert report.all_passed is False

    def test_inf_value_passes_gte(self):
        """Inf >= threshold → True。"""
        checker = QualityChecker.from_config([
            {"metric": "x", "operator": ">=", "threshold": 100.0,
             "severity": "error", "description": "Inf test"},
        ])
        report = checker.check({"x": float("inf")}, "inf")
        assert report.all_passed is True

    def test_neg_inf_fails_gte(self):
        """-Inf >= threshold → False。"""
        checker = QualityChecker.from_config([
            {"metric": "x", "operator": ">=", "threshold": -100,
             "severity": "error", "description": "-Inf test"},
        ])
        report = checker.check({"x": float("-inf")}, "neg_inf")
        assert report.all_passed is False

    def test_unknown_operator_returns_false(self):
        """不支持的 operator → _compare 返回 False → 不通过。"""
        checker = QualityChecker.from_config([
            {"metric": "x", "operator": "~=", "threshold": 1.0,
             "severity": "error", "description": "bad op"},
        ])
        report = checker.check({"x": 1.0}, "bad_op")
        assert report.all_passed is False

    def test_boundary_gte_exact_equal(self):
        """边界: 值恰好等于阈值，>= 应通过。"""
        checker = QualityChecker.from_config([
            {"metric": "x", "operator": ">=", "threshold": 1.0,
             "severity": "error", "description": "boundary"},
        ])
        report = checker.check({"x": 1.0}, "bound")
        assert report.all_passed is True

    def test_boundary_gt_exact_equal_fails(self):
        """边界: 值恰好等于阈值，> 应失败。"""
        checker = QualityChecker.from_config([
            {"metric": "x", "operator": ">", "threshold": 1.0,
             "severity": "error", "description": "boundary strict"},
        ])
        report = checker.check({"x": 1.0}, "bound_strict")
        assert report.all_passed is False

    def test_multiple_strategies_no_state_leak(self):
        """同一 checker 对不同 metrics 执行 check() 无状态泄漏。"""
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 1.0,
             "severity": "error", "description": "Sharpe"},
        ])
        r1 = checker.check({"sharpe_ratio": 0.5}, "strategy_a")
        r2 = checker.check({"sharpe_ratio": 2.0}, "strategy_b")
        assert r1.all_passed is False
        assert r2.all_passed is True
        assert r1.strategy_name == "strategy_a"
        assert r2.strategy_name == "strategy_b"


# ============================================================================
# YAML 加载边界
# ============================================================================

class TestYAMLLoadingEdge:
    """from_yaml 边界场景。"""

    def test_yaml_not_a_list(self, tmp_path):
        """quality_gates 值不是 list → fallback 默认。"""
        yaml_file = tmp_path / "bad.yml"
        yaml_file.write_text("quality_gates: not_a_list\n", encoding="utf-8")
        checker = QualityChecker.from_yaml(yaml_file)
        assert len(checker.gates) == len(DEFAULT_QUALITY_GATES)

    def test_yaml_empty_file(self, tmp_path):
        """空 YAML 文件 → fallback 默认。"""
        yaml_file = tmp_path / "empty.yml"
        yaml_file.write_text("", encoding="utf-8")
        checker = QualityChecker.from_yaml(yaml_file)
        # yaml.safe_load("") returns None → data is None → fallback
        assert len(checker.gates) == len(DEFAULT_QUALITY_GATES)

    def test_yaml_flat_list(self, tmp_path):
        """顶层直接是列表（无 quality_gates 键）→ 应正确解析。"""
        content = """
- metric: sharpe_ratio
  operator: ">="
  threshold: 0.8
  severity: error
  description: "Sharpe"
"""
        yaml_file = tmp_path / "flat.yml"
        yaml_file.write_text(content, encoding="utf-8")
        checker = QualityChecker.from_yaml(yaml_file)
        # data.get("quality_gates", data) → data is list → from_config(list)
        assert len(checker.gates) == 1
        assert checker.gates[0].threshold == 0.8

    def test_real_config_loads(self):
        """读取真实的 configs/quality_gates.yml 并验证不腐败。"""
        config_path = project_path("configs/quality_gates.yml")
        if not config_path.exists():
            pytest.skip("configs/quality_gates.yml not found")
        checker = QualityChecker.from_yaml(config_path)
        assert len(checker.gates) >= 10
        metrics_set = {g.metric for g in checker.gates}
        assert "sharpe_ratio" in metrics_set

    def test_yaml_with_quality_gates_key(self, tmp_path):
        """有 quality_gates 键的正常 YAML 解析验证。"""
        content = """
quality_gates:
  - metric: total_return
    operator: ">"
    threshold: 0.0
    severity: error
    description: "Positive return"
  - metric: win_rate
    operator: ">="
    threshold: 0.3
    severity: warning
    description: "Win rate"
"""
        yaml_file = tmp_path / "normal.yml"
        yaml_file.write_text(content, encoding="utf-8")
        checker = QualityChecker.from_yaml(yaml_file)
        assert len(checker.gates) == 2
        severities = {g.severity for g in checker.gates}
        assert severities == {"error", "warning"}


# ============================================================================
# 别名补充
# ============================================================================

class TestMetricAliasesEdge:
    """补充别名测试: annualised_return, max_dd, 双向查找。"""

    def test_annualised_return_british_spelling(self):
        """英式拼写 annualised_return 别名可用。"""
        checker = QualityChecker.from_config([
            {"metric": "annualised_return", "operator": ">=", "threshold": 0.05,
             "severity": "error", "description": "return"},
        ])
        # 实际 metrics 用标准名 annualized_return
        report = checker.check({"annualized_return": 0.10}, "alias")
        assert report.all_passed is True

    def test_max_dd_alias(self):
        """max_dd 别名 → max_drawdown_pct。"""
        checker = QualityChecker.from_config([
            {"metric": "max_dd", "operator": "<=", "threshold": 0.30,
             "severity": "error", "description": "MDD"},
        ])
        report = checker.check({"max_drawdown_pct": 0.20}, "alias")
        assert report.all_passed is True

    def test_canonical_name_with_alias_in_metrics(self):
        """gate 用 canonical 名, metrics 用别名 → 反向查找成功。"""
        checker = QualityChecker.from_config([
            {"metric": "annualized_return", "operator": ">=", "threshold": 0.05,
             "severity": "error", "description": "return"},
        ])
        # metrics 中只有别名
        report = checker.check({"annual_return": 0.10}, "reverse")
        assert report.all_passed is True


# ============================================================================
# format_quality_report 补充
# ============================================================================

class TestFormatReportEdge:
    """format_quality_report 混合与边界场景。"""

    def test_mixed_errors_and_warnings(self):
        """同时包含 error 失败和 warning 告警的格式化。"""
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 2.0,
             "severity": "error", "description": "Sharpe"},
            {"metric": "win_rate", "operator": ">=", "threshold": 0.9,
             "severity": "warning", "description": "WinRate"},
        ])
        report = checker.check({"sharpe_ratio": 0.5, "win_rate": 0.3}, "mixed")
        text = format_quality_report(report)
        assert "硬门禁失败" in text
        assert "软告警" in text
        assert "存在失败" in text

    def test_missing_metric_format(self):
        """缺失指标的格式化输出包含 '不存在'。"""
        checker = QualityChecker.from_config([
            {"metric": "nonexistent", "operator": ">=", "threshold": 1.0,
             "severity": "error", "description": "Missing"},
        ])
        report = checker.check({}, "missing")
        text = format_quality_report(report)
        assert "不存在" in text

    def test_empty_report_format(self):
        """空 gates 报告也能正常格式化。"""
        checker = QualityChecker(gates=[])
        report = checker.check({}, "empty")
        text = format_quality_report(report)
        assert "全部通过" in text


# ============================================================================
# IterationTracker 边界
# ============================================================================

class TestIterationTrackerEdge:
    """IterationTracker 边界场景。"""

    def test_corrupted_json_graceful(self, tmp_path):
        """损坏的 JSON 文件 → 清空重建。"""
        path = tmp_path / "bad.json"
        path.write_text("{broken json!!!", encoding="utf-8")
        tracker = IterationTracker(path)
        assert tracker.get_history() == []
        # 仍能正常添加
        rec = tracker.add_iteration("s1", "after corruption",
                                    metrics_after={"sr": 1.0})
        assert rec.iteration_id == 1

    def test_compare_nonexistent_ids(self, tmp_path):
        """compare_iterations 找不到 id → 返回 error。"""
        path = tmp_path / "iter.json"
        tracker = IterationTracker(path)
        tracker.add_iteration("s1", "v1", metrics_after={"sr": 1.0})
        result = tracker.compare_iterations(1, 999)
        assert "error" in result

    def test_get_history_with_limit(self, tmp_path):
        """get_history limit 参数正确限制结果数。"""
        path = tmp_path / "iter.json"
        tracker = IterationTracker(path)
        for i in range(5):
            tracker.add_iteration("s1", f"v{i}", metrics_after={"sr": float(i)})
        limited = tracker.get_history(limit=3)
        assert len(limited) == 3
        # 返回最后 3 条
        assert limited[0].iteration_id == 3

    def test_metric_trend_missing_metric(self, tmp_path):
        """get_metric_trend 对不存在的指标 → 全部返回 None。"""
        path = tmp_path / "iter.json"
        tracker = IterationTracker(path)
        tracker.add_iteration("s1", "v1", metrics_after={"sr": 1.0})
        tracker.add_iteration("s1", "v2", metrics_after={"sr": 2.0})
        trend = tracker.get_metric_trend("nonexistent")
        assert len(trend) == 2
        assert all(t[2] is None for t in trend)

    def test_get_latest_no_match(self, tmp_path):
        """get_latest 过滤无匹配 → None。"""
        path = tmp_path / "iter.json"
        tracker = IterationTracker(path)
        tracker.add_iteration("s1", "v1")
        assert tracker.get_latest("no_such_strategy") is None

    def test_format_empty_history(self, tmp_path):
        """空历史格式化。"""
        path = tmp_path / "iter.json"
        tracker = IterationTracker(path)
        text = tracker.format_history()
        assert "无迭代记录" in text


# ============================================================================
# QualityReport asdict 兼容性
# ============================================================================

class TestQualityReportSerialization:
    """验证 QualityReport 可序列化为 dict/JSON。"""

    def test_asdict_roundtrip(self):
        """QualityReport 可通过 dataclasses.asdict 序列化。"""
        from dataclasses import asdict
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 0.5,
             "severity": "error", "description": "Sharpe"},
        ])
        report = checker.check({"sharpe_ratio": 1.0}, "test_ser")
        d = asdict(report)
        assert isinstance(d, dict)
        assert d["strategy_name"] == "test_ser"
        assert d["all_passed"] is True
        # 可 JSON 序列化
        json_str = json.dumps(d, ensure_ascii=False)
        assert "test_ser" in json_str

    def test_asdict_with_failure(self):
        """失败报告也能序列化。"""
        from dataclasses import asdict
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 5.0,
             "severity": "error", "description": "High Sharpe"},
        ])
        report = checker.check({"sharpe_ratio": 0.1}, "fail_ser")
        d = asdict(report)
        assert d["all_passed"] is False
        assert len(d["errors"]) == 1
