"""Strategy catalog & version management API router."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from aipha.api.auth import get_current_user, require_role
from aipha.api.deps import get_result_store
from aipha.api.schemas import ApiResponse, PaginatedResponse, PaginationMeta, UserInfo
from aipha.utils.paths import project_path

router = APIRouter(prefix="/strategies", tags=["strategies"])

# 锚到项目根，不吃当前工作目录（见 aipha/utils/paths.py）
_CATALOG_PATH = project_path("configs", "strategy_catalog.json")
_STATUS_PATH = project_path("configs", "strategy_status.json")
_LIFECYCLE_STAGES = [f"S{i}" for i in range(1, 8)]
_VALID_STATUSES = {"active", "archived", "deprecated", "experimental"}


def _lifecycle_key(strategy_name: str) -> str:
    return f"strategy_lifecycle::{strategy_name}"


def _default_lifecycle_state(stage: str) -> Dict[str, Any]:
    return {"stage": stage, "checklist": {}, "logs": []}


def _load_lifecycle_state(store, strategy_name: str,
                          default_stage: str) -> Dict[str, Any]:
    data = store.get_system_setting(
        _lifecycle_key(strategy_name),
        _default_lifecycle_state(default_stage),
    )
    if not isinstance(data, dict):
        return _default_lifecycle_state(default_stage)
    return {
        "stage": data.get("stage", default_stage),
        "checklist": data.get("checklist", {}),
        "logs": data.get("logs", []),
    }


def _save_lifecycle_state(store, strategy_name: str,
                          state: Dict[str, Any], user: str = "") -> None:
    store.set_system_setting(_lifecycle_key(strategy_name), state, updated_by=user)


def _find_strategy(strategy_name: str) -> Dict[str, Any]:
    catalog = _load_catalog()
    for atype, strats in catalog.items():
        for strategy in strats:
            if strategy.get("name") == strategy_name:
                result = dict(strategy)
                result["asset_type"] = atype
                return result
    raise HTTPException(status_code=404, detail="Strategy not found")


def _append_log(state: Dict[str, Any], *, from_stage: str, to_stage: str,
                user: str, action: str, comment: Optional[str] = None) -> None:
    logs = state.setdefault("logs", [])
    logs.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "from_stage": from_stage,
        "to_stage": to_stage,
        "user": user,
        "action": action,
        "comment": comment or "",
    })


def _load_catalog() -> Dict[str, Any]:
    if not _CATALOG_PATH.exists():
        return {}
    with open(_CATALOG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _load_status_overrides() -> Dict[str, Dict[str, Any]]:
    """Load strategy status overrides from configs/strategy_status.json."""
    if not _STATUS_PATH.exists():
        return {}
    try:
        with open(_STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("overrides", {})
    except Exception:
        return {}


def _get_strategy_status(name: str, category: str) -> str:
    """Determine effective status for a strategy."""
    overrides = _load_status_overrides()
    if name in overrides:
        return overrides[name].get("status", "active")
    # demo/other categories treated as experimental
    if category in ("demo", "other"):
        return "experimental"
    return "active"


def _get_status_reason(name: str) -> str:
    """Get the reason for non-active status."""
    overrides = _load_status_overrides()
    if name in overrides:
        return overrides[name].get("reason", "")
    return ""


@router.get("/", response_model=PaginatedResponse[Dict[str, Any]])
async def list_strategies(
    asset_type: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filter by status: active/archived/deprecated/experimental/all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: UserInfo = Depends(get_current_user),
):
    """List strategies from catalog with optional filters.

    By default hides archived/deprecated/experimental strategies.
    Use status=all to show everything, or status=archived to show only archived.
    """
    catalog = _load_catalog()
    items: List[Dict[str, Any]] = []
    for atype, strats in catalog.items():
        if asset_type and atype != asset_type:
            continue
        for s in strats:
            if category and s.get("category") != category:
                continue
            if search and search.lower() not in s.get("name", "").lower():
                continue
            s_copy = dict(s)
            s_copy["asset_type"] = atype
            effective_status = _get_strategy_status(s["name"], s_copy.get("category", ""))
            s_copy["quality_status"] = effective_status
            s_copy["status_reason"] = _get_status_reason(s["name"])
            # Filter by status
            if status and status != "all":
                if effective_status != status:
                    continue
            elif not status:
                # Default: hide archived/deprecated/experimental
                if effective_status in ("archived", "deprecated", "experimental"):
                    continue
            items.append(s_copy)

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]
    return PaginatedResponse(
        data=page_items,
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
    )


@router.get("/{strategy_name}", response_model=ApiResponse[Dict[str, Any]])
async def get_strategy(
    strategy_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get strategy details by name."""
    store = get_result_store()
    strategy = _find_strategy(strategy_name)
    lifecycle_state = _load_lifecycle_state(store, strategy_name, strategy.get("stage", "S1"))
    strategy["stage"] = lifecycle_state.get("stage", strategy.get("stage", "S1"))
    strategy["lifecycle_stage"] = strategy["stage"]
    strategy["quality_status"] = _get_strategy_status(strategy_name, strategy.get("category", ""))
    strategy["status_reason"] = _get_status_reason(strategy_name)
    return ApiResponse(data=strategy)


@router.get("/lifecycle/board", response_model=ApiResponse[Dict[str, Any]])
async def get_lifecycle_board(
    show_archived: bool = Query(False, description="Include archived/deprecated strategies"),
    _user: UserInfo = Depends(get_current_user),
):
    """Get strategies grouped by lifecycle stage for Kanban board.

    By default hides archived/deprecated/experimental strategies.
    """
    catalog = _load_catalog()
    store = get_result_store()
    board: Dict[str, List[Dict[str, Any]]] = {
        f"S{i}": [] for i in range(1, 8)
    }
    for _atype, strats in catalog.items():
        for s in strats:
            effective_status = _get_strategy_status(s["name"], s.get("category", ""))
            if not show_archived and effective_status in ("archived", "deprecated", "experimental"):
                continue
            lifecycle_state = _load_lifecycle_state(store, s["name"], s.get("stage", "S1"))
            stage = lifecycle_state.get("stage", s.get("stage", "S1"))
            if stage in board:
                board[stage].append({
                    "name": s["name"],
                    "stage": stage,
                    "quality_status": effective_status,
                    "checklist_state": lifecycle_state.get("checklist", {}),
                    "sharpe": s.get("sharpe_ratio") or s.get("sharpe"),
                    "ar": s.get("annualized_return") or s.get("ar"),
                    "mdd": s.get("max_drawdown") or s.get("mdd"),
                })
    return ApiResponse(data={"stages": board})


@router.get("/{strategy_name}/versions", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_strategy_versions(
    strategy_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get version history for a strategy."""
    versions_path = project_path("configs", "strategy_versions.json")
    versions = []
    if versions_path.exists():
        import json as json_mod
        all_versions = json_mod.loads(versions_path.read_text(encoding="utf-8"))
        versions = [v for v in all_versions if v.get("strategy_name") == strategy_name]
    return ApiResponse(data=versions)


@router.post("/{strategy_name}/versions", response_model=ApiResponse[Dict[str, Any]])
async def create_strategy_version(
    strategy_name: str,
    body: Dict[str, Any],
    user: UserInfo = Depends(require_role("researcher")),
):
    """Create a new version for a strategy."""
    import uuid
    version_id = f"v_{uuid.uuid4().hex[:8]}"
    return ApiResponse(data={"version_id": version_id, "strategy_name": strategy_name})


@router.get("/versions/{version_a}/diff/{version_b}", response_model=ApiResponse[Dict[str, Any]])
async def get_version_diff(
    version_a: str,
    version_b: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get diff between two strategy versions."""
    return ApiResponse(data={"version_a": version_a, "version_b": version_b, "diff": []})


@router.put("/{strategy_name}/lifecycle", response_model=ApiResponse)
async def update_lifecycle(
    strategy_name: str,
    body: Dict[str, Any],
    user: UserInfo = Depends(require_role("researcher")),
):
    """Update strategy lifecycle stage."""
    new_stage = body.get("stage", "")
    if new_stage not in _LIFECYCLE_STAGES:
        raise HTTPException(status_code=400, detail="Invalid lifecycle stage")
    store = get_result_store()
    strategy = _find_strategy(strategy_name)
    state = _load_lifecycle_state(store, strategy_name, strategy.get("stage", "S1"))
    old_stage = state.get("stage", strategy.get("stage", "S1"))
    state["stage"] = new_stage
    _append_log(state, from_stage=old_stage, to_stage=new_stage, user=user.username, action="update", comment=body.get("comment"))
    _save_lifecycle_state(store, strategy_name, state, user.username)
    return ApiResponse(message=f"Strategy {strategy_name} moved to {new_stage}")


@router.get("/{strategy_name}/lifecycle/log", response_model=ApiResponse[Dict[str, Any]])
async def get_lifecycle_log(
    strategy_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get persisted lifecycle transition log."""
    store = get_result_store()
    strategy = _find_strategy(strategy_name)
    state = _load_lifecycle_state(store, strategy_name, strategy.get("stage", "S1"))
    return ApiResponse(data={"entries": list(reversed(state.get("logs", [])))})


@router.get("/{strategy_name}/lifecycle/checklist", response_model=ApiResponse[Dict[str, Any]])
async def get_lifecycle_checklist(
    strategy_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get persisted lifecycle checklist state."""
    store = get_result_store()
    strategy = _find_strategy(strategy_name)
    state = _load_lifecycle_state(store, strategy_name, strategy.get("stage", "S1"))
    return ApiResponse(data={"checklist": state.get("checklist", {}), "stage": state.get("stage")})


@router.put("/{strategy_name}/lifecycle/checklist", response_model=ApiResponse)
async def update_lifecycle_checklist(
    strategy_name: str,
    body: Dict[str, Any],
    user: UserInfo = Depends(require_role("risk_approver")),
):
    """Persist lifecycle checklist state for a strategy."""
    checklist = body.get("checklist")
    if not isinstance(checklist, dict):
        raise HTTPException(status_code=400, detail="checklist must be a dict")
    store = get_result_store()
    strategy = _find_strategy(strategy_name)
    state = _load_lifecycle_state(store, strategy_name, strategy.get("stage", "S1"))
    state["checklist"] = checklist
    _save_lifecycle_state(store, strategy_name, state, user.username)
    return ApiResponse(message="Checklist updated")


@router.post("/{strategy_name}/lifecycle/approve", response_model=ApiResponse)
async def approve_lifecycle_transition(
    strategy_name: str,
    body: Dict[str, Any],
    user: UserInfo = Depends(require_role("risk_approver")),
):
    """Approve or reject lifecycle transition with persisted log."""
    action = body.get("action")
    if action not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="Invalid action")
    store = get_result_store()
    strategy = _find_strategy(strategy_name)
    state = _load_lifecycle_state(store, strategy_name, strategy.get("stage", "S1"))
    current_stage = state.get("stage", strategy.get("stage", "S1"))
    try:
        idx = _LIFECYCLE_STAGES.index(current_stage)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid current stage") from exc
    next_idx = min(idx + 1, len(_LIFECYCLE_STAGES) - 1) if action == "approve" else max(idx - 1, 0)
    target_stage = _LIFECYCLE_STAGES[next_idx]
    state["stage"] = target_stage
    _append_log(state, from_stage=current_stage, to_stage=target_stage, user=user.username, action=action, comment=body.get("comment"))
    _save_lifecycle_state(store, strategy_name, state, user.username)
    return ApiResponse(message=f"Strategy {strategy_name} {action}d to {target_stage}")
