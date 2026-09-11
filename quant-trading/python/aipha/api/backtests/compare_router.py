"""Backtest comparison endpoint — GET /backtests/compare?ids=a,b,c

为什么单独一个 router 模块而不是塞进 ``backtests/router.py``：
``detail_router`` 里有 ``GET /backtests/{run_id}``，它会吞掉任何
``GET /backtests/xxx``。FastAPI 按注册顺序匹配，所以 compare 必须先注册
—— ``app.py`` 里的 ``include_router`` 顺序（compare 在 detail 之前）就是
为此。放在同一个 router 对象里也能排序，但拆开更难被后来人改错。

与已有 ``POST /backtests/compare``（`router.py:149`）的分工：
POST 那个只是把若干条 run 记录原样返回，前端自己算对比；这里做真正的
横向对比 —— 每个指标给出 best / worst / avg 以及逐条取值，形状对齐
TS 侧既有实现 ``backtestStore.compare()``（`src/services/backtest-store.ts:354`），
以免同一块 UI 面对两种数据结构。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from aipha.api.auth import get_current_user
from aipha.api.deps import get_result_store
from aipha.api.schemas import ApiResponse, UserInfo

router = APIRouter(prefix="/backtests", tags=["backtests"])

# 一次最多比几条。上限是为了别让一个 URL 打出几百次 get_run；
# 对比视图本身超过十条也没法看。
_MAX_COMPARE = 10

# (返回字段, backtest_runs 列名, 越大越好?)
#
# ``max_drawdown`` 的 higher_is_better=False，且排序按**绝对值**：
# 库里这一列的正负号不统一（`router.py:68` 的过滤器就是用 abs() 兜的），
# 直接比大小会把 -0.35 判成比 0.05 更优。
_METRICS: tuple[tuple[str, str, bool], ...] = (
    ("total_return", "_total_return", True),
    ("annualized_return", "annualized_return", True),
    ("max_drawdown", "max_drawdown", False),
    ("sharpe_ratio", "sharpe_ratio", True),
    ("win_rate", "win_rate", True),
    ("profit_factor", "profit_factor", True),
)


def _total_return(row: Dict[str, Any]) -> Optional[float]:
    """总收益率。库里没有这一列，用 final_nav / initial_capital 推。"""
    init = row.get("initial_capital")
    final = row.get("final_nav")
    if not init or final is None:
        return None
    return final / init - 1.0


def _metric_value(row: Dict[str, Any], column: str) -> Optional[float]:
    if column == "_total_return":
        return _total_return(row)
    raw = row.get(column)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _build_metric(
    rows: List[Dict[str, Any]], column: str, higher_is_better: bool
) -> Dict[str, Any]:
    """汇总单个指标的 best / worst / avg / 逐条取值。

    缺值（None）的处理：仍然出现在 ``values`` 里，但**不参与** best /
    worst / avg。TS 侧对缺值兜的是 0，那会让一条没跑出 Sharpe 的回测
    在别人都是负 Sharpe 时被评成 "best"，是个假结论；这里宁可少算。
    """
    values = [
        {"run_id": r["run_id"], "value": _metric_value(r, column)} for r in rows
    ]
    known = [v for v in values if v["value"] is not None]
    if not known:
        return {"best": None, "worst": None, "avg": None, "values": values}

    def sort_key(item: Dict[str, Any]) -> float:
        v = float(item["value"])
        return abs(v) if column == "max_drawdown" else v

    ordered = sorted(known, key=sort_key, reverse=higher_is_better)
    return {
        "best": ordered[0],
        "worst": ordered[-1],
        "avg": sum(float(v["value"]) for v in known) / len(known),
        "values": values,
    }


@router.get("/compare", response_model=ApiResponse[Dict[str, Any]])
async def compare_backtest_runs(
    ids: str = Query(..., description="逗号分隔的 run_id，至少 2 个"),
    _user: UserInfo = Depends(get_current_user),
):
    """横向对比多次回测。

    Args:
        ids: 逗号分隔的 run_id 列表（去重，保持传入顺序）。

    Raises:
        HTTPException: 400 —— 有效 run_id 少于 2 个，或个数超过上限。
    """
    requested: List[str] = []
    for raw in ids.split(","):
        rid = raw.strip()
        if rid and rid not in requested:
            requested.append(rid)

    if len(requested) > _MAX_COMPARE:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多对比 {_MAX_COMPARE} 条，收到 {len(requested)} 条",
        )

    store = get_result_store()
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    for rid in requested:
        row = store.get_run(rid)
        if row is None:
            missing.append(rid)
        else:
            rows.append(dict(row))

    # 少于 2 条有效记录时对比无意义 —— 明确报错，不返回一个空壳让前端画图。
    if len(rows) < 2:
        raise HTTPException(
            status_code=400,
            detail=(
                f"需要至少 2 个有效 run_id，实际有效 {len(rows)} 个"
                + (f"，未找到: {', '.join(missing)}" if missing else "")
            ),
        )

    return ApiResponse(
        data={
            "run_ids": [r["run_id"] for r in rows],
            "missing": missing,
            "runs": rows,
            "metrics": {
                name: _build_metric(rows, column, higher_is_better)
                for name, column, higher_is_better in _METRICS
            },
        }
    )
