"""tests/monitor/test_agent_audit.py — AgentAudit 单元测试

覆盖：AuditRecord 序列化、log_* 四种方法、query_audit_trail 过滤、
tail() 逆序、trace_id 格式、_safe_hash、MLflow 降级（共 ≥12 个用例）。
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aipha.monitor.agent_audit import (
    AgentAudit,
    AuditEventType,
    AuditRecord,
    _make_trace_id,
    _safe_hash,
)


# ── fixtures ──────────────────────────────────────────────────────

@pytest.fixture()
def tmp_audit_dir(tmp_path: Path) -> Path:
    """临时的审计日志目录"""
    d = tmp_path / "audit"
    d.mkdir()
    return d


@pytest.fixture()
def audit(tmp_audit_dir: Path) -> AgentAudit:
    """AgentAudit 实例，指向临时目录，不启用 MLflow"""
    return AgentAudit(log_dir=tmp_audit_dir, model_version="test-v1")


# ── 工具函数测试 ──────────────────────────────────────────────────

class TestHelpers:
    def test_safe_hash_returns_12_hex(self) -> None:
        h = _safe_hash("hello world")
        assert len(h) == 12
        assert all(c in "0123456789abcdef" for c in h)

    def test_safe_hash_deterministic(self) -> None:
        assert _safe_hash("abc") == _safe_hash("abc")

    def test_safe_hash_different_inputs(self) -> None:
        assert _safe_hash("abc") != _safe_hash("abd")

    def test_make_trace_id_prefix(self) -> None:
        tid = _make_trace_id()
        assert tid.startswith("tr_")
        assert len(tid) > 10


# ── AuditRecord 序列化测试 ────────────────────────────────────────

class TestAuditRecord:
    def test_to_dict_has_required_fields(self) -> None:
        rec = AuditRecord(
            trace_id="tr_abc_001",
            event_type="factor_generation",
            timestamp="2026-03-01T10:00:00",
            strategy_id="test_strat",
            model_version="v1",
            triggered_by="agent",
            summary="test summary",
        )
        d = rec.to_dict()
        assert d["trace_id"] == "tr_abc_001"
        assert d["event_type"] == "factor_generation"
        assert d["strategy_id"] == "test_strat"

    def test_from_dict_roundtrip(self) -> None:
        original = AuditRecord(
            trace_id="tr_xy_999",
            event_type="backtest_trigger",
            timestamp="2026-03-01T12:00:00",
            strategy_id="double_low",
            model_version="v2",
            triggered_by="user",
            summary="re-run",
            details={"config_hash": "abc123"},
        )
        d = original.to_dict()
        restored = AuditRecord.from_dict(d)
        assert restored.trace_id == original.trace_id
        assert restored.details["config_hash"] == "abc123"


# ── AgentAudit log_* 方法测试 ────────────────────────────────────

class TestAgentAuditLogMethods:
    def test_log_factor_generation_returns_trace_id(self, audit: AgentAudit) -> None:
        tid = audit.log_factor_generation(
            factor_name="ep_ratio",
            prompt_hash="abc123",
            code="def ep_ratio(df): return df['net_profit'] / df['total_mv']",
        )
        assert isinstance(tid, str)
        assert tid.startswith("tr_")

    def test_log_factor_writes_jsonl(self, audit: AgentAudit, tmp_audit_dir: Path) -> None:
        audit.log_factor_generation("roe_q", "hash001", "def roe_q(): pass")
        files = list(tmp_audit_dir.glob("agent_audit_*.jsonl"))
        assert files
        lines = [l for l in files[0].read_text().splitlines() if l.strip()]
        assert len(lines) >= 1
        rec = json.loads(lines[-1])
        assert rec["event_type"] == AuditEventType.FACTOR_GENERATION.value

    def test_log_backtest_trigger(self, audit: AgentAudit) -> None:
        tid = audit.log_backtest_trigger(
            strategy_id="double_low",
            config_hash="cfg_hash_xyz",
            triggered_by="agent",
        )
        assert tid.startswith("tr_")

    def test_log_model_update(self, audit: AgentAudit) -> None:
        tid = audit.log_model_update(
            model_id="lgb_csi2000_20d",
            old_version="v1.0",
            new_version="v1.1",
            metrics_delta={"ic": 0.002, "icir": 0.05},
        )
        assert tid.startswith("tr_")

    def test_log_code_modification(self, audit: AgentAudit) -> None:
        tid = audit.log_code_modification(
            file_path="src/aipha/factors/ep_ratio.py",
            diff_summary="add PIT-aligned calculation",
            lines_changed=15,
        )
        assert tid.startswith("tr_")

    def test_model_version_stored_in_record(
        self, audit: AgentAudit, tmp_audit_dir: Path
    ) -> None:
        audit.log_backtest_trigger("strat_x", "hash123")
        files = list(tmp_audit_dir.glob("agent_audit_*.jsonl"))
        rec = json.loads(files[0].read_text().strip())
        assert rec["model_version"] == "test-v1"

    def test_get_model_version(self, audit: AgentAudit) -> None:
        assert audit.get_model_version() == "test-v1"


# ── AgentAudit query 测试 ────────────────────────────────────────

class TestAgentAuditQuery:
    @pytest.fixture()
    def populated_audit(self, audit: AgentAudit) -> AgentAudit:
        """预填充 5 条审计记录的 AgentAudit"""
        audit.log_factor_generation("ep_ratio", "h1", "def a(): pass", strategy_id="strat_a")
        audit.log_backtest_trigger("strat_a", "cfg1")
        audit.log_backtest_trigger("strat_b", "cfg2")
        audit.log_model_update("lgb", "v1", "v2", strategy_id="strat_a")
        audit.log_code_modification("factors/ep.py", "fix", lines_changed=5)
        return audit

    def test_query_by_strategy_id(self, populated_audit: AgentAudit) -> None:
        results = populated_audit.query_audit_trail(strategy_id="strat_a")
        assert len(results) == 3
        assert all(r.strategy_id == "strat_a" for r in results)

    def test_query_by_event_type(self, populated_audit: AgentAudit) -> None:
        results = populated_audit.query_audit_trail(
            event_type=AuditEventType.BACKTEST_TRIGGER
        )
        assert len(results) == 2

    def test_query_by_since(self, populated_audit: AgentAudit) -> None:
        future = datetime.now() + timedelta(hours=1)
        results = populated_audit.query_audit_trail(since=future)
        assert results == []

    def test_query_limit(self, populated_audit: AgentAudit) -> None:
        results = populated_audit.query_audit_trail(limit=2)
        assert len(results) <= 2

    def test_tail_returns_n_records(self, populated_audit: AgentAudit) -> None:
        results = populated_audit.tail(n=3)
        assert len(results) == 3

    def test_tail_on_empty_dir(self, tmp_path: Path) -> None:
        empty_audit = AgentAudit(log_dir=tmp_path / "empty_audit")
        assert empty_audit.tail() == []

    def test_mlflow_disabled_by_default(self, audit: AgentAudit) -> None:
        """不启用 MLflow 时，_mlflow_enabled 应为 False"""
        assert not audit._mlflow_enabled

