"""tests/models/test_retrain_pipeline.py — 自动重训管线编排器单元测试

测试覆盖:
    - RetrainConfig 数据类（from_yaml、路径解析）
    - PipelineResult 序列化与摘要
    - AutoRetrainPipeline 核心流程（trigger/gate/rollback）
    - 质量门控逻辑（IC 阈值、IC 改善）
    - 回滚机制
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from aipha.models.retrain_pipeline import (
    AutoRetrainPipeline,
    PipelineResult,
    RetrainConfig,
)


# ── RetrainConfig 测试 ──────────────────────────────────────

class TestRetrainConfig:
    """RetrainConfig 数据类测试"""

    def test_default_values(self):
        cfg = RetrainConfig()
        assert cfg.universe == "csi2000"
        assert cfg.legacy_ml_compat is False
        assert cfg.ic_threshold == 0.10
        assert cfg.psi_threshold == 0.20
        assert cfg.max_age_days == 90

    def test_custom_values(self):
        cfg = RetrainConfig(
            universe="csi1000",
            ic_threshold=0.08,
            max_age_days=60,
        )
        assert cfg.universe == "csi1000"
        assert cfg.ic_threshold == 0.08
        assert cfg.max_age_days == 60

    def test_model_dir_path(self):
        cfg = RetrainConfig(
            model_dir="models/test_model",
            project_root="/tmp/proj",
        )
        assert cfg.model_dir_path() == Path("/tmp/proj/models/test_model")

    def test_archive_dir_path(self):
        cfg = RetrainConfig(
            archive_dir="models/_archive",
            project_root="/tmp/proj",
        )
        assert cfg.archive_dir_path() == Path("/tmp/proj/models/_archive")

    def test_from_yaml(self, tmp_path):
        yaml_content = """
retrain:
  universe: csi1000
  model_dir: models/ml_csi1000_lgb_fund
  archive_dir: models/_archive
  triggers:
    psi_threshold: 0.25
    max_age_days: 120
    ic_threshold: 0.05
    ic_improvement_min: -0.01
"""
        cfg_file = tmp_path / "retrain.yml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        cfg = RetrainConfig.from_yaml(cfg_file)
        assert cfg.universe == "csi1000"
        assert cfg.ic_threshold == 0.05
        assert cfg.psi_threshold == 0.25
        assert cfg.max_age_days == 120
        assert cfg.ic_improvement_min == -0.01
        assert cfg.legacy_ml_compat is False


# ── PipelineResult 测试 ─────────────────────────────────────

class TestPipelineResult:
    """PipelineResult 数据类测试"""

    def test_default_values(self):
        r = PipelineResult()
        assert r.triggered is False
        assert r.promoted is False
        assert r.rolled_back is False
        assert r.error == ""

    def test_to_dict(self):
        r = PipelineResult(
            triggered=True,
            trigger_reasons=["psi_drift"],
            retrained=True,
            retrain_exit_code=0,
            old_ic=0.10,
            new_ic=0.12,
            ic_delta=0.02,
            gate_passed=True,
            promoted=True,
            timestamp="2026-03-15T20:00:00",
        )
        d = r.to_dict()
        assert d["triggered"] is True
        assert d["promoted"] is True
        assert d["new_ic"] == 0.12
        assert d["ic_delta"] == 0.02
        assert isinstance(d, dict)

    def test_summary_no_trigger(self):
        r = PipelineResult(triggered=False)
        assert "SKIP" in r.summary

    def test_summary_promoted(self):
        r = PipelineResult(
            triggered=True, promoted=True,
            old_ic=0.10, new_ic=0.12, ic_delta=0.02,
        )
        assert "PROMOTED" in r.summary

    def test_summary_error(self):
        r = PipelineResult(error="something failed")
        assert "ERROR" in r.summary

    def test_summary_rollback(self):
        r = PipelineResult(
            triggered=True, rolled_back=True, new_ic=0.03,
        )
        assert "ROLLBACK" in r.summary


# ── AutoRetrainPipeline 测试 ────────────────────────────────

class TestAutoRetrainPipeline:
    """核心管线逻辑测试"""

    @pytest.fixture
    def cfg(self, tmp_path):
        model_dir = tmp_path / "models" / "ml_csi2000_lgb_fund"
        model_dir.mkdir(parents=True)
        return RetrainConfig(
            universe="csi2000",
            model_dir=str(model_dir.relative_to(tmp_path)),
            archive_dir=str((tmp_path / "models" / "_archive").relative_to(tmp_path)),
            ic_threshold=0.10,
            ic_improvement_min=-0.005,
            project_root=str(tmp_path),
            legacy_ml_compat=True,
        )

    @pytest.fixture
    def pipeline(self, cfg):
        return AutoRetrainPipeline(cfg)

    def test_quality_gate_pass(self, pipeline):
        old = {"mean_ic": 0.10}
        new = {"mean_ic": 0.12}
        passed, old_ic, new_ic = pipeline._quality_gate(old, new)
        assert passed is True
        assert new_ic == 0.12

    def test_quality_gate_fail_below_threshold(self, pipeline):
        old = {"mean_ic": 0.10}
        new = {"mean_ic": 0.05}
        passed, _, new_ic = pipeline._quality_gate(old, new)
        assert passed is False

    def test_quality_gate_fail_too_much_degradation(self, pipeline):
        """新 IC 达标但比旧 IC 降太多"""
        old = {"mean_ic": 0.15}
        new = {"mean_ic": 0.10}
        # delta = -0.05 < ic_improvement_min(-0.005)
        passed, _, _ = pipeline._quality_gate(old, new)
        assert passed is False

    def test_quality_gate_slight_degradation_ok(self, pipeline):
        """微降 < 0.5% 仍可接受"""
        old = {"mean_ic": 0.12}
        new = {"mean_ic": 0.117}
        passed, _, _ = pipeline._quality_gate(old, new)
        assert passed is True

    def test_quality_gate_no_old_report(self, pipeline):
        new = {"mean_ic": 0.12}
        passed, old_ic, new_ic = pipeline._quality_gate(None, new)
        assert passed is True
        assert old_ic == 0.0

    def test_quality_gate_no_new_report(self, pipeline):
        old = {"mean_ic": 0.10}
        passed, _, _ = pipeline._quality_gate(old, None)
        assert passed is False

    def test_quality_gate_nested_summary(self, pipeline):
        """IC 嵌套在 summary 字典中"""
        old = {"mean_ic": {}, "summary": {"mean_ic": 0.10}}
        new = {"mean_ic": {}, "summary": {"mean_ic": 0.13}}
        passed, old_ic, new_ic = pipeline._quality_gate(old, new)
        assert passed is True
        assert old_ic == 0.10
        assert new_ic == 0.13

    def test_archive_and_rollback(self, pipeline, cfg, tmp_path):
        """验证归档→回滚可恢复原模型"""
        model_dir = Path(cfg.project_root) / cfg.model_dir
        (model_dir / "meta.pkl").write_bytes(b"original")

        archive_path = pipeline._archive_model("v20260315")
        assert archive_path is not None
        assert archive_path.exists()

        # 模拟新训练覆盖了模型
        (model_dir / "meta.pkl").write_bytes(b"new_bad")

        # 回滚
        ok = pipeline._rollback(archive_path)
        assert ok is True
        assert (model_dir / "meta.pkl").read_bytes() == b"original"

    def test_rollback_no_archive(self, pipeline):
        ok = pipeline._rollback(Path("/nonexistent"))
        assert ok is False

    def test_fallback_age_check_no_model(self, pipeline, cfg, tmp_path):
        """无模型文件时 fallback 返回 needs retrain"""
        should, reasons, _ = pipeline._fallback_age_check()
        assert should is True
        assert "no_model" in reasons

    def test_write_audit(self, pipeline, cfg, tmp_path):
        result = PipelineResult(triggered=True, timestamp="2026-03-15")
        pipeline._write_audit(result)
        audit_dir = Path(cfg.project_root) / "logs" / "audit"
        audit_files = list(audit_dir.glob("retrain_*.json"))
        assert len(audit_files) >= 1

    def test_run_no_trigger(self, pipeline):
        """无触发时直接返回 SKIP"""
        with patch.object(pipeline, "_check_trigger", return_value=(False, [], {})):
            result = pipeline.run(force=False)
        assert result.triggered is False
        assert "SKIP" in result.summary

    def test_run_rejects_legacy_retraining_without_explicit_compat(self, cfg):
        cfg.legacy_ml_compat = False
        pipeline = AutoRetrainPipeline(cfg)
        with pytest.raises(RuntimeError, match="legacy automatic ML retraining is disabled"):
            pipeline.run(force=True)

    def test_run_dry_run(self, pipeline):
        """dry_run 模式：触发但不执行"""
        with patch.object(
            pipeline, "_check_trigger", return_value=(True, ["psi_drift"], {})
        ):
            result = pipeline.run(dry_run=True)
        assert result.triggered is True
        assert result.retrained is False

    def test_load_evaluation_missing(self, pipeline):
        report = pipeline._load_evaluation()
        assert report is None

    def test_load_evaluation_exists(self, pipeline, cfg):
        model_dir = Path(cfg.project_root) / cfg.model_dir
        model_dir.mkdir(parents=True, exist_ok=True)
        report_path = model_dir / "evaluation_report.json"
        report_path.write_text(
            json.dumps({"mean_ic": 0.11}), encoding="utf-8"
        )
        report = pipeline._load_evaluation()
        assert report is not None
        assert report["mean_ic"] == 0.11
