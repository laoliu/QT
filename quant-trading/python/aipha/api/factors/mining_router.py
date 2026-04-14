"""因子挖掘与评估 API — 供前端 Dashboard 查询挖掘/评估结果."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from aipha.api.auth import get_current_user
from aipha.api.schemas import (
    ApiResponse,
    PaginatedResponse,
    PaginationMeta,
    UserInfo,
)
from aipha.factors.mining_store import MiningStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mining", tags=["mining"])

_store: MiningStore | None = None


def _get_store() -> MiningStore:
    """Lazy-init singleton MiningStore."""
    global _store
    if _store is None:
        _store = MiningStore()
    return _store


@router.get("/dashboard", response_model=ApiResponse)
async def get_dashboard(
    user: UserInfo = Depends(get_current_user),
) -> ApiResponse:
    """总览统计 — 挖掘总数/通过率/评估轮/独立因子."""
    store = _get_store()
    stats = store.get_dashboard_stats()
    return ApiResponse(data=stats)


@router.get("/factors", response_model=PaginatedResponse)
async def list_mined_factors(
    passed: Optional[bool] = Query(None),
    min_icir: float = Query(0, ge=0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    user: UserInfo = Depends(get_current_user),
) -> PaginatedResponse:
    """分页查询挖掘因子."""
    store = _get_store()
    offset = (page - 1) * page_size
    rows = store.query_mined_factors(
        passed=passed, min_icir=min_icir,
        limit=page_size, offset=offset,
    )
    total = store.count_mined_factors(
        passed=passed, min_icir=min_icir,
    )
    total_pages = (total + page_size - 1) // page_size
    return PaginatedResponse(
        data=rows,
        pagination=PaginationMeta(
            page=page, page_size=page_size,
            total=total, total_pages=total_pages,
        ),
    )


@router.get("/eval-runs", response_model=ApiResponse)
async def list_eval_runs(
    limit: int = Query(50, ge=1, le=500),
    user: UserInfo = Depends(get_current_user),
) -> ApiResponse:
    """查询评估运行记录."""
    store = _get_store()
    rows = store.query_eval_runs(limit=limit)
    return ApiResponse(data=rows)


@router.get("/eval-factors", response_model=ApiResponse)
async def list_eval_factors(
    eval_id: Optional[str] = Query(None),
    min_score: float = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    user: UserInfo = Depends(get_current_user),
) -> ApiResponse:
    """查询评估因子详情."""
    store = _get_store()
    rows = store.query_eval_factors(
        eval_id=eval_id, min_score=min_score, limit=limit,
    )
    return ApiResponse(data=rows)


@router.get("/rounds", response_model=ApiResponse)
async def list_mining_rounds(
    kb_path: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    user: UserInfo = Depends(get_current_user),
) -> ApiResponse:
    """查询挖掘轮次记录."""
    store = _get_store()
    rows = store.query_mining_rounds(
        kb_path=kb_path, limit=limit,
    )
    return ApiResponse(data=rows)
