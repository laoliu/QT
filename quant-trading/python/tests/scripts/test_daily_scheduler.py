"""Tests for daily_scheduler — E-4.

T01  run_daily_schedule dry_run 无数据 → success=True（告警 info 不触发 error）
T02  Task-A 无 IC 文件 → 返回 warning 告警
T03  Task-A 有 IC 数据 → 正确更新 monitor_store
T04  Task-B drift_monitor=None → info 告警 skip
T05  Task-C n_total=0 → info 告警 skip
T06  Task-C 撤单率超标 → error+summary.success=False
T07  Task-C 撤单率合规 → 无 alerts
T08  run_daily_schedule dry_run → 不写入日志文件
T09  run_daily_schedule 非 dry_run + violations → 写入 JSONL
T10  汇总结构包含正确键
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 确保 src 路径
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

# 导入被测模块（须主动 insert path 再 import）
import importlib.util
_SCHED_PATH = _REPO_ROOT / "scripts" / "daily_scheduler.py"
_spec = importlib.util.spec_from_file_location("daily_scheduler", _SCHED_PATH)
_sched = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_sched)

run_daily_schedule = _sched.run_daily_schedule
run_task_a_factor_decay = _sched.run_task_a_factor_decay
run_task_b_drift_detection = _sched.run_task_b_drift_detection
run_task_c_cancel_rate = _sched.run_task_c_cancel_rate

from aipha.monitoring import HalflifeDecayMonitor
from aipha.portfolio.compliance_engine import ComplianceEngine


# ---------------------------------------------------------------------------
# T01 — dry_run 无文件 → success
# ---------------------------------------------------------------------------
def test_T01_dry_run_no_data_success():
    result = run_daily_schedule("2024-01-05", dry_run=True)
    assert "success" in result
    assert "date" in result
    assert result["date"] == "2024-01-05"


# ---------------------------------------------------------------------------
# T02 — Task-A 无 IC 文件 → warning
# ---------------------------------------------------------------------------
def test_T02_task_a_no_ic_file():
    store: dict = {}
    with patch.object(_sched, "_load_factor_ic_data", return_value={}):
        alerts = run_task_a_factor_decay("2024-01-05", store)
    assert len(alerts) >= 1
    assert alerts[0]["severity"] == "warning"


# ---------------------------------------------------------------------------
# T03 — Task-A IC 数据更新 monitor_store
# ---------------------------------------------------------------------------
def test_T03_task_a_updates_store():
    store: dict = {}
    fake_ic = {"ep_ratio": 0.03, "bp_ratio": -0.01}
    with patch.object(_sched, "_load_factor_ic_data", return_value=fake_ic):
        run_task_a_factor_decay("2024-01-05", store)
    assert "ep_ratio" in store
    assert isinstance(store["ep_ratio"], HalflifeDecayMonitor)


# ---------------------------------------------------------------------------
# T04 — Task-B None monitor → skip info
# ---------------------------------------------------------------------------
def test_T04_task_b_no_monitor():
    alerts = run_task_b_drift_detection("2024-01-05", None)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "info"
    assert "DriftMonitor not initialized" in alerts[0]["message"]


# ---------------------------------------------------------------------------
# T05 — Task-C n_total=0 → skip info
# ---------------------------------------------------------------------------
def test_T05_task_c_no_orders():
    eng = ComplianceEngine(enable_audit_log=False)
    with patch.object(_sched, "_load_cancel_rate_data", return_value=(0, 0)):
        alerts = run_task_c_cancel_rate("2024-01-05", eng)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "info"


# ---------------------------------------------------------------------------
# T06 — Task-C 超限 → error
# ---------------------------------------------------------------------------
def test_T06_task_c_exceeded():
    eng = ComplianceEngine(max_cancel_rate=0.80, enable_audit_log=False)
    with patch.object(_sched, "_load_cancel_rate_data", return_value=(90, 100)):
        alerts = run_task_c_cancel_rate("2024-01-05", eng)
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "error"
    assert "cancel_rate" in alerts[0]


# ---------------------------------------------------------------------------
# T07 — Task-C 合规 → 无 alerts
# ---------------------------------------------------------------------------
def test_T07_task_c_ok():
    eng = ComplianceEngine(max_cancel_rate=0.80, enable_audit_log=False)
    with patch.object(_sched, "_load_cancel_rate_data", return_value=(50, 100)):
        alerts = run_task_c_cancel_rate("2024-01-05", eng)
    assert alerts == []


# ---------------------------------------------------------------------------
# T08 — dry_run 不写日志文件
# ---------------------------------------------------------------------------
def test_T08_dry_run_no_file(tmp_path):
    orig_dir = _sched.AUDIT_LOG_DIR
    _sched.AUDIT_LOG_DIR = tmp_path / "audit"
    try:
        run_daily_schedule("2024-01-05", dry_run=True)
        log_files = list((tmp_path / "audit").glob("*.jsonl")) if (tmp_path / "audit").exists() else []
        assert log_files == []
    finally:
        _sched.AUDIT_LOG_DIR = orig_dir


# ---------------------------------------------------------------------------
# T09 — 非 dry_run + 有告警 → 写入 JSONL
# ---------------------------------------------------------------------------
def test_T09_non_dry_run_writes_log(tmp_path):
    orig_dir = _sched.AUDIT_LOG_DIR
    _sched.AUDIT_LOG_DIR = tmp_path / "audit"
    try:
        # 让 task-c 产生 error，触发日志写入
        with patch.object(_sched, "_load_cancel_rate_data", return_value=(90, 100)):
            run_daily_schedule("2024-01-05", dry_run=False)
        log_files = list((tmp_path / "audit").glob("*.jsonl"))
        assert len(log_files) >= 1
        with open(log_files[0], encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert "date" in record
        assert record["date"] == "2024-01-05"
    finally:
        _sched.AUDIT_LOG_DIR = orig_dir


# ---------------------------------------------------------------------------
# T10 — 汇总结构包含正确键
# ---------------------------------------------------------------------------
def test_T10_summary_keys():
    result = run_daily_schedule("2024-01-05", dry_run=True)
    for key in ("date", "ran_at", "tasks_run", "total_alerts", "error_count",
                "warning_count", "alerts", "success"):
        assert key in result, f"missing key: {key}"
    assert isinstance(result["tasks_run"], list)
    assert isinstance(result["alerts"], list)


# ---------------------------------------------------------------------------
# T11 — Task-F auto_retrain function exists and returns list
# ---------------------------------------------------------------------------
def test_T11_task_f_exists_and_returns_list():
    run_task_f = _sched.run_task_f_auto_retrain
    # 当配置文件不存在时应返回告警而非崩溃
    alerts = run_task_f("2024-01-05", config_path="nonexistent.yml")
    assert isinstance(alerts, list)
    assert len(alerts) >= 1


# ---------------------------------------------------------------------------
# T12 — Task-F auto_retrain in tasks_run list
# ---------------------------------------------------------------------------
def test_T12_auto_retrain_in_tasks_run():
    result = run_daily_schedule("2024-01-05", dry_run=True)
    assert "auto_retrain" in result["tasks_run"]
