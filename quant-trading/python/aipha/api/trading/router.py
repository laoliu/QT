"""Paper trading management API router."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from aipha.api.auth import get_current_user, require_role
from aipha.api.deps import get_result_store
from aipha.api.schemas import ApiResponse, PaginatedResponse, PaginationMeta, UserInfo
from aipha.api.trading.paper_state_reader import build_real_snapshot

router = APIRouter(prefix="/trading", tags=["trading"])


def _build_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    """Build portfolio snapshot from real paper trading state files."""
    return build_real_snapshot(row)


def _normalize_status(status: str | None) -> str:
    mapping = {
        "registered": "stopped",
        "idle": "stopped",
        "ready": "stopped",
        "running": "running",
        "paused": "paused",
        "stopped": "stopped",
        "archived": "archived",
    }
    return mapping.get(status or "registered", "stopped")


def _normalize_paper_row(row: Dict[str, Any], snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    strategies = row.get("strategies") or []
    weights = row.get("weights") or {}
    source_configs = row.get("source_configs") or []
    source_combo_id = next(
        (item.get("combo_id") for item in source_configs
         if isinstance(item, dict) and item.get("type") == "combo"),
        None,
    )
    if len(strategies) == 1:
        strategy_label = strategies[0]
        source_type = "strategy"
    elif source_combo_id:
        strategy_label = f"组合:{source_combo_id}"
        source_type = "combo"
    else:
        strategy_label = f"{len(strategies)}策略组合"
        source_type = "custom"

    status = _normalize_status(row.get("paper_status"))
    started_at = row.get("created_at") if status in {"running", "paused"} else ""
    summary = (snapshot or {}).get("summary", {})
    category = row.get("paper_category", "research")
    return {
        "id": row.get("portfolio_id"),
        "name": row.get("display_name") or row.get("portfolio_id"),
        "strategy_name": strategy_label,
        "strategy_names": strategies,
        "weights": weights,
        "source_type": source_type,
        "source_combo_id": source_combo_id,
        "status": status,
        "category": category,
        "initial_capital": row.get("initial_capital", 1_000_000),
        "current_nav": summary.get("current_nav", 1.0),
        "pnl": summary.get("pnl", 0.0),
        "pnl_pct": summary.get("pnl_pct", 0.0),
        "started_at": started_at,
        "total_trades": summary.get("total_trades", 0),
        "positions_count": summary.get("positions_count", 0),
        "paper_mode": row.get("paper_mode", "single_strategy" if len(strategies) <= 1 else "multi_strategy"),
        "created_at": row.get("created_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    }


def _get_paper_row_or_404(store, portfolio_id: str) -> Dict[str, Any]:
    row = store.get_paper_portfolio(portfolio_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Paper portfolio not found")
    return dict(row)


def _extract_strategy_config(store, body: Dict[str, Any]) -> tuple[list[str], dict[str, float], list[dict[str, Any]], str]:
    combo_id = body.get("combo_id")
    if combo_id:
        combo = store.get_paper_portfolio(combo_id)
        if combo is None:
            raise HTTPException(status_code=404, detail="Combo not found")
        strategies = combo.get("strategies") or []
        weights = combo.get("weights") or {name: 1 / max(len(strategies), 1) for name in strategies}
        source_configs = [{"type": "combo", "combo_id": combo_id}]
        return strategies, weights, source_configs, combo.get("display_name", combo_id)

    strategy_defs = body.get("strategies") or []
    if strategy_defs:
        parsed = []
        for item in strategy_defs:
            if not isinstance(item, dict):
                continue
            name = item.get("strategy_name") or item.get("name")
            weight = float(item.get("weight", 0))
            if name:
                parsed.append((name, weight))
        total = sum(weight for _, weight in parsed)
        if not parsed or total <= 0:
            raise HTTPException(status_code=400, detail="Invalid strategies payload")
        weights = {name: weight / total for name, weight in parsed}
        source_configs = [
            {"type": "strategy", "strategy_name": name, "weight": weights[name]}
            for name, _weight in parsed
        ]
        return [name for name, _weight in parsed], weights, source_configs, body.get("name", "")

    strategy_name = body.get("strategy_name")
    if not strategy_name:
        raise HTTPException(status_code=400, detail="strategy_name, combo_id or strategies is required")
    return [strategy_name], {strategy_name: 1.0}, [{"type": "strategy", "strategy_name": strategy_name, "weight": 1.0}], body.get("name", "")


@router.get("/paper", response_model=PaginatedResponse[Dict[str, Any]])
async def list_paper_portfolios(
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: UserInfo = Depends(get_current_user),
):
    """List paper trading portfolios."""
    store = get_result_store()
    rows = store.list_paper_portfolios()
    if status:
        rows = [r for r in rows if _normalize_status(r.get("paper_status")) == status]
    rows = [_normalize_paper_row(r, _build_snapshot(dict(r))) for r in rows]
    total = len(rows)
    start = (page - 1) * page_size
    page_items = rows[start : start + page_size]
    return PaginatedResponse(
        data=page_items,
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
    )


@router.get("/paper/{portfolio_id}", response_model=ApiResponse[Dict[str, Any]])
async def get_paper_detail(
    portfolio_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get paper trading portfolio detail."""
    store = get_result_store()
    row = _get_paper_row_or_404(store, portfolio_id)
    snapshot = _build_snapshot(row)
    return ApiResponse(data=_normalize_paper_row(row, snapshot))


@router.put("/paper/{portfolio_id}/action", response_model=ApiResponse)
async def paper_action(
    portfolio_id: str,
    body: Dict[str, Any],
    user: UserInfo = Depends(require_role("ops")),
):
    """Start/pause/reset a paper portfolio."""
    action = body.get("action", "")
    if action not in ("start", "pause", "stop", "reset", "archive"):
        raise HTTPException(status_code=400, detail="Invalid action")
    store = get_result_store()
    status_map = {
        "start": "running",
        "pause": "paused",
        "stop": "stopped",
        "reset": "registered",
        "archive": "archived",
    }
    updated = store.update_paper_portfolio_status(portfolio_id, status_map[action])
    if not updated:
        raise HTTPException(status_code=404, detail="Paper portfolio not found")
    return ApiResponse(message=f"Paper portfolio {action}ed")


@router.post("/paper", response_model=ApiResponse[Dict[str, Any]])
async def create_paper_portfolio(
    body: Dict[str, Any],
    user: UserInfo = Depends(require_role("researcher")),
):
    """Create a new paper trading portfolio."""
    import uuid

    portfolio_id = f"paper_{uuid.uuid4().hex[:8]}"
    store = get_result_store()
    strategies, weights, source_configs, fallback_name = _extract_strategy_config(store, body)
    store.upsert_paper_portfolio({
        "portfolio_id": portfolio_id,
        "display_name": body.get("name", fallback_name or portfolio_id),
        "config_path": "",
        "paper_mode": "single_strategy" if len(strategies) == 1 else "multi_strategy",
        "paper_status": "registered",
        "strategies": strategies,
        "weights": weights,
        "source_configs": source_configs,
        "initial_capital": body.get("initial_capital", 1_000_000),
        "created_by": user.username,
    })
    created = store.get_paper_portfolio(portfolio_id)
    return ApiResponse(data=_normalize_paper_row(created or {"portfolio_id": portfolio_id, "display_name": body.get("name", portfolio_id)}))


@router.post("/paper/{portfolio_id}/reset", response_model=ApiResponse)
async def reset_paper_portfolio(
    portfolio_id: str,
    user: UserInfo = Depends(require_role("ops")),
):
    """Reset a paper trading portfolio to registered status."""
    store = get_result_store()
    updated = store.update_paper_portfolio_status(portfolio_id, "registered")
    if not updated:
        raise HTTPException(status_code=404, detail="Paper portfolio not found")
    return ApiResponse(message="Paper portfolio reset")


@router.get("/paper/{portfolio_id}/positions", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_paper_positions(
    portfolio_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get current positions."""
    store = get_result_store()
    row = _get_paper_row_or_404(store, portfolio_id)
    return ApiResponse(data=_build_snapshot(row)["positions"])


@router.get("/paper/{portfolio_id}/orders", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_paper_orders(
    portfolio_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get recent orders."""
    store = get_result_store()
    row = _get_paper_row_or_404(store, portfolio_id)
    return ApiResponse(data=_build_snapshot(row)["orders"])


@router.get("/paper/{portfolio_id}/pnl", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_paper_pnl(
    portfolio_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get PnL time series."""
    store = get_result_store()
    row = _get_paper_row_or_404(store, portfolio_id)
    return ApiResponse(data=_build_snapshot(row)["pnl"])
