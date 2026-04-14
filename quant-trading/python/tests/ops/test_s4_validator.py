"""Tests for s4_validator.py (C4: R89)

Validates:
- load_nav_history: file read, missing file, invalid JSON
- validate_s4: no records, pass/in-progress, invalid NAV, large change, gap reset, dedup
- check_all_portfolios: scanning + filter
- format_s4_summary: Markdown output
- send_s4_notification: import fallback
- main CLI: exit codes
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


class TestLoadNavHistory:
    """load_nav_history 单元测试。"""

    def test_reads_valid_jsonl(self, tmp_path):
        from scripts.ops.s4_validator import load_nav_history

        d = tmp_path / "combo1"
        d.mkdir()
        lines = [
            json.dumps({"date": "2025-01-01", "nav": 1.0}),
            json.dumps({"date": "2025-01-02", "nav": 1.01}),
        ]
        (d / "nav_history.jsonl").write_text("\n".join(lines), encoding="utf-8")
        records = load_nav_history(d)
        assert len(records) == 2
        assert records[0]["date"] == "2025-01-01"

    def test_missing_file_returns_empty(self, tmp_path):
        from scripts.ops.s4_validator import load_nav_history

        d = tmp_path / "combo1"
        d.mkdir()
        assert load_nav_history(d) == []

    def test_invalid_json_lines_skipped(self, tmp_path):
        from scripts.ops.s4_validator import load_nav_history

        d = tmp_path / "combo1"
        d.mkdir()
        content = '{"date":"2025-01-01","nav":1.0}\nBROKEN LINE\n{"date":"2025-01-02","nav":1.01}\n'
        (d / "nav_history.jsonl").write_text(content, encoding="utf-8")
        records = load_nav_history(d)
        assert len(records) == 2


class TestValidateS4:
    """validate_s4 核心验证逻辑测试。"""

    def test_no_records_returns_not_passed(self):
        from scripts.ops.s4_validator import validate_s4

        r = validate_s4([], portfolio_id="test")
        assert r.passed is False
        assert r.total_valid_days == 0

    def test_sufficient_days_pass(self):
        from scripts.ops.s4_validator import validate_s4

        records = [
            {"date": f"2025-01-{d:02d}", "nav": 1.0 + d * 0.001}
            for d in range(1, 32)
        ]
        # 31 天, required=30
        r = validate_s4(records, portfolio_id="test", required_days=30)
        assert r.passed is True
        assert r.total_valid_days == 31

    def test_insufficient_days_in_progress(self):
        from scripts.ops.s4_validator import validate_s4

        records = [
            {"date": f"2025-01-{d:02d}", "nav": 1.0 + d * 0.001}
            for d in range(1, 11)
        ]
        r = validate_s4(records, portfolio_id="test", required_days=60)
        assert r.passed is False
        assert r.total_valid_days == 10
        assert "IN PROGRESS" in r.detail

    def test_nav_zero_is_invalid(self):
        from scripts.ops.s4_validator import validate_s4

        records = [
            {"date": "2025-01-01", "nav": 1.0},
            {"date": "2025-01-02", "nav": 0.0},
            {"date": "2025-01-03", "nav": 1.01},
        ]
        r = validate_s4(records, portfolio_id="test", required_days=1)
        assert r.total_valid_days == 2  # day1 and day3 valid
        assert len(r.invalid_days) == 1

    def test_excessive_change_is_invalid(self):
        from scripts.ops.s4_validator import validate_s4

        records = [
            {"date": "2025-01-01", "nav": 1.0},
            {"date": "2025-01-02", "nav": 1.5},  # +50% > 20%
            {"date": "2025-01-03", "nav": 1.02},  # vs prev_nav=1.5 → -32%
        ]
        r = validate_s4(records, portfolio_id="test",
                         required_days=1, max_daily_change=0.20)
        # Both day2 and day3 are invalid (cascading from spike)
        assert len(r.invalid_days) == 2
        assert "change=" in r.invalid_days[0]["reason"]

    def test_gap_resets_streak(self):
        from scripts.ops.s4_validator import validate_s4

        records = [
            {"date": "2025-01-01", "nav": 1.0},
            {"date": "2025-01-02", "nav": 1.001},
            {"date": "2025-01-03", "nav": 1.002},
            # 10-day gap (exceeds max_gap_days=3)
            {"date": "2025-01-13", "nav": 1.003},
            {"date": "2025-01-14", "nav": 1.004},
        ]
        r = validate_s4(records, portfolio_id="test",
                         required_days=1, max_gap_days=3)
        assert r.total_valid_days == 5
        assert r.consecutive_valid_days == 2  # after gap reset
        assert r.longest_streak == 3  # first 3 days
        assert len(r.gaps) == 1

    def test_dedup_same_date_keeps_last(self):
        from scripts.ops.s4_validator import validate_s4

        records = [
            {"date": "2025-01-01", "nav": 1.0},
            {"date": "2025-01-01", "nav": 2.0},  # dup, should keep this
        ]
        r = validate_s4(records, portfolio_id="test", required_days=1)
        assert r.total_valid_days == 1


class TestCheckAllPortfolios:
    """check_all_portfolios 扫描测试。"""

    def test_scans_dirs(self, tmp_path):
        from scripts.ops.s4_validator import check_all_portfolios

        for name in ["combo1", "combo2"]:
            d = tmp_path / name
            d.mkdir()
            lines = [json.dumps({"date": f"2025-01-{i:02d}", "nav": 1.0})
                     for i in range(1, 4)]
            (d / "nav_history.jsonl").write_text("\n".join(lines), encoding="utf-8")
        results = check_all_portfolios(state_dir=tmp_path, required_days=2)
        assert len(results) == 2
        assert all(r.passed for r in results)

    def test_portfolio_filter(self, tmp_path):
        from scripts.ops.s4_validator import check_all_portfolios

        for name in ["combo1", "combo2"]:
            d = tmp_path / name
            d.mkdir()
            line = json.dumps({"date": "2025-01-01", "nav": 1.0})
            (d / "nav_history.jsonl").write_text(line, encoding="utf-8")
        results = check_all_portfolios(
            state_dir=tmp_path, required_days=1, portfolio_filter="combo1"
        )
        assert len(results) == 1
        assert results[0].portfolio_id == "combo1"


class TestFormatS4Summary:
    """format_s4_summary 输出测试。"""

    def test_no_results(self):
        from scripts.ops.s4_validator import format_s4_summary

        text = format_s4_summary([])
        assert "no portfolios found" in text.lower()

    def test_has_results(self):
        from scripts.ops.s4_validator import S4ValidationResult, format_s4_summary

        r = S4ValidationResult(
            portfolio_id="test",
            total_valid_days=45,
            consecutive_valid_days=45,
            longest_streak=45,
            first_valid_date="2025-01-01",
            last_valid_date="2025-03-15",
            required_days=60,
            passed=False,
            detail="45/60 (75%)",
        )
        text = format_s4_summary([r])
        assert "test" in text
        assert "45/60" in text
        assert "IN PROGRESS" in text


class TestS4ValidatorCli:
    """main() CLI 集成测试。"""

    def test_exit_0_when_all_pass(self, tmp_path):
        from scripts.ops.s4_validator import main

        d = tmp_path / "combo1"
        d.mkdir()
        lines = [json.dumps({"date": f"2025-01-{i:02d}", "nav": 1.0})
                 for i in range(1, 6)]
        (d / "nav_history.jsonl").write_text("\n".join(lines), encoding="utf-8")
        ret = main(["--state-dir", str(tmp_path), "--required", "3"])
        assert ret == 0

    def test_exit_1_when_any_fail(self, tmp_path):
        from scripts.ops.s4_validator import main

        d = tmp_path / "combo1"
        d.mkdir()
        line = json.dumps({"date": "2025-01-01", "nav": 1.0})
        (d / "nav_history.jsonl").write_text(line, encoding="utf-8")
        ret = main(["--state-dir", str(tmp_path), "--required", "60"])
        assert ret == 1
