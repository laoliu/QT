"""Walk-Forward 质量门禁 单元测试

验证:
1. WalkForwardGateConfig 默认值与自定义
2. check_walk_forward_gates 各项检查逻辑（通过/失败/边界）
3. load_wf_gate_config 从 YAML 加载
4. format_wf_gate_report 格式化输出
5. 与真实 WF 报告字典的集成
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from aipha.backtest.quality_gates import (
    WalkForwardGateConfig,
    WalkForwardGateReport,
    WalkForwardGateResult,
    check_walk_forward_gates,
    format_wf_gate_report,
    load_wf_gate_config,
)


# ============================================================================
# 辅助数据
# ============================================================================

def _good_wf_dict() -> dict:
    """OOS 表现好的 WF 报告字典"""
    return {
        "avg_sharpe_decay": 0.30,
        "avg_return_decay": 0.40,
        "test_win_rate": 0.80,
        "avg_test_sharpe": 1.5,
        "score": 7.5,
        "verdict": "PASS",
    }


def _bad_wf_dict() -> dict:
    """OOS 表现差的 WF 报告字典"""
    return {
        "avg_sharpe_decay": 0.90,
        "avg_return_decay": 0.95,
        "test_win_rate": 0.20,
        "avg_test_sharpe": -0.5,
        "score": 2.0,
        "verdict": "FAIL",
    }


# ============================================================================
# TestWalkForwardGateConfig
# ============================================================================

class TestWalkForwardGateConfig:
    """WF 门禁配置测试"""

    def test_default_values(self):
        cfg = WalkForwardGateConfig()
        assert cfg.max_sharpe_decay == 0.60
        assert cfg.max_return_decay == 0.80
        assert cfg.min_test_win_rate == 0.50
        assert cfg.min_test_sharpe == 0.0
        assert cfg.min_wf_score == 5.0
        assert cfg.severity == "warning"

    def test_custom_values(self):
        cfg = WalkForwardGateConfig(
            max_sharpe_decay=0.40,
            min_wf_score=7.0,
            severity="error",
        )
        assert cfg.max_sharpe_decay == 0.40
        assert cfg.min_wf_score == 7.0
        assert cfg.severity == "error"

    def test_frozen(self):
        cfg = WalkForwardGateConfig()
        with pytest.raises(AttributeError):
            cfg.max_sharpe_decay = 0.99  # type: ignore[misc]


# ============================================================================
# TestCheckWalkForwardGates — 核心检查逻辑
# ============================================================================

class TestCheckWalkForwardGates:
    """check_walk_forward_gates 检查逻辑"""

    def test_good_report_all_pass(self):
        """好的 WF 结果应全部通过"""
        report = check_walk_forward_gates(_good_wf_dict(), "test_strategy")

        assert report.all_passed
        assert report.wf_verdict == "PASS"
        assert report.wf_score == 7.5
        assert len(report.errors) == 0
        assert len(report.warnings) == 0
        assert all(r.passed for r in report.results)

    def test_bad_report_all_fail(self):
        """差的 WF 结果应全部失败"""
        report = check_walk_forward_gates(_bad_wf_dict(), "test_strategy")

        assert len(report.results) == 5
        # 所有 5 项检查都应该失败
        failed = [r for r in report.results if not r.passed]
        assert len(failed) == 5

    def test_sharpe_decay_boundary(self):
        """Sharpe 衰减恰好 = 阈值时应通过"""
        d = _good_wf_dict()
        d["avg_sharpe_decay"] = 0.60  # 恰好等于阈值
        report = check_walk_forward_gates(d)
        decay_result = next(r for r in report.results if r.metric == "sharpe_decay")
        assert decay_result.passed  # <= 0.60

    def test_sharpe_decay_exceed(self):
        """Sharpe 衰减超过阈值时应失败"""
        d = _good_wf_dict()
        d["avg_sharpe_decay"] = 0.61
        report = check_walk_forward_gates(d)
        decay_result = next(r for r in report.results if r.metric == "sharpe_decay")
        assert not decay_result.passed

    def test_negative_decay_passes(self):
        """负衰减 (OOS > IS) 应通过"""
        d = _good_wf_dict()
        d["avg_sharpe_decay"] = -0.10  # OOS 优于 IS
        report = check_walk_forward_gates(d)
        decay_result = next(r for r in report.results if r.metric == "sharpe_decay")
        assert decay_result.passed

    def test_test_win_rate_boundary(self):
        """OOS 盈利比例恰好 = 阈值时应通过"""
        d = _good_wf_dict()
        d["test_win_rate"] = 0.50
        report = check_walk_forward_gates(d)
        wr_result = next(r for r in report.results if r.metric == "test_win_rate")
        assert wr_result.passed

    def test_test_sharpe_zero_fails(self):
        """OOS Sharpe = 0 时应失败 (要求 > 0)"""
        d = _good_wf_dict()
        d["avg_test_sharpe"] = 0.0
        report = check_walk_forward_gates(d)
        sharpe_result = next(r for r in report.results if r.metric == "test_sharpe")
        assert not sharpe_result.passed

    def test_wf_score_boundary(self):
        """WF 评分恰好 = 5.0 应通过"""
        d = _good_wf_dict()
        d["score"] = 5.0
        report = check_walk_forward_gates(d)
        score_result = next(r for r in report.results if r.metric == "wf_score")
        assert score_result.passed

    def test_custom_thresholds(self):
        """自定义严格阈值: 好报告也可能不通过"""
        cfg = WalkForwardGateConfig(
            max_sharpe_decay=0.10,  # 非常严格
            min_wf_score=9.0,
        )
        report = check_walk_forward_gates(_good_wf_dict(), config=cfg)
        # sharpe_decay=0.30 > 0.10 → 失败
        decay_result = next(r for r in report.results if r.metric == "sharpe_decay")
        assert not decay_result.passed
        # score=7.5 < 9.0 → 失败
        score_result = next(r for r in report.results if r.metric == "wf_score")
        assert not score_result.passed

    def test_severity_error_populates_errors(self):
        """severity=error 时失败项应进入 errors 列表"""
        cfg = WalkForwardGateConfig(severity="error")
        report = check_walk_forward_gates(_bad_wf_dict(), config=cfg)
        assert len(report.errors) > 0
        assert not report.all_passed

    def test_severity_warning_populates_warnings(self):
        """severity=warning 时失败项进入 warnings，all_passed 仍为 True"""
        cfg = WalkForwardGateConfig(severity="warning")
        report = check_walk_forward_gates(_bad_wf_dict(), config=cfg)
        assert len(report.warnings) > 0
        assert report.all_passed  # warning 不影响 all_passed

    def test_missing_keys_use_defaults(self):
        """缺少键时使用安全默认值"""
        report = check_walk_forward_gates({}, "empty_strategy")
        assert report.strategy_name == "empty_strategy"
        assert len(report.results) == 5
        # 所有默认值都是最差情况，应该全部失败（除了 sharpe=0 test）
        # avg_sharpe_decay=1.0, avg_return_decay=1.0, test_win_rate=0.0, avg_test_sharpe=0.0, score=0.0

    def test_strategy_name_propagated(self):
        """策略名称正确传递到报告"""
        report = check_walk_forward_gates(_good_wf_dict(), "my_alpha_strategy")
        assert report.strategy_name == "my_alpha_strategy"

    def test_results_have_5_items(self):
        """固定检查 5 个指标"""
        report = check_walk_forward_gates(_good_wf_dict())
        assert len(report.results) == 5
        metrics = {r.metric for r in report.results}
        assert metrics == {
            "sharpe_decay", "return_decay", "test_win_rate",
            "test_sharpe", "wf_score",
        }


# ============================================================================
# TestLoadWfGateConfig — YAML 加载
# ============================================================================

class TestLoadWfGateConfig:
    """load_wf_gate_config YAML 加载测试"""

    def test_load_real_config(self):
        """加载项目真实 configs/quality_gates.yml"""
        cfg = load_wf_gate_config("configs/quality_gates.yml")
        assert cfg is not None
        assert cfg.max_sharpe_decay == 0.50
        assert cfg.min_wf_score == 5.0
        assert cfg.severity == "error"

    def test_load_nonexistent_file(self):
        """文件不存在返回 None"""
        cfg = load_wf_gate_config("/nonexistent/path/quality_gates.yml")
        assert cfg is None

    def test_load_custom_yaml(self):
        """从临时文件加载自定义配置"""
        content = """
walk_forward_gates:
  max_sharpe_decay: 0.30
  min_wf_score: 8.0
  severity: error
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            f.flush()

            cfg = load_wf_gate_config(f.name)
            assert cfg is not None
            assert cfg.max_sharpe_decay == 0.30
            assert cfg.min_wf_score == 8.0
            assert cfg.severity == "error"
            # 未指定的字段保持默认
            assert cfg.min_test_win_rate == 0.50

    def test_load_yaml_without_wf_section(self):
        """YAML 没有 walk_forward_gates 段返回 None"""
        content = """
quality_gates:
  - metric: sharpe_ratio
    operator: ">="
    threshold: 0.3
"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8",
        ) as f:
            f.write(content)
            f.flush()
            cfg = load_wf_gate_config(f.name)
            assert cfg is None

    def test_load_empty_yaml(self):
        """空 YAML 返回 None"""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, encoding="utf-8",
        ) as f:
            f.write("")
            f.flush()
            cfg = load_wf_gate_config(f.name)
            assert cfg is None


# ============================================================================
# TestFormatWfGateReport
# ============================================================================

class TestFormatWfGateReport:
    """format_wf_gate_report 格式化测试"""

    def test_format_passed_report(self):
        """通过的报告包含 ✓"""
        report = check_walk_forward_gates(_good_wf_dict(), "good_strat")
        text = format_wf_gate_report(report)
        assert "good_strat" in text
        assert "PASS" in text
        assert "7.5" in text
        assert "✓" in text
        assert "全部通过" in text

    def test_format_failed_report(self):
        """失败的报告包含 ✗"""
        cfg = WalkForwardGateConfig(severity="error")
        report = check_walk_forward_gates(_bad_wf_dict(), "bad_strat", config=cfg)
        text = format_wf_gate_report(report)
        assert "bad_strat" in text
        assert "FAIL" in text
        assert "✗" in text
        assert "存在失败" in text


# ============================================================================
# TestWfGateIntegration — 与真实 WF 报告集成
# ============================================================================

class TestWfGateIntegration:
    """与真实 WF 输出 (wf_dual_ma.json) 格式集成"""

    def test_dual_ma_wf_result_format(self):
        """使用真实 dual_ma WF 结果格式做门禁检查"""
        # 模拟 wf_config_runner 输出的结构
        wf_result = {
            "avg_train_return": 0.2048,
            "avg_test_return": 0.0157,
            "avg_train_sharpe": 2.79,
            "avg_test_sharpe": 1.79,
            "avg_return_decay": 0.9447,
            "avg_sharpe_decay": 0.4964,
            "test_win_rate": 0.3333,
            "score": 6.67,
            "verdict": "FAIL",
        }
        report = check_walk_forward_gates(wf_result, "dual_ma")

        assert report.strategy_name == "dual_ma"
        assert report.wf_verdict == "FAIL"
        assert report.wf_score == 6.67

        # sharpe_decay=0.4964 <= 0.60 → pass
        decay = next(r for r in report.results if r.metric == "sharpe_decay")
        assert decay.passed

        # return_decay=0.9447 > 0.80 → fail
        ret_decay = next(r for r in report.results if r.metric == "return_decay")
        assert not ret_decay.passed

        # test_win_rate=0.3333 < 0.50 → fail
        wr = next(r for r in report.results if r.metric == "test_win_rate")
        assert not wr.passed

    def test_ideal_strategy_all_pass(self):
        """理想策略 (低衰减、高胜率) 全部通过"""
        wf_result = {
            "avg_sharpe_decay": 0.10,
            "avg_return_decay": 0.15,
            "test_win_rate": 1.0,
            "avg_test_sharpe": 2.5,
            "score": 9.5,
            "verdict": "PASS",
        }
        report = check_walk_forward_gates(wf_result, "ideal")
        assert report.all_passed
        assert all(r.passed for r in report.results)

    def test_gate_report_has_checked_at(self):
        """报告包含检查时间戳"""
        report = check_walk_forward_gates(_good_wf_dict())
        assert report.checked_at  # 非空
        assert "T" in report.checked_at  # ISO 格式
