"""System admin API — users, config, tasks, audit."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
import yaml

from aipha.api.auth import get_current_user, require_role
from aipha.api.deps import get_result_store
from aipha.api.schemas import ApiResponse, PaginatedResponse, PaginationMeta, UserInfo

router = APIRouter(prefix="/system", tags=["system"])

_ROOT_DIR = Path(__file__).resolve().parents[4]
_UNIVERSE_CONFIG = _ROOT_DIR / "configs" / "universe" / "exclude_list.yml"
_ENV_EXAMPLE = _ROOT_DIR / ".env.example"


DEFAULT_PERMISSION_MATRIX = [
    {"module": "因子中心", "viewer": "只读", "researcher": "读写", "ops": "只读", "risk": "只读", "admin": "完全"},
    {"module": "策略中心", "viewer": "只读", "researcher": "读写", "ops": "只读", "risk": "审批", "admin": "完全"},
    {"module": "回测中心", "viewer": "只读", "researcher": "读写", "ops": "只读", "risk": "只读", "admin": "完全"},
    {"module": "组合管理", "viewer": "只读", "researcher": "读写", "ops": "编辑", "risk": "审批", "admin": "完全"},
    {"module": "交易执行", "viewer": "禁止", "researcher": "只读", "ops": "读写", "risk": "审批", "admin": "完全"},
    {"module": "数据中心", "viewer": "只读", "researcher": "只读", "ops": "读写", "risk": "只读", "admin": "完全"},
    {"module": "系统管理", "viewer": "禁止", "researcher": "禁止", "ops": "禁止", "risk": "禁止", "admin": "完全"},
]


def _load_permission_matrix(store) -> List[Dict[str, str]]:
    matrix = store.get_system_setting("permission_matrix", DEFAULT_PERMISSION_MATRIX)
    return matrix if isinstance(matrix, list) and matrix else DEFAULT_PERMISSION_MATRIX


def _load_market_rules(store) -> List[Dict[str, Any]]:
    try:
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond

        equity = MarketRulesCNEquity()
        cb = MarketRulesConvertibleBond()
        base = {
            "cn_equity": {
                "asset_type": "cn_equity",
                "commission_rate": equity.commission_rate,
                "commission_min": getattr(equity, "commission_min", 5.0),
                "stamp_tax_rate": equity.stamp_tax_rate,
                "transfer_fee_rate": getattr(equity, "transfer_fee_rate", 0.0),
                "slippage_bps": getattr(equity, "slippage_bps_default", 10.0),
                "lot_size": equity.lot_size,
                "t_plus_n": getattr(equity, "t_plus_n", getattr(equity, "t_plus", 1)),
                "price_limit_pct": getattr(equity, "price_limit_pct", 0.1),
            },
            "convertible_bond": {
                "asset_type": "convertible_bond",
                "commission_rate": cb.commission_rate,
                "commission_min": getattr(cb, "commission_min", 5),
                "stamp_tax_rate": cb.stamp_tax_rate,
                "transfer_fee_rate": getattr(cb, "transfer_fee_rate", 0.0),
                "slippage_bps": getattr(cb, "slippage_bps_default", 5.0),
                "lot_size": cb.lot_size,
                "t_plus_n": getattr(cb, "t_plus_n", getattr(cb, "t_plus", 0)),
                "price_limit_pct": getattr(cb, "price_limit_pct", 0.2),
            },
        }
    except ImportError:
        base = {}

    overrides = store.get_system_setting("market_rules_overrides", {})
    if isinstance(overrides, dict):
        for asset_type, values in overrides.items():
            if asset_type in base and isinstance(values, dict):
                base[asset_type].update(values)
    return list(base.values())


def _seed_for(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def _sort_by_timestamp(rows: List[Dict[str, Any]], key: str) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda row: str(row.get(key) or ""), reverse=True)


def _task_description(task_type: str, name: str) -> str:
    mapping = {
        "backtest": f"{name} 回测结果",
        "wf": f"{name} Walk-Forward 验证",
        "sweep": f"{name} 参数扫描",
        "data_update": f"{name} 数据更新",
    }
    return mapping.get(task_type, name)


def _build_completed_history_rows(store) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    conn = store.conn
    backtests = conn.execute(
        "SELECT run_id, strategy_name, run_type, created_at, output_dir FROM backtest_runs ORDER BY created_at DESC LIMIT 30"
    ).fetchall()
    for item in backtests:
        run_type = item["run_type"] if item["run_type"] in {"backtest", "wf", "sweep", "data_update"} else "backtest"
        rows.append({
            "task_id": item["run_id"],
            "type": run_type,
            "status": "completed",
            "progress": 100,
            "description": _task_description(run_type, item["strategy_name"]),
            "created_by": "system",
            "created_at": item["created_at"],
            "started_at": item["created_at"],
            "completed_at": item["created_at"],
            "duration_seconds": None,
            "result_id": item["run_id"],
            "output_dir": item["output_dir"],
        })

    wf_rows = conn.execute(
        "SELECT wf_id, strategy_name, created_at FROM wf_results ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    for item in wf_rows:
        rows.append({
            "task_id": item["wf_id"],
            "type": "wf",
            "status": "completed",
            "progress": 100,
            "description": _task_description("wf", item["strategy_name"]),
            "created_by": "system",
            "created_at": item["created_at"],
            "started_at": item["created_at"],
            "completed_at": item["created_at"],
            "duration_seconds": None,
            "result_id": item["wf_id"],
        })

    sweep_rows = conn.execute(
        "SELECT sweep_id, strategy_name, created_at, total_tasks, output_dir FROM sweep_runs ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    for item in sweep_rows:
        rows.append({
            "task_id": item["sweep_id"],
            "type": "sweep",
            "status": "completed",
            "progress": 100,
            "description": f"{item['strategy_name']} 参数扫描（{item['total_tasks']} 子任务）",
            "created_by": "system",
            "created_at": item["created_at"],
            "started_at": item["created_at"],
            "completed_at": item["created_at"],
            "duration_seconds": None,
            "result_id": item["sweep_id"],
            "output_dir": item["output_dir"],
        })
    return rows


def _build_task_rows(store) -> List[Dict[str, Any]]:
    persisted = store.get_system_setting("async_tasks", None)
    active_rows = persisted if isinstance(persisted, list) else []
    merged: Dict[str, Dict[str, Any]] = {}
    for row in _build_completed_history_rows(store):
        if row.get("task_id"):
            merged[str(row["task_id"])] = row
    for row in active_rows:
        if row.get("task_id"):
            merged[str(row["task_id"])] = row
    return _sort_by_timestamp(list(merged.values()), "created_at")


def _save_task_rows(store, rows: List[Dict[str, Any]], updated_by: str) -> None:
    store.set_system_setting("async_tasks", rows, updated_by=updated_by)


def _append_audit_entry(
    store,
    *,
    user: str,
    action: str,
    resource_type: str,
    resource_id: str,
    details: str,
    ip_address: str = "127.0.0.1",
) -> None:
    entries = store.get_system_setting("audit_entries", [])
    if not isinstance(entries, list):
        entries = []
    next_id = max((int(item.get("id", 0)) for item in entries), default=0) + 1
    entries.append({
        "id": next_id,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": user,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "details": details,
        "ip_address": ip_address,
    })
    store.set_system_setting("audit_entries", entries, updated_by=user)


def _task_log_lines(task: Dict[str, Any]) -> List[str]:
    lines = [
        f"[{task['created_at']}] 任务创建: {task['description']}",
        f"[{task.get('started_at') or task['created_at']}] 开始执行 type={task['type']} created_by={task['created_by']}",
        f"当前状态: {task['status']} 进度={task.get('progress', 0)}%",
    ]
    if task.get("output_dir"):
        lines.append(f"输出目录: {task['output_dir']}")
    if task.get("result_id"):
        lines.append(f"结果ID: {task['result_id']}")
    if task.get("error_message"):
        lines.append(f"错误信息: {task['error_message']}")
    if task.get("completed_at"):
        lines.append(f"[{task['completed_at']}] 执行结束")
    return lines


def _default_universe_rules() -> List[Dict[str, Any]]:
    manual_count = 0
    if _UNIVERSE_CONFIG.exists():
        try:
            config = yaml.safe_load(_UNIVERSE_CONFIG.read_text(encoding="utf-8")) or {}
            manual_count = len(config.get("items") or [])
        except Exception:
            manual_count = 0
    return [
        {
            "name": "ST排除",
            "type": "STExclusionRule",
            "enabled": True,
            "description": "自动剔除 ST/*ST/退市风险警示标的。",
            "params": {"source": "market_rules"},
        },
        {
            "name": "退市剔除",
            "type": "DelistedExclusionRule",
            "enabled": True,
            "description": "自动剔除已退市或进入退市整理期标的。",
            "params": {"source": "market_rules"},
        },
        {
            "name": "停牌剔除",
            "type": "SuspendedExclusionRule",
            "enabled": True,
            "description": "连续停牌达到阈值时自动剔除。",
            "params": {"max_suspend_days": 10},
        },
        {
            "name": "手动黑名单",
            "type": "ManualExclusionRule",
            "enabled": True,
            "description": "读取 configs/universe/exclude_list.yml 中的人工排除条目。",
            "params": {"config_path": str(_UNIVERSE_CONFIG), "item_count": manual_count},
        },
        {
            "name": "强赎剔除",
            "type": "ForceRedeemExclusionRule",
            "enabled": True,
            "description": "可转债已公告强赎或距到期 ≤30 天时自动剔除。",
            "params": {"days_to_maturity": 30},
        },
    ]


def _load_universe_rules(store) -> List[Dict[str, Any]]:
    base = {row["name"]: row for row in _default_universe_rules()}
    overrides = store.get_system_setting("universe_rules", {})
    if isinstance(overrides, dict):
        for name, values in overrides.items():
            if name in base and isinstance(values, dict):
                base[name].update(values)
    return list(base.values())


def _env_category(key: str) -> str:
    upper = key.upper()
    if "TOKEN" in upper or "PASSWORD" in upper or "WEBHOOK" in upper:
        return "secret"
    if upper.startswith("CB_") or "COMMISSION" in upper or "SLIPPAGE" in upper or "TAX" in upper:
        return "trading"
    if upper.startswith("AIPHA_") or upper.endswith("_PORT"):
        return "runtime"
    return "general"


def _is_secret_env(key: str) -> bool:
    upper = key.upper()
    return any(flag in upper for flag in ["TOKEN", "PASSWORD", "WEBHOOK", "SMTP_USER", "ALERT_EMAIL"])


def _load_env_vars(store) -> List[Dict[str, Any]]:
    env_overrides = store.get_system_setting("env_overrides", {})
    if not isinstance(env_overrides, dict):
        env_overrides = {}

    rows: List[Dict[str, Any]] = []
    comments: List[str] = []
    if _ENV_EXAMPLE.exists():
        for raw_line in _ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                comments.clear()
                continue
            if line.startswith("#"):
                comments.append(line.lstrip("# "))
                continue
            if "=" not in line:
                comments.clear()
                continue
            key, default_value = line.split("=", 1)
            current_value = env_overrides.get(key, os.environ.get(key, default_value))
            is_secret = _is_secret_env(key)
            rows.append({
                "key": key,
                "value": "****" if is_secret and current_value else current_value,
                "description": comments[-1] if comments else "来自 .env.example",
                "is_secret": is_secret,
                "category": _env_category(key),
            })
            comments.clear()
    return rows


def _build_audit_rows(store) -> List[Dict[str, Any]]:
    persisted = store.get_system_setting("audit_entries", None)
    if isinstance(persisted, list) and persisted:
        return _sort_by_timestamp(persisted, "timestamp")

    rows: List[Dict[str, Any]] = []
    for idx, task in enumerate(_build_task_rows(store), start=1):
        rows.append({
            "id": idx,
            "timestamp": task.get("created_at"),
            "user": task.get("created_by") or "system",
            "action": "run",
            "resource_type": task.get("type", "system"),
            "resource_id": task.get("task_id", ""),
            "details": task.get("description", ""),
            "ip_address": "127.0.0.1",
        })

    for user in store.list_users(active_only=False):
        if user.get("last_login"):
            rows.append({
                "id": len(rows) + 1,
                "timestamp": user.get("last_login"),
                "user": user.get("username"),
                "action": "login",
                "resource_type": "system",
                "resource_id": f"session_{user.get('username')}",
                "details": f"用户 {user.get('username')} 最近一次登录",
                "ip_address": "127.0.0.1",
            })
    ordered = _sort_by_timestamp(rows, "timestamp")
    for idx, row in enumerate(ordered, start=1):
        row["id"] = idx
    return ordered


@router.get("/users", response_model=PaginatedResponse[Dict[str, Any]])
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    admin: UserInfo = Depends(require_role("admin")),
):
    """List all users (admin only)."""
    store = get_result_store()
    rows = store.list_users(active_only=False)
    # Strip password hashes
    clean = []
    for r in rows:
        d = dict(r)
        d.pop("password_hash", None)
        clean.append(d)
    total = len(clean)
    start = (page - 1) * page_size
    return PaginatedResponse(
        data=clean[start : start + page_size],
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
    )


@router.post("/users", response_model=ApiResponse)
async def create_user(
    body: Dict[str, Any],
    admin: UserInfo = Depends(require_role("admin")),
):
    """Create a new user."""
    username = body.get("username", "")
    password = body.get("password", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    store = get_result_store()
    pwd_hash = hashlib.sha256(password.encode()).hexdigest()
    store.upsert_user({
        "username": username,
        "name": body.get("name", username),
        "password_hash": pwd_hash,
        "role": body.get("role", "viewer"),
        "email": body.get("email", ""),
        "created_by": admin.username,
    })
    return ApiResponse(message=f"User '{username}' created")


@router.put("/users/{username}", response_model=ApiResponse)
async def update_user(
    username: str,
    body: Dict[str, Any],
    admin: UserInfo = Depends(require_role("admin")),
):
    """Update user role / status."""
    store = get_result_store()
    existing = store.get_user(username)
    if existing is None:
        raise HTTPException(status_code=404, detail="User not found")
    updated = dict(existing)
    if "name" in body:
        updated["name"] = body["name"]
    if "role" in body:
        updated["role"] = body["role"]
    if "is_active" in body:
        updated["is_active"] = bool(body["is_active"])
    updated["created_by"] = admin.username
    store.upsert_user(updated)
    return ApiResponse(message="User updated")


@router.delete("/users/{username}", response_model=ApiResponse)
async def delete_user(
    username: str,
    admin: UserInfo = Depends(require_role("admin")),
):
    """Soft delete a user."""
    store = get_result_store()
    try:
        deleted = store.soft_delete_user(username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="User not found")
    return ApiResponse(message="User deleted")


@router.get("/config/permission-matrix", response_model=ApiResponse[List[Dict[str, str]]])
async def get_permission_matrix(
    admin: UserInfo = Depends(require_role("admin")),
):
    """Get persisted permission matrix."""
    store = get_result_store()
    return ApiResponse(data=_load_permission_matrix(store))


@router.put("/config/permission-matrix", response_model=ApiResponse)
async def update_permission_matrix(
    body: Dict[str, Any],
    admin: UserInfo = Depends(require_role("admin")),
):
    """Persist permission matrix."""
    matrix = body.get("matrix")
    if not isinstance(matrix, list) or not matrix:
        raise HTTPException(status_code=400, detail="matrix must be a non-empty list")
    store = get_result_store()
    store.set_system_setting("permission_matrix", matrix, updated_by=admin.username)
    _append_audit_entry(store, user=admin.username, action="update", resource_type="system", resource_id="permission_matrix", details="更新模块权限矩阵")
    return ApiResponse(message="Permission matrix updated")


@router.get("/config/market-rules", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_market_rules(
    _user: UserInfo = Depends(get_current_user),
):
    """Get current market rules configuration."""
    store = get_result_store()
    return ApiResponse(data=_load_market_rules(store))


@router.put("/config/market-rules/{asset_type}", response_model=ApiResponse)
async def update_market_rules(
    asset_type: str,
    body: Dict[str, Any],
    admin: UserInfo = Depends(require_role("admin")),
):
    """Persist market rules overrides for an asset type."""
    store = get_result_store()
    current = {item["asset_type"]: item for item in _load_market_rules(store)}
    if asset_type not in current:
        raise HTTPException(status_code=404, detail="Asset type not found")
    allowed = {
        "commission_rate", "commission_min", "stamp_tax_rate", "transfer_fee_rate",
        "slippage_bps", "lot_size", "t_plus_n", "price_limit_pct",
    }
    overrides = store.get_system_setting("market_rules_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    asset_overrides = overrides.get(asset_type, {}) if isinstance(overrides.get(asset_type), dict) else {}
    for key, value in body.items():
        if key in allowed and value is not None:
            asset_overrides[key] = value
    overrides[asset_type] = asset_overrides
    store.set_system_setting("market_rules_overrides", overrides, updated_by=admin.username)
    _append_audit_entry(store, user=admin.username, action="update", resource_type="system", resource_id=f"market_rules:{asset_type}", details=f"更新 {asset_type} 市场规则")
    return ApiResponse(message=f"Market rules for {asset_type} updated")


@router.get("/config/universe", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_universe_rules(
    _user: UserInfo = Depends(get_current_user),
):
    """Get universe filter rules."""
    store = get_result_store()
    return ApiResponse(data=_load_universe_rules(store))


@router.put("/config/universe/{name}", response_model=ApiResponse)
async def update_universe_rule(
    name: str,
    body: Dict[str, Any],
    admin: UserInfo = Depends(require_role("admin")),
):
    """Persist Universe rule state."""
    store = get_result_store()
    rules = {item["name"]: item for item in _load_universe_rules(store)}
    if name not in rules:
        raise HTTPException(status_code=404, detail="Universe rule not found")
    overrides = store.get_system_setting("universe_rules", {})
    if not isinstance(overrides, dict):
        overrides = {}
    current = overrides.get(name, {}) if isinstance(overrides.get(name), dict) else {}
    if "enabled" in body:
        current["enabled"] = bool(body["enabled"])
    overrides[name] = current
    store.set_system_setting("universe_rules", overrides, updated_by=admin.username)
    _append_audit_entry(store, user=admin.username, action="update", resource_type="system", resource_id=f"universe:{name}", details=f"更新 Universe 规则 {name}")
    return ApiResponse(message=f"Universe rule {name} updated")


@router.get("/config/env", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_env_vars(
    admin: UserInfo = Depends(require_role("admin")),
):
    """Get environment variables from .env.example plus persisted overrides."""
    store = get_result_store()
    return ApiResponse(data=_load_env_vars(store))


@router.put("/config/env/{key}", response_model=ApiResponse)
async def update_env_var(
    key: str,
    body: Dict[str, Any],
    admin: UserInfo = Depends(require_role("admin")),
):
    """Persist editable environment variable override."""
    if "value" not in body:
        raise HTTPException(status_code=400, detail="value required")
    store = get_result_store()
    overrides = store.get_system_setting("env_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
    overrides[key] = str(body.get("value", ""))
    store.set_system_setting("env_overrides", overrides, updated_by=admin.username)
    _append_audit_entry(store, user=admin.username, action="update", resource_type="system", resource_id=f"env:{key}", details=f"更新环境变量 {key}")
    return ApiResponse(message=f"Environment variable {key} updated")


@router.get("/tasks", response_model=PaginatedResponse[Dict[str, Any]])
async def list_tasks(
    status: Optional[str] = Query(None),
    task_type: Optional[str] = Query(None, alias="type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: UserInfo = Depends(get_current_user),
):
    """List async tasks."""
    store = get_result_store()
    rows = _build_task_rows(store)
    if status:
        rows = [row for row in rows if row.get("status") == status]
    if task_type:
        rows = [row for row in rows if row.get("type") == task_type]
    total = len(rows)
    start = (page - 1) * page_size
    return PaginatedResponse(
        data=rows[start : start + page_size],
        pagination=PaginationMeta(page=page, page_size=page_size, total=total, total_pages=(total + page_size - 1) // page_size),
    )


@router.delete("/tasks/{task_id}", response_model=ApiResponse)
async def cancel_task(
    task_id: str,
    user: UserInfo = Depends(require_role("ops")),
):
    """Cancel an async task."""
    store = get_result_store()
    rows = _build_task_rows(store)
    for row in rows:
        if row.get("task_id") == task_id:
            row["status"] = "cancelled"
            row["progress"] = row.get("progress", 0)
            row["completed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _save_task_rows(store, rows, user.username)
            _append_audit_entry(store, user=user.username, action="update", resource_type=row.get("type", "system"), resource_id=task_id, details=f"取消任务 {task_id}")
            return ApiResponse(message=f"Task {task_id} cancelled")
    raise HTTPException(status_code=404, detail="Task not found")


@router.post("/tasks/{task_id}/retry", response_model=ApiResponse[Dict[str, Any]])
async def retry_task(
    task_id: str,
    user: UserInfo = Depends(require_role("ops")),
):
    """Retry a failed async task."""
    store = get_result_store()
    rows = _build_task_rows(store)
    for row in rows:
        if row.get("task_id") == task_id:
            row["status"] = "pending"
            row["progress"] = 0
            row["error_message"] = None
            row["created_by"] = user.username
            row["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row["started_at"] = None
            row["completed_at"] = None
            _save_task_rows(store, rows, user.username)
            _append_audit_entry(store, user=user.username, action="run", resource_type=row.get("type", "system"), resource_id=task_id, details=f"重试任务 {task_id}")
            return ApiResponse(data=row, message=f"Task {task_id} retried")
    raise HTTPException(status_code=404, detail="Task not found")


@router.get("/tasks/{task_id}/log", response_model=ApiResponse[Dict[str, Any]])
async def get_task_log(
    task_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get task log lines."""
    store = get_result_store()
    rows = _build_task_rows(store)
    for row in rows:
        if row.get("task_id") == task_id:
            return ApiResponse(data={"lines": _task_log_lines(row)})
    raise HTTPException(status_code=404, detail="Task not found")


@router.get("/audit", response_model=PaginatedResponse[Dict[str, Any]])
async def get_audit_log(
    user_filter: Optional[str] = Query(None, alias="user"),
    action: Optional[str] = Query(None),
    resource_id: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _user: UserInfo = Depends(get_current_user),
):
    """Get audit log entries."""
    rows = _build_audit_rows(get_result_store())
    if user_filter:
        rows = [row for row in rows if row.get("user") == user_filter]
    if action:
        rows = [row for row in rows if row.get("action") == action]
    if resource_id:
        rows = [row for row in rows if row.get("resource_id") == resource_id]
    if start_date:
        rows = [row for row in rows if str(row.get("timestamp", ""))[:10] >= start_date]
    if end_date:
        rows = [row for row in rows if str(row.get("timestamp", ""))[:10] <= end_date]
    total = len(rows)
    start = (page - 1) * page_size
    return PaginatedResponse(
        data=rows[start : start + page_size],
        pagination=PaginationMeta(page=page, page_size=page_size, total=total, total_pages=(total + page_size - 1) // page_size),
    )
