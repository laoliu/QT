"""Authenticated emergency stop. Resuming requires manual intervention."""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from aipha.api.auth import get_current_user
from aipha.api.schemas import UserInfo
from aipha.api.live.engine_manager import EngineManager, get_engine_manager

router = APIRouter(prefix="/live", tags=["live"])


def _operator(user: UserInfo = Depends(get_current_user)) -> UserInfo:
    if user.role not in {"ops", "risk", "risk_approver", "admin"}:
        raise HTTPException(status_code=403, detail="Trading operator role required")
    return user


class StopRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    # 默认给跨进程 worker 一个有限确认窗口；需要异步发布时显式传 0。
    wait_seconds: float = Field(default=2.0, ge=0.0, le=10.0, allow_inf_nan=False)


@router.get("/engine/status")
def engine_status(
    user: UserInfo = Depends(_operator),
    manager: EngineManager = Depends(get_engine_manager),
):
    return {"data": manager.status()}


@router.post("/engine/stop")
def emergency_stop(
    request: StopRequest,
    user: UserInfo = Depends(_operator),
    manager: EngineManager = Depends(get_engine_manager),
):
    reason = request.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A stop reason is required")
    try:
        result = manager.stop(reason, actor=user.username, wait_seconds=request.wait_seconds)
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Unable to persist emergency stop") from exc
    # 急停开关写入成功不等于券商撤单完成。对未确认状态返回 503，
    # 让调用方/监控 fail-closed；响应体仍保留完整结果，便于人工处置。
    if result.get("stop_state") != "confirmed":
        return JSONResponse(status_code=503, content={"data": result})
    return {"data": result}
