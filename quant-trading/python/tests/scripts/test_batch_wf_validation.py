"""批量 WF 验证脚本的单测

验证脚本结构、策略配置合法性、汇总逻辑。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


class TestProductionStrategies:
    """验证生产策略配置合法性"""

    def test_all_configs_exist(self) -> None:
        from scripts.batch_wf_validation import PRODUCTION_STRATEGIES
        for key, spec in PRODUCTION_STRATEGIES.items():
            config_path = Path(spec["config"])
            assert config_path.exists(), (
                f"策略 {key} 的配置文件不存在: {config_path}"
            )

    def test_all_strategies_have_required_fields(self) -> None:
        from scripts.batch_wf_validation import PRODUCTION_STRATEGIES
        required = {"config", "name", "train_days", "test_days",
                    "n_splits", "purged", "description"}
        for key, spec in PRODUCTION_STRATEGIES.items():
            missing = required - set(spec.keys())
            assert not missing, (
                f"策略 {key} 缺少字段: {missing}"
            )

    def test_train_days_positive(self) -> None:
        from scripts.batch_wf_validation import PRODUCTION_STRATEGIES
        for key, spec in PRODUCTION_STRATEGIES.items():
            assert spec["train_days"] > 0
            assert spec["test_days"] > 0
            assert spec["n_splits"] >= 2


class TestSummaryLogic:
    """验证汇总打印逻辑"""

    def test_print_summary_with_pass(self, capsys) -> None:
        from scripts.batch_wf_validation import print_summary
        results = [
            {"key": "csi2000", "status": "OK",
             "verdict": "PASS", "score": 7.5},
        ]
        print_summary(results)
        captured = capsys.readouterr()
        assert "通过: 1/1" in captured.out

    def test_print_summary_with_fail(self, capsys) -> None:
        from scripts.batch_wf_validation import print_summary
        results = [
            {"key": "csi2000", "status": "OK",
             "verdict": "FAIL", "score": 3.0},
            {"key": "csi300", "status": "OK",
             "verdict": "PASS", "score": 8.0},
        ]
        print_summary(results)
        captured = capsys.readouterr()
        assert "通过: 1/2" in captured.out
        assert "未通过" in captured.out

    def test_print_summary_with_error(self, capsys) -> None:
        from scripts.batch_wf_validation import print_summary
        results = [
            {"key": "csi2000", "status": "ERROR",
             "reason": "data missing"},
        ]
        print_summary(results)
        captured = capsys.readouterr()
        assert "通过: 0/1" in captured.out
