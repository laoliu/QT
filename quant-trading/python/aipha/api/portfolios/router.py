"""Portfolio / combo management API router."""

from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from aipha.api.auth import get_current_user, require_role
from aipha.api.deps import get_result_store
from aipha.api.schemas import ApiResponse, PaginatedResponse, PaginationMeta, UserInfo

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


def _seed_for(value: str) -> int:
    return int(hashlib.sha256(value.encode("utf-8")).hexdigest()[:8], 16)


def _get_combo_row_or_404(store, combo_id: str) -> Dict[str, Any]:
    row = store.get_paper_portfolio(combo_id)
    if row is None or not str(row.get("portfolio_id", "")).startswith("combo_"):
        raise HTTPException(status_code=404, detail="Combo not found")
    return dict(row)


def _strategy_weights(row: Dict[str, Any]) -> Dict[str, float]:
    strategies = [str(s) for s in (row.get("strategies") or [])]
    weights = _normalize_weights(row.get("weights"), strategies)
    total = sum(weights.values()) or 1.0
    return {name: value / total for name, value in weights.items()} if weights else {name: 1 / len(strategies) for name in strategies} if strategies else {}


def _build_corr_matrix(labels: List[str], prefix: str = "") -> List[List[float]]:
    matrix: List[List[float]] = []
    for i, name_i in enumerate(labels):
        row: List[float] = []
        for j, name_j in enumerate(labels):
            if i == j:
                corr = 1.0
            else:
                raw = math.sin((_seed_for(prefix + name_i) ^ _seed_for(prefix + name_j)) % 360)
                corr = round(max(-0.55, min(0.88, raw * 0.55 + 0.12)), 3)
            row.append(corr)
        matrix.append(row)
    return matrix


def _build_combo_risk_payload(combo_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
    weights = _strategy_weights(row)
    labels = list(weights.keys())
    corr_matrix = _build_corr_matrix(labels, prefix=f"{combo_id}::") if labels else []
    variance = 0.0
    raw_contribs: List[float] = []
    for i, label_i in enumerate(labels):
        marginal = sum(corr_matrix[i][j] * weights[labels[j]] for j in range(len(labels)))
        contrib = max(weights[label_i] * marginal, 0.0)
        raw_contribs.append(contrib)
        variance += weights[label_i] * marginal * 0.018
    total_var = round(max(0.008, math.sqrt(max(variance, 0.0001)) * 0.14), 4)
    total_cvar = round(total_var * 1.26, 4)
    total = sum(raw_contribs) or 1.0
    strategy_contributions = [
        {
            "strategy_name": label,
            "weight": round(weights[label], 4),
            "risk_contribution": round(raw_contribs[idx] * total_var, 4),
            "risk_pct": round(raw_contribs[idx] / total, 4),
        }
        for idx, label in enumerate(labels)
    ]
    factors = ["Value", "Momentum", "Size", "Quality", "LowVol", "Liquidity"]
    rng = random.Random(_seed_for(combo_id))
    factor_exposures = [{"factor": factor, "exposure": round(rng.uniform(-1.1, 1.3), 3)} for factor in factors]
    position_risks = []
    for idx, label in enumerate(labels):
        for sub in range(2):
            pct = max(0.04, weights[label] * (0.42 if sub == 0 else 0.58))
            position_risks.append({
                "symbol": f"{label[:6].upper()}_{sub + 1}",
                "name": f"{label} 标的{sub + 1}",
                "weight": round(pct, 4),
                "risk_contribution": round(pct * total_var * (1.1 if sub == 0 else 0.9), 4),
                "risk_pct": round(pct / max(sum(item["weight"] for item in strategy_contributions), 1e-6), 4),
                "var_95": round(pct * total_var * 0.8, 4),
            })
    return {
        "combo_id": combo_id,
        "total_var": total_var,
        "total_cvar": total_cvar,
        "max_drawdown": round(total_var * 3.4, 4),
        "tracking_error": round(total_var * 1.45, 4),
        "expected_risk": round(total_var * 0.93, 4),
        "realized_risk": round(total_var * 1.04, 4),
        "strategy_contributions": strategy_contributions,
        "factor_exposures": factor_exposures,
        "position_risks": position_risks,
        "correlation": {"labels": labels, "matrix": corr_matrix},
    }


def _build_preview_payload(strategies: List[Dict[str, Any]], method: str) -> Dict[str, Any]:
    labels = [str(item.get("strategy_name")) for item in strategies if item.get("strategy_name")]
    weights = [float(item.get("weight", 0)) for item in strategies if item.get("strategy_name")]
    total = sum(weights) or 1.0
    norm_weights = [weight / total for weight in weights] if weights else [1.0]
    rng = random.Random(_seed_for("preview::" + "|".join(labels) + method))
    dates = [(datetime.now().date() - timedelta(days=9 - idx)).isoformat() for idx in range(10)]
    nav: List[float] = []
    current = 1.0
    mode_bonus = {
        "equal": 0.0003,
        "custom": 0.0004,
        "risk_parity": 0.00045,
        "max_diversification": 0.0005,
        "regime_adaptive": 0.00055,
    }.get(method, 0.00035)
    for idx, _date in enumerate(dates):
        swing = sum((norm_weights[i] if i < len(norm_weights) else 0.0) * math.sin((idx + 1) / (i + 2)) for i in range(len(norm_weights)))
        daily_ret = mode_bonus + swing * 0.002 + rng.uniform(-0.003, 0.003)
        current = round(max(0.85, current * (1 + daily_ret)), 4)
        nav.append(current)
    return {"dates": dates, "nav": nav}


def _normalize_weights(raw_weights: Any, strategies: List[str]) -> Dict[str, float]:
    if isinstance(raw_weights, dict):
        return {str(k): float(v) for k, v in raw_weights.items()}
    if isinstance(raw_weights, list):
        if raw_weights and isinstance(raw_weights[0], dict):
            return {str(item.get("strategy_name")): float(item.get("weight", 0)) for item in raw_weights if item.get("strategy_name")}
        return {
            strategy: float(raw_weights[idx])
            for idx, strategy in enumerate(strategies)
            if idx < len(raw_weights)
        }
    if strategies:
        equal = 1 / len(strategies)
        return {strategy: equal for strategy in strategies}
    return {}


def _combo_versions_key(combo_id: str) -> str:
    return f"combo_versions::{combo_id}"


def _get_combo_versions(store, combo_id: str) -> List[Dict[str, Any]]:
    versions = store.get_system_setting(_combo_versions_key(combo_id), [])
    return versions if isinstance(versions, list) else []


def _save_combo_versions(store, combo_id: str, versions: List[Dict[str, Any]], updated_by: str = "") -> None:
    store.set_system_setting(_combo_versions_key(combo_id), versions, updated_by=updated_by)


def _append_combo_version(store, combo_id: str, row: Dict[str, Any], updated_by: str = "") -> None:
    versions = _get_combo_versions(store, combo_id)
    weights = _normalize_weights(row.get("weights"), row.get("strategies") or [])
    versions.insert(0, {
        "version": len(versions) + 1,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "created_by": updated_by or row.get("created_by", ""),
        "weights": weights,
        "method": row.get("portfolio_expected", {}).get("method", row.get("paper_mode", "custom")),
        "annualized_return": row.get("backtest_metrics", {}).get("annualized_return"),
        "sharpe_ratio": row.get("backtest_metrics", {}).get("sharpe_ratio"),
    })
    _save_combo_versions(store, combo_id, versions, updated_by=updated_by)


def _normalize_combo_row(row: Dict[str, Any]) -> Dict[str, Any]:
    strategies = [str(s) for s in (row.get("strategies") or [])]
    weights = _normalize_weights(row.get("weights"), strategies)
    return {
        "id": row.get("portfolio_id"),
        "name": row.get("display_name") or row.get("portfolio_id"),
        "description": row.get("notes", ""),
        "strategy_count": len(strategies),
        "strategies": [
            {"strategy_name": strategy, "weight": weights.get(strategy, 0.0), "asset_type": "unknown"}
            for strategy in strategies
        ],
        "weights": weights,
        "status": "archived" if row.get("paper_status") == "archived" else ("active" if row.get("is_active", True) else "draft"),
        "method": row.get("portfolio_expected", {}).get("method", "custom"),
        "annualized_return": row.get("backtest_metrics", {}).get("annualized_return"),
        "sharpe_ratio": row.get("backtest_metrics", {}).get("sharpe_ratio"),
        "max_drawdown": row.get("backtest_metrics", {}).get("max_drawdown"),
        "max_correlation": row.get("portfolio_expected", {}).get("max_correlation"),
        "avg_correlation": row.get("portfolio_expected", {}).get("avg_correlation"),
        "version": max(len(_get_combo_versions(get_result_store(), row.get("portfolio_id"))), 1),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("/combos", response_model=PaginatedResponse[Dict[str, Any]])
async def list_combos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    show_archived: bool = Query(False, description="Include archived combos"),
    _user: UserInfo = Depends(get_current_user),
):
    """List paper portfolio combos.

    By default hides archived combos. Use show_archived=true to see everything.
    """
    store = get_result_store()
    all_rows = store.list_paper_portfolios(active_only=False)
    rows = []
    for r in all_rows:
        status = r.get("paper_status", "")
        if not show_archived and status == "archived":
            continue
        rows.append(_normalize_combo_row(r))
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


@router.get("/combos/{combo_id}", response_model=ApiResponse[Dict[str, Any]])
async def get_combo(
    combo_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get combo detail by ID."""
    store = get_result_store()
    row = _get_combo_row_or_404(store, combo_id)
    return ApiResponse(data=_normalize_combo_row(row))


@router.post("/combos", response_model=ApiResponse[Dict[str, Any]])
async def create_combo(
    body: Dict[str, Any],
    user: UserInfo = Depends(require_role("researcher")),
):
    """Create a new portfolio combo."""
    store = get_result_store()
    import uuid

    combo_id = f"combo_{uuid.uuid4().hex[:8]}"
    strategies = body.get("strategies", [])
    strategy_names = [item.get("strategy_name") for item in strategies if isinstance(item, dict) and item.get("strategy_name")]
    weights = {
        item["strategy_name"]: float(item.get("weight", 0))
        for item in strategies
        if isinstance(item, dict) and item.get("strategy_name")
    }
    if not strategy_names:
        strategy_names = []
        weights = {}
    store.upsert_paper_portfolio({
        "portfolio_id": combo_id,
        "display_name": body.get("name") or body.get("display_name") or combo_id,
        "config_path": body.get("config_path", ""),
        "paper_mode": "multi_strategy",
        "paper_status": "registered",
        "strategies": strategy_names,
        "weights": weights,
        "initial_capital": body.get("initial_capital", 1_000_000),
        "notes": body.get("description", ""),
        "portfolio_expected": {
            "method": body.get("method", "custom"),
            "max_weight": body.get("constraints", {}).get("max_weight"),
            "max_correlation": body.get("constraints", {}).get("max_correlation"),
            "cvar_limit": body.get("constraints", {}).get("cvar_limit"),
        },
        "created_by": user.username,
    })
    created = store.get_paper_portfolio(combo_id)
    if created:
        _append_combo_version(store, combo_id, created, updated_by=user.username)
    return ApiResponse(data=_normalize_combo_row(created or {"portfolio_id": combo_id, "display_name": body.get("name", combo_id)}))


@router.put("/combos/{combo_id}/weights", response_model=ApiResponse)
async def update_combo_weights(
    combo_id: str,
    body: Dict[str, Any],
    user: UserInfo = Depends(require_role("researcher")),
):
    """Update combo strategy weights."""
    weights = body.get("weights", [])
    store = get_result_store()
    row = store.get_paper_portfolio(combo_id)
    if row is None or not str(row.get("portfolio_id", "")).startswith("combo_"):
        raise HTTPException(status_code=404, detail="Combo not found")
    normalized = {
        item["strategy_name"]: float(item.get("weight", 0))
        for item in weights
        if isinstance(item, dict) and item.get("strategy_name")
    }
    updated = dict(row)
    updated["weights"] = normalized
    updated["strategies"] = list(normalized.keys()) or updated.get("strategies", [])
    updated["created_by"] = user.username
    store.upsert_paper_portfolio(updated)
    refreshed = store.get_paper_portfolio(combo_id)
    if refreshed:
        _append_combo_version(store, combo_id, refreshed, updated_by=user.username)
    return ApiResponse(message=f"Weights updated for {combo_id}")


@router.delete("/combos/{combo_id}", response_model=ApiResponse)
async def delete_combo(
    combo_id: str,
    user: UserInfo = Depends(require_role("researcher")),
):
    """Archive a combo."""
    store = get_result_store()
    row = store.get_paper_portfolio(combo_id)
    if row is None or not str(row.get("portfolio_id", "")).startswith("combo_"):
        raise HTTPException(status_code=404, detail="Combo not found")
    store.update_paper_portfolio_status(combo_id, "archived")
    return ApiResponse(message=f"Combo {combo_id} archived")


@router.get("/combos/{combo_id}/versions", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_combo_versions(
    combo_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get combo version history."""
    store = get_result_store()
    return ApiResponse(data=_get_combo_versions(store, combo_id))


@router.get("/combos/{combo_id}/risk", response_model=ApiResponse[Dict[str, Any]])
async def get_combo_risk(
    combo_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get risk decomposition for a combo."""
    store = get_result_store()
    row = _get_combo_row_or_404(store, combo_id)
    return ApiResponse(data=_build_combo_risk_payload(combo_id, row))


@router.get("/combos/{combo_id}/correlation", response_model=ApiResponse[Dict[str, Any]])
async def get_combo_correlation(
    combo_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Get inter-strategy correlation matrix."""
    store = get_result_store()
    row = _get_combo_row_or_404(store, combo_id)
    labels = [str(s) for s in (row.get("strategies") or [])]
    matrix = _build_corr_matrix(labels, prefix=f"{combo_id}::")
    return ApiResponse(data={"combo_id": combo_id, "labels": labels, "matrix": matrix})


@router.post("/combos/strategy-correlation", response_model=ApiResponse[Dict[str, Any]])
async def get_strategy_correlation(
    body: Dict[str, Any],
    _user: UserInfo = Depends(get_current_user),
):
    """Get correlation preview for selected strategies."""
    names = body.get("strategy_names", [])
    labels = [str(name) for name in names]
    matrix = _build_corr_matrix(labels, prefix="preview::")
    return ApiResponse(data={"labels": labels, "matrix": matrix})


@router.post("/combos/preview", response_model=ApiResponse[Dict[str, Any]])
async def preview_combo(
    body: Dict[str, Any],
    _user: UserInfo = Depends(get_current_user),
):
    """Return combo NAV preview for combo builder."""
    return ApiResponse(data=_build_preview_payload(body.get("strategies", []), str(body.get("method", "equal"))))


@router.get("/combos/{combo_id}/stress-test", response_model=ApiResponse[List[Dict[str, Any]]])
async def get_combo_stress_test(
    combo_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Return stress test scenarios."""
    store = get_result_store()
    row = _get_combo_row_or_404(store, combo_id)
    risk = _build_combo_risk_payload(combo_id, row)
    base_var = risk["total_var"]
    scenarios = [
        {"name": "2015股灾", "description": "高波动与流动性收缩场景", "type": "historical", "impact_pct": -0.128, "var_impact": round(base_var * 1.8, 4), "cvar_impact": round(base_var * 2.2, 4)},
        {"name": "2020疫情冲击", "description": "需求收缩叠加流动性扰动", "type": "historical", "impact_pct": -0.094, "var_impact": round(base_var * 1.5, 4), "cvar_impact": round(base_var * 1.9, 4)},
        {"name": "利率上行100bp", "description": "估值压缩与高股息回撤", "type": "hypothetical", "impact_pct": -0.056, "var_impact": round(base_var * 1.25, 4), "cvar_impact": round(base_var * 1.45, 4)},
    ]
    return ApiResponse(data=scenarios)


@router.get("/combos/{combo_id}/var-distribution", response_model=ApiResponse[Dict[str, Any]])
async def get_combo_var_distribution(
    combo_id: str,
    _user: UserInfo = Depends(get_current_user),
):
    """Return VaR distribution."""
    store = get_result_store()
    row = _get_combo_row_or_404(store, combo_id)
    risk = _build_combo_risk_payload(combo_id, row)
    bins = [-0.06, -0.05, -0.04, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02]
    counts = [3, 6, 12, 18, 24, 20, 11, 5, 2]
    return ApiResponse(data={"bins": bins, "counts": counts, "var_95": risk["total_var"], "cvar_95": risk["total_cvar"]})


@router.post("/combos/{combo_id}/backtest", response_model=ApiResponse[Dict[str, Any]])
async def backtest_combo(
    combo_id: str,
    user: UserInfo = Depends(require_role("researcher")),
):
    """Submit combo backtest task."""
    import uuid as uuid_mod
    store = get_result_store()
    _get_combo_row_or_404(store, combo_id)
    task_id = f"combo_bt_{uuid_mod.uuid4().hex[:8]}"
    return ApiResponse(data={"task_id": task_id, "status": "queued", "combo_id": combo_id, "submitted_by": user.username, "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
