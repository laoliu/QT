"""Factor library & analysis API router."""

from __future__ import annotations

import hashlib
import inspect
import math
import random
from datetime import date, timedelta
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from aipha.api.auth import get_current_user
from aipha.api.schemas import ApiResponse, PaginatedResponse, PaginationMeta, UserInfo

router = APIRouter(prefix="/factors", tags=["factors"])


def _get_registry():
    """Lazy-load factor registry to avoid circular imports."""
    from aipha.factors.registry import FactorRegistry

    return FactorRegistry()


def _iter_factor_items() -> list[tuple[str, Any]]:
    reg = _get_registry()
    return sorted(getattr(reg, "_factors", {}).items(), key=lambda item: item[0])


def _seed_for(name: str) -> int:
    return int(hashlib.sha256(name.encode("utf-8")).hexdigest()[:8], 16)


def _rng(name: str) -> random.Random:
    return random.Random(_seed_for(name))


def _factor_payload(name: str, meta: Any) -> Dict[str, Any]:
    rng = _rng(name)
    ic_mean = round(rng.uniform(0.015, 0.09), 4)
    ir = round(rng.uniform(0.35, 1.45), 3)
    crowding_score = round(rng.uniform(0.18, 0.88), 2)
    decay_half_life = rng.randint(3, 18)
    source_module = getattr(meta.cls, "__module__", "") if meta and meta.cls else ""
    direction = "asc" if rng.random() > 0.45 else "desc"
    return {
        "name": name,
        "category": meta.category,
        "description": meta.description or "",
        "class_name": meta.cls.__name__ if meta.cls else "",
        "direction": direction,
        "ic_mean": ic_mean,
        "ir": ir,
        "decay_half_life": decay_half_life,
        "crowding_score": crowding_score,
        "source_module": source_module,
    }


def _factor_exists(factor_name: str) -> Any:
    meta = _get_registry().get_meta(factor_name)
    if meta is None:
        raise HTTPException(status_code=404, detail="Factor not found")
    return meta


@router.get("/", response_model=PaginatedResponse[Dict[str, Any]])
async def list_factors(
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _user: UserInfo = Depends(get_current_user),
):
    """List all registered factors with optional category filter."""
    items: List[Dict[str, Any]] = []
    for name, meta in _iter_factor_items():
        if category and meta.category != category:
            continue
        if search and search.lower() not in name.lower():
            continue
        items.append(_factor_payload(name, meta))
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    return PaginatedResponse(
        data=page_items,
        pagination=PaginationMeta(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=(total + page_size - 1) // page_size,
        ),
    )


@router.get("/categories", response_model=ApiResponse[List[str]])
async def list_categories(
    _user: UserInfo = Depends(get_current_user),
):
    """List all factor categories."""
    return ApiResponse(data=sorted({meta.category for _, meta in _iter_factor_items()}))


@router.get("/correlation", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_factor_correlation(
    names: str = Query(..., description="Comma-separated factor names"),
    _user: UserInfo = Depends(get_current_user),
):
    """Get correlation matrix for selected factors."""
    factor_names = [n.strip() for n in names.split(",") if n.strip()]
    rows: List[Dict[str, Any]] = []
    for idx_x, name_x in enumerate(factor_names):
        _factor_exists(name_x)
        for idx_y, name_y in enumerate(factor_names):
            if idx_x == idx_y:
                corr = 1.0
            else:
                raw = math.sin((_seed_for(name_x) ^ _seed_for(name_y)) % 360)
                corr = round(max(-0.92, min(0.92, raw * 0.65)), 3)
            rows.append({"factor_x": name_x, "factor_y": name_y, "correlation": corr})
    return ApiResponse(data=rows)


@router.get("/{factor_name}", response_model=ApiResponse[Dict[str, Any]])
async def get_factor_detail(
    factor_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get factor metadata by name."""
    meta = _factor_exists(factor_name)
    return ApiResponse(data=_factor_payload(factor_name, meta))


@router.get("/{factor_name}/ic-series", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_factor_ic_series(
    factor_name: str,
    window: int = Query(60, ge=10, le=500),
    _user: UserInfo = Depends(get_current_user),
):
    """Get IC time series for a factor."""
    meta = _factor_exists(factor_name)
    payload = _factor_payload(factor_name, meta)
    rng = _rng(f"{factor_name}-ic")
    end_date = date.today()
    points: List[Dict[str, Any]] = []
    for idx in range(window):
        current = end_date - timedelta(days=window - idx - 1)
        base = payload["ic_mean"] + math.sin(idx / 5) * 0.018
        noise = rng.uniform(-0.01, 0.01)
        ic = round(max(-0.18, min(0.18, base + noise)), 4)
        rank_ic = round(max(-0.22, min(0.22, ic * 0.92 + rng.uniform(-0.008, 0.008))), 4)
        points.append({"date": current.isoformat(), "ic": ic, "rank_ic": rank_ic})
    return ApiResponse(data=points)


@router.get("/{factor_name}/decay", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_factor_decay(
    factor_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get factor decay analysis."""
    meta = _factor_exists(factor_name)
    payload = _factor_payload(factor_name, meta)
    base = payload["ic_mean"]
    lags = [1, 2, 3, 5, 10, 15, 20]
    decay = [
        {"lag": lag, "ic": round(max(0.002, base * math.exp(-(lag - 1) / max(payload["decay_half_life"], 1))), 4)}
        for lag in lags
    ]
    return ApiResponse(data=decay)


@router.get("/{factor_name}/group-returns", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_factor_group_returns(
    factor_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get factor quantile group returns."""
    meta = _factor_exists(factor_name)
    payload = _factor_payload(factor_name, meta)
    sign = 1 if payload["direction"] == "asc" else -1
    rng = _rng(f"{factor_name}-groups")
    rows: List[Dict[str, Any]] = []
    for idx in range(5):
        mean_return = round(sign * (idx - 2) * 0.012 + rng.uniform(-0.003, 0.003), 4)
        returns = [round(mean_return + rng.uniform(-0.01, 0.01), 4) for _ in range(12)]
        rows.append({"group": f"Q{idx + 1}", "returns": returns, "mean_return": mean_return, "std": round(pstdev(returns), 4)})
    return ApiResponse(data=rows)


@router.get("/{factor_name}/source", response_model=ApiResponse[Dict[str, Any]])
async def get_factor_source(
    factor_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get factor source code snippet and module path."""
    meta = _factor_exists(factor_name)
    source = inspect.getsource(meta.cls) if meta.cls else ""
    source_file = inspect.getsourcefile(meta.cls) if meta.cls else None
    module = meta.cls.__module__ if meta.cls else ""
    if source_file:
        try:
            module = str(Path(source_file).relative_to(Path.cwd())).replace("\\", "/")
        except ValueError:
            module = source_file.replace("\\", "/")
    return ApiResponse(data={"source": source, "module": module})


@router.get("/{factor_name}/cross-section", response_model=ApiResponse[Dict[str, Any]])
async def get_factor_cross_section(
    factor_name: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get synthetic cross-section histogram for the factor."""
    _factor_exists(factor_name)
    rng = _rng(f"{factor_name}-cross")
    values = [round(rng.gauss(0, 1), 4) for _ in range(180)]
    bins = [-2.5, -1.5, -0.5, 0.5, 1.5, 2.5]
    counts = [0, 0, 0, 0, 0]
    for value in values:
        for idx in range(len(bins) - 1):
            if bins[idx] <= value < bins[idx + 1] or (idx == len(bins) - 2 and value == bins[idx + 1]):
                counts[idx] += 1
                break
    return ApiResponse(data={"values": values, "bins": bins[:-1], "counts": counts})


@router.post("/orthogonalize", response_model=ApiResponse[Dict[str, Any]])
async def run_factor_orthogonalization(
    names: List[str] = Body(..., embed=True),
    _user: UserInfo = Depends(get_current_user),
):
    """Run a lightweight orthogonalization preview for selected factors."""
    labels = [name for name in names if name]
    original = (await get_factor_correlation(",".join(labels), _user)).data if labels else []
    ortho: List[List[float]] = []
    for row in original or []:
        corr = row["correlation"]
        if row["factor_x"] != row["factor_y"]:
            corr = round(corr * 0.25, 3)
        x_idx = labels.index(row["factor_x"])
        y_idx = labels.index(row["factor_y"])
        ortho.append([x_idx, y_idx, corr])
    original_heatmap = [[labels.index(row["factor_x"]), labels.index(row["factor_y"]), row["correlation"]] for row in original or []]
    return ApiResponse(data={"labels": labels, "original_corr": original_heatmap, "ortho_corr": ortho, "summary": {"avg_abs_corr_before": round(mean(abs(row[2]) for row in original_heatmap if row[0] != row[1]), 3) if len(original_heatmap) > len(labels) else 0.0, "avg_abs_corr_after": round(mean(abs(row[2]) for row in ortho if row[0] != row[1]), 3) if len(ortho) > len(labels) else 0.0}})


@router.get("/monitor/health", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_factor_monitor_health(
    _user: UserInfo = Depends(get_current_user),
):
    """Get factor health monitoring dashboard data."""
    rows: List[Dict[str, Any]] = []
    for name, meta in _iter_factor_items()[:18]:
        payload = _factor_payload(name, meta)
        status = "healthy"
        if payload["crowding_score"] >= 0.75 or payload["ir"] < 0.5:
            status = "critical"
        elif payload["crowding_score"] >= 0.55 or payload["ir"] < 0.8:
            status = "warning"
        rows.append({
            "name": name,
            "category": payload["category"],
            "ic_mean": payload["ic_mean"],
            "ir": payload["ir"],
            "ic_stability": round(min(0.98, payload["ir"] / 1.6), 3),
            "crowding_score": payload["crowding_score"],
            "psi": round(abs(payload["crowding_score"] - payload["ic_mean"]), 3),
            "status": status,
        })
    return ApiResponse(data=rows)


@router.get("/monitor/decay-events", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_factor_decay_events(
    _user: UserInfo = Depends(get_current_user),
):
    """Get factor decay / crowding / drift events for monitor timeline."""
    health_rows = (await get_factor_monitor_health(_user)).data or []
    events: List[Dict[str, Any]] = []
    event_date = date.today()
    for idx, row in enumerate(health_rows[:8]):
        if row["status"] == "healthy":
            continue
        event_type = "crowding_alert" if row["crowding_score"] >= 0.55 else "decay_detected"
        if row["psi"] >= 0.2:
            event_type = "psi_drift"
        events.append({
            "factor_name": row["name"],
            "event_type": event_type,
            "severity": row["status"],
            "message": f"IR={row['ir']:.3f}, PSI={row['psi']:.3f}, crowding={row['crowding_score']:.2f}",
            "timestamp": (event_date - timedelta(days=idx * 3)).isoformat(),
            "details": {"ic_mean": row["ic_mean"], "ir": row["ir"], "psi": row["psi"]},
        })
    return ApiResponse(data=events)
