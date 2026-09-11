"""Backtest detail endpoints — single run, NAV, trades, attribution."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from aipha.api.auth import get_current_user
from aipha.api.deps import get_result_store
from aipha.api.schemas import ApiResponse, UserInfo
from aipha.utils.paths import project_path

router = APIRouter(prefix="/backtests", tags=["backtests"])

# 锚到项目根，不吃当前工作目录（见 aipha/utils/paths.py）
_OUTPUT_DIR = project_path("output")


def _load_json(path: Path) -> Optional[Dict]:
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


@router.get("/{run_id}", response_model=ApiResponse[Dict[str, Any]])
async def get_backtest_detail(
    run_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get full backtest details including config and fingerprint."""
    store = get_result_store()
    row = store.get_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    result = dict(row)
    # Enrich with file-based data
    run_dir = _OUTPUT_DIR / run_id
    if run_dir.is_dir():
        summary = _load_json(run_dir / "summary.json")
        if summary:
            result["summary"] = summary
        fp = _load_json(run_dir / "fingerprint.json")
        if fp:
            result["fingerprint"] = fp
        config_path = run_dir / "config.yaml"
        if config_path.exists():
            result["config_yaml"] = config_path.read_text(encoding="utf-8")
    return ApiResponse(data=result)


@router.get("/{run_id}/nav", response_model=ApiResponse[Dict[str, Any]])
async def get_backtest_nav(
    run_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get NAV time series for a backtest run."""
    run_dir = _OUTPUT_DIR / run_id
    summary = _load_json(run_dir / "summary.json") if run_dir.is_dir() else None
    if summary is None:
        raise HTTPException(status_code=404, detail="NAV data not found")
    nav_data = summary.get("nav_series") or summary.get("equity_curve")
    return ApiResponse(data={"run_id": run_id, "nav": nav_data})


@router.get("/{run_id}/trades", response_model=ApiResponse[Dict[str, Any]])
async def get_backtest_trades(
    run_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get trade list for a backtest run."""
    run_dir = _OUTPUT_DIR / run_id
    summary = _load_json(run_dir / "summary.json") if run_dir.is_dir() else None
    trades = []
    if summary:
        trades = summary.get("trades", [])
    # Also check audit log
    audit_dir = run_dir / "audit" if run_dir.is_dir() else None
    audit_entries = []
    if audit_dir and audit_dir.is_dir():
        for f in sorted(audit_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    try:
                        audit_entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return ApiResponse(
        data={"run_id": run_id, "trades": trades, "audit_count": len(audit_entries)}
    )


@router.get("/{run_id}/attribution", response_model=ApiResponse[Dict[str, Any]])
async def get_backtest_attribution(
    run_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get attribution analysis for a backtest run."""
    run_dir = _OUTPUT_DIR / run_id
    summary = _load_json(run_dir / "summary.json") if run_dir.is_dir() else None
    attr = {}
    if summary:
        attr = summary.get("attribution", {})
    return ApiResponse(data=attr)


@router.get("/{run_id}/yearly", response_model=ApiResponse[Dict[str, Any]])
async def get_backtest_yearly(
    run_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get yearly stats for a backtest run."""
    run_dir = _OUTPUT_DIR / run_id
    summary = _load_json(run_dir / "summary.json") if run_dir.is_dir() else None
    years = []
    if summary:
        years = summary.get("yearly_stats", [])
    return ApiResponse(data={"run_id": run_id, "years": years})


@router.get("/{run_id}/audit", response_model=ApiResponse[Dict[str, Any]])
async def get_backtest_audit(
    run_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get audit log for a backtest run."""
    run_dir = _OUTPUT_DIR / run_id
    audit_dir = run_dir / "audit" if run_dir.is_dir() else None
    entries: List[Dict] = []
    if audit_dir and audit_dir.is_dir():
        for f in sorted(audit_dir.glob("*.jsonl")):
            for line in f.read_text(encoding="utf-8").strip().split("\n"):
                if line.strip():
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return ApiResponse(data={"run_id": run_id, "entries": entries})
