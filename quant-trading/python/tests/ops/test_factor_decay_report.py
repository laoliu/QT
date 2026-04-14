"""Tests for factor_decay_report.py (B3: R88)

Validates:
- load_daily_monitor_alerts correctly reads JSONL
- extract_factor_decay_alerts filters correctly
- extract_psi_alerts honors threshold
- generate_decay_report produces valid JSON output
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


class TestLoadDailyMonitorAlerts:
    """读取审计日志测试。"""

    def test_reads_recent_files(self, tmp_path):
        from scripts.ops.factor_decay_report import load_daily_monitor_alerts
        today = datetime.now().strftime("%Y%m%d")

        _write_jsonl(
            tmp_path / f"daily_monitor_{today}.jsonl",
            [{"task": "factor_decay", "severity": "warning"}],
        )
        records = load_daily_monitor_alerts(
            lookback_days=7, audit_dir=tmp_path
        )
        assert len(records) == 1

    def test_skips_old_files(self, tmp_path):
        from scripts.ops.factor_decay_report import load_daily_monitor_alerts

        _write_jsonl(
            tmp_path / "daily_monitor_20240101.jsonl",
            [{"task": "factor_decay"}],
        )
        records = load_daily_monitor_alerts(
            lookback_days=7, audit_dir=tmp_path
        )
        assert len(records) == 0

    def test_empty_dir(self, tmp_path):
        from scripts.ops.factor_decay_report import load_daily_monitor_alerts

        records = load_daily_monitor_alerts(
            lookback_days=30, audit_dir=tmp_path
        )
        assert records == []


class TestExtractAlerts:
    """告警过滤测试。"""

    def test_extract_factor_decay(self):
        from scripts.ops.factor_decay_report import extract_factor_decay_alerts

        records = [
            {"task": "factor_decay", "severity": "warning", "factor": "mom"},
            {"task": "factor_decay", "severity": "info", "factor": "vol"},
            {"task": "drift_detection", "severity": "warning", "psi": 0.3},
            {"tasks_run": ["factor_decay"]},  # summary, skip
        ]
        alerts = extract_factor_decay_alerts(records)
        assert len(alerts) == 2  # factor_decay warning + drift warning

    def test_extract_psi_alerts(self):
        from scripts.ops.factor_decay_report import extract_psi_alerts

        records = [
            {"task": "drift_detection", "psi": 0.15, "severity": "info"},
            {"task": "drift_detection", "psi": 0.25, "severity": "warning"},
            {"task": "drift_detection", "psi": 0.35, "severity": "warning"},
            {"task": "factor_decay", "severity": "warning"},
        ]
        alerts = extract_psi_alerts(records, threshold=0.2)
        assert len(alerts) == 2
        assert all(a["psi"] >= 0.2 for a in alerts)


class TestGenerateDecayReport:
    """报告生成测试。"""

    def test_generates_json_file(self, tmp_path):
        from scripts.ops.factor_decay_report import generate_decay_report
        today = datetime.now().strftime("%Y%m%d")
        today_dash = datetime.now().strftime("%Y-%m-%d")

        audit_dir = tmp_path / "audit"
        output_dir = tmp_path / "output"
        _write_jsonl(
            audit_dir / f"daily_monitor_{today}.jsonl",
            [
                {"task": "factor_decay", "severity": "warning",
                 "factor": "size", "date": today_dash},
                {"task": "drift_detection", "severity": "warning",
                 "psi": 0.25, "feature": "ret_5d", "date": today_dash},
            ],
        )
        report = generate_decay_report(
            lookback_days=7,
            output_dir=output_dir,
            audit_dir=audit_dir,
        )
        assert report["factor_decay_alerts"] >= 1
        assert report["psi_alerts"] >= 1
        report_file = output_dir / "factor_decay_report.json"
        assert report_file.exists()
        loaded = json.loads(report_file.read_text(encoding="utf-8"))
        assert "generated_at" in loaded

    def test_empty_audit_produces_report(self, tmp_path):
        from scripts.ops.factor_decay_report import generate_decay_report

        report = generate_decay_report(
            lookback_days=7,
            output_dir=tmp_path / "output",
            audit_dir=tmp_path / "empty_audit",
        )
        assert report["factor_decay_alerts"] == 0
        assert report["psi_alerts"] == 0


class TestFactorDecayReportCli:
    """CLI 测试。"""

    def test_cli_returns_0_no_psi(self, tmp_path):
        from scripts.ops.factor_decay_report import main
        today = datetime.now().strftime("%Y%m%d")

        audit_dir = tmp_path / "audit"
        output_dir = tmp_path / "output"
        _write_jsonl(
            audit_dir / f"daily_monitor_{today}.jsonl",
            [{"task": "factor_decay", "severity": "warning", "factor": "x"}],
        )
        code = main([
            "--lookback-days", "7",
            "--output-dir", str(output_dir),
            "--audit-dir", str(audit_dir),
        ])
        # No PSI alerts → exit 0
        assert code == 0

    def test_cli_returns_1_with_psi(self, tmp_path):
        from scripts.ops.factor_decay_report import main
        today = datetime.now().strftime("%Y%m%d")
        today_dash = datetime.now().strftime("%Y-%m-%d")

        audit_dir = tmp_path / "audit"
        output_dir = tmp_path / "output"
        _write_jsonl(
            audit_dir / f"daily_monitor_{today}.jsonl",
            [{"task": "drift_detection", "severity": "warning",
              "psi": 0.25, "feature": "ret_5d", "date": today_dash}],
        )
        code = main([
            "--lookback-days", "7",
            "--output-dir", str(output_dir),
            "--audit-dir", str(audit_dir),
        ])
        assert code == 1
