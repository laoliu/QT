"""test_config_auditor.py — 回测配置审计器测试。

覆盖要求：≥ 15 测试，正常/边界/异常。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from aipha.backtest.config_auditor import (
    AuditFinding,
    ConfigAuditReport,
    audit_config,
    audit_config_directory,
    _get_nested,
)


# ============================================================================
# AuditFinding dataclass
# ============================================================================


class TestAuditFinding:
    def test_to_dict_minimal(self):
        f = AuditFinding(severity="error", category="general", message="test")
        d = f.to_dict()
        assert d["severity"] == "error"
        assert "config_path" not in d

    def test_to_dict_full(self):
        f = AuditFinding(
            severity="warning",
            category="slippage",
            message="low slippage",
            config_path="foo.yml",
            field_path="slippage.bps",
            value=3.0,
            suggestion="use >=5",
        )
        d = f.to_dict()
        assert d["config_path"] == "foo.yml"
        assert d["value"] == 3.0


# ============================================================================
# ConfigAuditReport
# ============================================================================


class TestConfigAuditReport:
    def test_empty_report(self):
        r = ConfigAuditReport()
        assert r.error_count == 0
        assert r.warning_count == 0
        assert "0 errors" in r.summary()

    def test_counts(self):
        r = ConfigAuditReport(
            findings=[
                AuditFinding("error", "a", "x"),
                AuditFinding("error", "b", "y"),
                AuditFinding("warning", "c", "z"),
            ],
            configs_scanned=1,
        )
        assert r.error_count == 2
        assert r.warning_count == 1
        assert r.configs_scanned == 1

    def test_to_dict(self):
        r = ConfigAuditReport(configs_scanned=3)
        d = r.to_dict()
        assert d["configs_scanned"] == 3
        assert "findings" in d


# ============================================================================
# _get_nested 辅助函数
# ============================================================================


class TestGetNested:
    def test_existing_keys(self):
        d = {"a": {"b": {"c": 42}}}
        assert _get_nested(d, "a", "b", "c") == 42

    def test_missing_key(self):
        d = {"a": {"b": 1}}
        assert _get_nested(d, "a", "x", default="nope") == "nope"

    def test_non_dict_intermediate(self):
        d = {"a": "string"}
        assert _get_nested(d, "a", "b", default=None) is None


# ============================================================================
# audit_config — 因子权重检查
# ============================================================================


class TestAuditConfigFactorWeights:
    def test_high_weight_error(self):
        config = {
            "strategy": {
                "name": "smallcap_rotation",
                "parameters": {"weight_small_cap": 0.80},
            },
        }
        findings = audit_config(config)
        factor_errors = [f for f in findings if f.category == "factor_exposure"]
        assert len(factor_errors) >= 1
        assert factor_errors[0].severity == "error"

    def test_balanced_weights_no_factor_error(self):
        config = {
            "strategy": {
                "name": "smallcap_rotation",
                "parameters": {
                    "weight_small_cap": 0.20,
                    "weight_momentum": 0.20,
                    "weight_quality": 0.20,
                    "weight_value": 0.20,
                    "weight_volatility": 0.10,
                    "weight_turnover": 0.10,
                },
            },
        }
        findings = audit_config(config)
        factor_errors = [f for f in findings if f.category == "factor_exposure"]
        assert len(factor_errors) == 0

    def test_threshold_boundary(self):
        """权重正好 0.40 不触发。"""
        config = {
            "strategy": {
                "name": "test",
                "parameters": {"weight_small_cap": 0.40},
            },
        }
        findings = audit_config(config)
        factor_errors = [f for f in findings if f.category == "factor_exposure"]
        assert len(factor_errors) == 0


# ============================================================================
# audit_config — 滑点检查
# ============================================================================


class TestAuditConfigSlippage:
    def test_low_slippage_error(self):
        config = {
            "strategy": {"name": "test"},
            "slippage_model": {"bps": 3.0},
        }
        findings = audit_config(config)
        slip_errors = [f for f in findings if f.category == "slippage" and f.severity == "error"]
        assert len(slip_errors) >= 1

    def test_realistic_slippage_no_error(self):
        config = {
            "strategy": {"name": "test"},
            "slippage_model": {"bps": 15.0},
        }
        findings = audit_config(config)
        slip_errors = [f for f in findings if f.category == "slippage" and f.severity == "error"]
        assert len(slip_errors) == 0

    def test_no_slippage_config(self):
        """没有滑点配置 → 不报错（可能是旧格式）。"""
        config = {"strategy": {"name": "test"}}
        findings = audit_config(config)
        slip_errors = [f for f in findings if f.category == "slippage"]
        assert len(slip_errors) == 0

    def test_slippage_format_cost(self):
        """cost.slippage_bps 格式也能识别。"""
        config = {
            "strategy": {"name": "test"},
            "cost": {"slippage_bps": 2.0},
        }
        findings = audit_config(config)
        slip_errors = [f for f in findings if f.category == "slippage" and f.severity == "error"]
        assert len(slip_errors) >= 1

    def test_index_recommended_slippage_warning(self):
        """沪深300 推荐 ≥ 10bps。"""
        config = {
            "strategy": {"name": "test"},
            "slippage_model": {"bps": 6.0},
            "market": {"instruments": {"index": "000300.SH"}},
        }
        findings = audit_config(config)
        slip_warnings = [f for f in findings if f.category == "slippage" and f.severity == "warning"]
        assert len(slip_warnings) >= 1


# ============================================================================
# audit_config — 风控检查
# ============================================================================


class TestAuditConfigRisk:
    def test_no_risk_warning(self):
        config = {"strategy": {"name": "test"}}
        findings = audit_config(config)
        risk_warnings = [f for f in findings if f.category == "risk"]
        assert len(risk_warnings) >= 1

    def test_with_risk_config(self):
        config = {
            "strategy": {"name": "test"},
            "risk": {"enabled": True, "stop_loss_pct": 0.15},
        }
        findings = audit_config(config)
        risk_warnings = [f for f in findings if f.category == "risk" and "风控" in f.message]
        assert len(risk_warnings) == 0

    def test_high_stop_loss_warning(self):
        config = {
            "strategy": {"name": "test"},
            "risk": {"enabled": True, "stop_loss_pct": 0.60},
        }
        findings = audit_config(config)
        sl_warnings = [f for f in findings if "止损" in f.message]
        assert len(sl_warnings) >= 1


# ============================================================================
# audit_config — 策略名存在性
# ============================================================================


class TestAuditConfigGeneral:
    def test_missing_strategy_name(self):
        config = {"strategy": {"parameters": {}}}
        findings = audit_config(config)
        name_errors = [f for f in findings if f.category == "general" and "strategy.name" in f.message]
        assert len(name_errors) >= 1

    def test_strategy_type_fallback(self):
        """strategy.type 也行。"""
        config = {"strategy": {"type": "my_strat"}}
        findings = audit_config(config)
        name_errors = [f for f in findings if "strategy.name" in f.message]
        assert len(name_errors) == 0


# ============================================================================
# audit_config_directory — 批量扫描
# ============================================================================


class TestAuditConfigDirectory:
    def test_scan_temp_dir(self, tmp_path: Path):
        # 创建 2 个配置
        good = {
            "strategy": {"name": "test"},
            "slippage_model": {"bps": 15.0},
            "risk": {"enabled": True, "stop_loss_pct": 0.15},
        }
        bad = {
            "strategy": {"name": "bad"},
            "slippage_model": {"bps": 2.0},
        }
        (tmp_path / "good.yml").write_text(yaml.dump(good), encoding="utf-8")
        (tmp_path / "bad.yml").write_text(yaml.dump(bad), encoding="utf-8")

        report = audit_config_directory(tmp_path)
        assert report.configs_scanned == 2
        assert report.error_count >= 1  # bad.yml 的低滑点

    def test_nonexistent_dir(self, tmp_path: Path):
        report = audit_config_directory(tmp_path / "nonexistent")
        assert report.configs_scanned == 0

    def test_invalid_yaml(self, tmp_path: Path):
        (tmp_path / "broken.yml").write_text("::invalid:: yaml: [", encoding="utf-8")
        report = audit_config_directory(tmp_path)
        assert report.configs_scanned == 1
        parse_errors = [f for f in report.findings if "解析失败" in f.message]
        assert len(parse_errors) >= 1

    def test_non_dict_yaml_skipped(self, tmp_path: Path):
        """YAML 解析为列表等非 dict 时跳过。"""
        (tmp_path / "list.yml").write_text("- item1\n- item2\n", encoding="utf-8")
        report = audit_config_directory(tmp_path)
        assert report.configs_scanned == 0
