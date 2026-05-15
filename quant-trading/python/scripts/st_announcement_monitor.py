#!/usr/bin/env python3
# coding: utf-8
"""
ST Restructuring Announcement Monitor — ST重整公告监控

Automated daily monitor that:
1. Scans Eastmoney announcements for restructuring-related keywords
2. Tracks ST stocks through their entire restructuring lifecycle
3. Alerts on stage transitions (especially MAIN-6 and MAIN-7)
4. Can be scheduled via Windows Task Scheduler or cron

Usage:
  # One-shot scan
  python st_announcement_monitor.py

  # Schedule: daily at 14:30 (after most announcements are published)
  python st_announcement_monitor.py --schedule 14:30

  # Install as Windows Task Scheduler task
  python st_announcement_monitor.py --install-task

  # Scan specific stock
  python st_announcement_monitor.py --stock 600165

  # Show current status
  python st_announcement_monitor.py --status

  # Scan with more history
  python st_announcement_monitor.py --days 180
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root importable
_SCRIPT_DIR = Path(__file__).resolve().parent
_PYTHON_ROOT = _SCRIPT_DIR.parent        # quant-trading/python/
_PROJECT_ROOT = _PYTHON_ROOT.parent       # quant-trading/
sys.path.insert(0, str(_PYTHON_ROOT))

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


# ── Output paths ──────────────────────────────────────────────────────────────
_QTDATA_CACHE = Path(os.environ.get("QTDATA_DIR", "G:/qtdata")) / "cache" / "st_restructuring"
_CACHE_DIR = _QTDATA_CACHE if _QTDATA_CACHE.parent.exists() else _PYTHON_ROOT / "cache"
_LOG_DIR = _PROJECT_ROOT / "logs"
_REPORT_FILE = _CACHE_DIR / "st_restructuring_report.json"
_ALERT_FILE = _CACHE_DIR / "st_restructuring_alerts.json"


def _log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] [{level}] {msg}"
    print(line)

    # Also write to log file
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _LOG_DIR / "st_monitor.log"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_scan(days: int = 90, stock: str | None = None) -> dict:
    """Run restructuring scan and generate report."""
    from engines.st_restructuring import (
        scan_all_restructuring,
        track_stock,
        get_status,
        STAGES,
    )

    _log(f"Starting scan (days={days}, stock={stock or 'all'})")

    if stock:
        record = track_stock(stock, days=days)
        report = {
            "scan_time": datetime.now().isoformat(),
            "mode": "single_stock",
            "stock": record.to_dict(),
        }
        _log(f"Stock {stock} ({record.name}): stage={record.current_stage} "
             f"({record.stage_label}), days={record.days_in_process}")
    else:
        records = scan_all_restructuring(days=days)
        status = get_status()

        # Detect stage transitions (compare with previous report)
        alerts = _detect_transitions(records)

        report = {
            "scan_time": datetime.now().isoformat(),
            "mode": "full_scan",
            "total_tracked": len(records),
            "stage_summary": status.get("stage_summary", {}),
            "executing_plan": status.get("executing_plan", []),
            "completed": status.get("completed", []),
            "risk_removed": status.get("risk_removed", []),
            "alerts": alerts,
            "records": {code: r.to_dict() for code, r in records.items()},
        }

        _log(f"Scan complete: {len(records)} stocks tracked")
        _log(f"Stage summary: {json.dumps(status.get('stage_summary', {}), ensure_ascii=False)}")

        if status.get("executing_plan"):
            _log("90 Stocks EXECUTING restructuring plan (MAIN-6):")
            for s in status["executing_plan"]:
                _log(f"  {s['code']} {s['name']} — {s['days_in_process']}d in process")

        if status.get("completed"):
            _log("73 Stocks with COMPLETED restructuring (MAIN-7):")
            for s in status["completed"]:
                _log(f"  {s['code']} {s['name']}")

        if status.get("risk_removed"):
            _log("95 Stocks with risk warning REMOVED:")
            for s in status["risk_removed"]:
                _log(f"  {s['code']} {s['name']}")

        if alerts:
            _log(f"73 {len(alerts)} stage transitions detected!", "ALERT")
            for alert in alerts:
                _log(f"  {alert['code']} {alert['name']}: "
                     f"{alert['old_stage']} → {alert['new_stage']}", "ALERT")

    # Save report
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    _log(f"Report saved to {_REPORT_FILE}")

    return report


def _detect_transitions(records: dict) -> list[dict]:
    """Compare current records with previous report to detect stage changes."""
    alerts: list[dict] = []

    # Load previous report
    if _REPORT_FILE.exists():
        try:
            with open(_REPORT_FILE, "r", encoding="utf-8") as f:
                prev = json.load(f)
            prev_records = prev.get("records", {})
        except (json.JSONDecodeError, OSError):
            prev_records = {}
    else:
        prev_records = {}

    for code, record in records.items():
        new_stage = record.current_stage
        old_data = prev_records.get(code, {})
        old_stage = old_data.get("current_stage", "")

        if old_stage and old_stage != new_stage:
            alerts.append({
                "code": code,
                "name": record.name,
                "old_stage": old_stage,
                "new_stage": new_stage,
                "new_stage_label": record.stage_label,
                "detected_at": datetime.now().isoformat(),
            })

    # Save alerts
    if alerts:
        existing_alerts = []
        if _ALERT_FILE.exists():
            try:
                with open(_ALERT_FILE, "r", encoding="utf-8") as f:
                    existing_alerts = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        existing_alerts.extend(alerts)
        # Keep last 200 alerts
        existing_alerts = existing_alerts[-200:]
        with open(_ALERT_FILE, "w", encoding="utf-8") as f:
            json.dump(existing_alerts, f, ensure_ascii=False, indent=2)

    return alerts


def show_status() -> None:
    """Display current tracking status."""
    from engines.st_restructuring import get_status, STAGES

    status = get_status()
    print("\n" + "=" * 60)
    print("  ST重整跟踪状态报告")
    print("=" * 60)
    print(f"  1107: {status.get('total_tracked', 0)}")
    print()

    summary = status.get("stage_summary", {})
    if summary:
        print("  96 000302:")
        from engines.st_restructuring import STAGE_ORDER
        for stage in STAGE_ORDER:
            count = summary.get(stage, 0)
            if count > 0:
                label = STAGES.get(stage, "")
                bar = "█" * count
                print(f"    {stage:8s} {label:12s}  {bar} ({count})")

    executing = status.get("executing_plan", [])
    if executing:
        print(f"\n  7515  正在执行重整计划 (MAIN-6): {len(executing)} 01")
        for s in executing:
            print(f"    {s['code']} {s['name']:8s}  {s['days_in_process']}天  "
                  f"risk={s.get('risk_level', '')}")

    completed = status.get("completed", [])
    if completed:
        print(f"\n  73 重整执行完毕 (MAIN-7): {len(completed)} 01")
        for s in completed:
            print(f"    {s['code']} {s['name']:8s}  {s['days_in_process']}天")

    removed = status.get("risk_removed", [])
    if removed:
        print(f"\n  95 0009/0005: {len(removed)} 01")
        for s in removed:
            print(f"    {s['code']} {s['name']}")

    unknown = summary.get("UNKNOWN", 0)
    if unknown:
        print(f"\n  71 00: {unknown} 01")

    print("\n" + "=" * 60)

    # Show latest alerts
    if _ALERT_FILE.exists():
        try:
            with open(_ALERT_FILE, "r", encoding="utf-8") as f:
                alerts = json.load(f)
            recent = [a for a in alerts[-10:]]
            if recent:
                print("\n  73 0500:")
                for a in recent:
                    print(f"    {a['detected_at'][:16]}  {a['code']} {a['name']}: "
                          f"{a['old_stage']} → {a['new_stage']} ({a.get('new_stage_label', '')})")
        except (json.JSONDecodeError, OSError):
            pass

    print()


def run_scheduled(schedule_time: str) -> None:
    """Run scan at scheduled time daily (blocking loop).

    Args:
        schedule_time: "HH:MM" format, e.g. "14:30"
    """
    hour, minute = map(int, schedule_time.split(":"))
    _log(f"Scheduled mode: will run daily at {schedule_time}")

    while True:
        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)

        wait_seconds = (target - now).total_seconds()
        _log(f"Next scan at {target.strftime('%Y-%m-%d %H:%M')} "
             f"(in {wait_seconds / 3600:.1f}h)")

        time.sleep(wait_seconds)

        try:
            run_scan(days=30)  # Daily scan: only look back 30 days
        except Exception as e:
            _log(f"Scan failed: {e}", "ERROR")

        # Brief pause to avoid re-trigger
        time.sleep(60)


def install_windows_task(schedule_time: str = "14:30") -> None:
    """Install as Windows Task Scheduler task."""
    if platform.system() != "Windows":
        _log("Windows Task Scheduler only available on Windows", "ERROR")
        return

    python_exe = sys.executable
    script_path = str(Path(__file__).resolve())
    task_name = "QT_ST_Restructuring_Monitor"

    # Build schtasks command
    cmd = [
        "schtasks", "/Create", "/F",
        "/TN", task_name,
        "/TR", f'"{python_exe}" "{script_path}" --days 30',
        "/SC", "DAILY",
        "/ST", schedule_time,
        "/RL", "HIGHEST",
    ]

    _log(f"Installing Windows Task: {task_name} at {schedule_time}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        _log(f"Task installed successfully: {result.stdout.strip()}")
        _log(f"Task name: {task_name}")
        _log(f"Schedule: Daily at {schedule_time}")
        _log(f"Command: {python_exe} {script_path} --days 30")
    except subprocess.CalledProcessError as e:
        _log(f"Failed to install task: {e.stderr}", "ERROR")
        _log("Try running as Administrator", "ERROR")


def uninstall_windows_task() -> None:
    """Remove the Windows Task Scheduler task."""
    task_name = "QT_ST_Restructuring_Monitor"
    try:
        subprocess.run(
            ["schtasks", "/Delete", "/TN", task_name, "/F"],
            capture_output=True, text=True, check=True,
        )
        _log(f"Task '{task_name}' removed.")
    except subprocess.CalledProcessError as e:
        _log(f"Failed to remove task: {e.stderr}", "ERROR")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ST重整公告监控 — Monitor ST stock restructuring announcements"
    )
    parser.add_argument("--days", type=int, default=90,
                        help="Look back N days for announcements (default: 90)")
    parser.add_argument("--stock", type=str, default=None,
                        help="Track a specific stock (e.g. 600165)")
    parser.add_argument("--status", action="store_true",
                        help="Show current tracking status")
    parser.add_argument("--schedule", type=str, default=None, metavar="HH:MM",
                        help="Run in scheduled mode, scanning daily at HH:MM (e.g. 14:30)")
    parser.add_argument("--install-task", action="store_true",
                        help="Install as Windows Task Scheduler task (daily at 14:30)")
    parser.add_argument("--uninstall-task", action="store_true",
                        help="Remove the Windows Task Scheduler task")
    parser.add_argument("--json", action="store_true",
                        help="Output raw JSON instead of formatted text")

    args = parser.parse_args()

    if args.uninstall_task:
        uninstall_windows_task()
        return

    if args.install_task:
        install_windows_task("14:30")
        return

    if args.status:
        if args.json:
            from engines.st_restructuring import get_status
            print(json.dumps(get_status(), ensure_ascii=False, indent=2))
        else:
            show_status()
        return

    if args.schedule:
        run_scheduled(args.schedule)
        return

    # One-shot scan
    report = run_scan(days=args.days, stock=args.stock)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
