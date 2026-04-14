"""Tests for AuditCSVExporter — R56 A8 审计CSV附件导出。

测试覆盖:
- _load_events: JSONL 解析
- export_daily_capital: ACCOUNT_SNAPSHOT → CSV
- export_trade_blotter: ORDER_FILLED → CSV
- export_all: 批量导出
- 边界: 空目录、无事件、畸形 JSON
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest


@pytest.fixture()
def audit_dir(tmp_path: Path) -> Path:
    """创建带测试数据的审计日志目录。"""
    d = tmp_path / "audit"
    d.mkdir()
    events = [
        {
            "timestamp": "2025-01-15T09:30:00",
            "event_type": "ACCOUNT_SNAPSHOT",
            "level": "INFO",
            "message": "daily snapshot",
            "data": {"nav": 1050000.0, "cash": 200000.0},
            "session_id": "s1",
        },
        {
            "timestamp": "2025-01-16T09:30:00",
            "event_type": "ACCOUNT_SNAPSHOT",
            "level": "INFO",
            "message": "daily snapshot",
            "data": {"nav": 1020000.0, "cash": 180000.0},
            "session_id": "s1",
        },
    ]
    fill_events = [
        {
            "timestamp": "2025-01-15T10:00:00",
            "event_type": "ORDER_FILLED",
            "level": "TRADE",
            "message": "filled",
            "data": {
                "side": "BUY", "quantity": 100,
                "price": 25.50, "fee": 5.0,
            },
            "symbol": "000001.SZ",
            "order_id": "ord_001",
            "session_id": "s1",
        },
    ]
    with open(d / "audit_20250115.jsonl", "w") as f:
        for e in events + fill_events:
            f.write(json.dumps(e) + "\n")
    return d


class TestAuditCSVExporter:
    """AuditCSVExporter 测试。"""

    def test_load_events(self, audit_dir: Path):
        from aipha.reporting.audit_csv_exporter import AuditCSVExporter
        exp = AuditCSVExporter(audit_dir)
        exp._load_events()
        assert len(exp._events) == 3  # 2 snapshots + 1 fill

    def test_daily_capital_csv(self, audit_dir: Path, tmp_path: Path):
        from aipha.reporting.audit_csv_exporter import AuditCSVExporter
        exp = AuditCSVExporter(audit_dir)
        out = tmp_path / "daily_capital.csv"
        n = exp.export_daily_capital(out)
        assert n == 2
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert lines[0] == "date,nav,cash,equity,drawdown"
        assert "2025-01-15" in lines[1]

    def test_trade_blotter_csv(self, audit_dir: Path, tmp_path: Path):
        from aipha.reporting.audit_csv_exporter import AuditCSVExporter
        exp = AuditCSVExporter(audit_dir)
        out = tmp_path / "blotter.csv"
        n = exp.export_trade_blotter(out)
        assert n == 1
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert "000001.SZ" in lines[1]
        assert "BUY" in lines[1]

    def test_export_all(self, audit_dir: Path, tmp_path: Path):
        from aipha.reporting.audit_csv_exporter import AuditCSVExporter
        exp = AuditCSVExporter(audit_dir)
        result = exp.export_all(tmp_path / "export")
        assert result["daily_capital"] == 2
        assert result["trade_blotter"] == 1

    def test_empty_dir(self, tmp_path: Path):
        from aipha.reporting.audit_csv_exporter import AuditCSVExporter
        empty = tmp_path / "empty_audit"
        empty.mkdir()
        exp = AuditCSVExporter(empty)
        n = exp.export_daily_capital(tmp_path / "dc.csv")
        assert n == 0

    def test_nonexistent_dir(self, tmp_path: Path):
        from aipha.reporting.audit_csv_exporter import AuditCSVExporter
        exp = AuditCSVExporter(tmp_path / "nonexist")
        n = exp.export_trade_blotter(tmp_path / "tb.csv")
        assert n == 0

    def test_malformed_jsonl(self, tmp_path: Path):
        from aipha.reporting.audit_csv_exporter import AuditCSVExporter
        d = tmp_path / "bad_audit"
        d.mkdir()
        with open(d / "bad.jsonl", "w") as f:
            f.write("not valid json\n")
            f.write('{"event_type": "OTHER"}\n')
        exp = AuditCSVExporter(d)
        exp._load_events()
        assert len(exp._events) == 1  # only the valid line

    def test_drawdown_calculation(self, audit_dir: Path, tmp_path: Path):
        from aipha.reporting.audit_csv_exporter import AuditCSVExporter
        exp = AuditCSVExporter(audit_dir)
        out = tmp_path / "dc.csv"
        exp.export_daily_capital(out)
        lines = out.read_text().strip().split("\n")
        # Day 1: nav=1050000, peak=1050000, dd=0
        assert "0.0000" in lines[1]
        # Day 2: nav=1020000, peak=1050000, dd=(50k-20k)/1050k
        row2 = lines[2].split(",")
        dd = float(row2[4])
        assert dd > 0.02  # ~2.86%

