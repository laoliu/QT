"""WebSocket endpoints for real-time data push."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from aipha.api.ws import ws_manager

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/alerts")
async def ws_alerts(websocket: WebSocket):
    """Real-time alert stream."""
    await ws_manager.connect(websocket, "alerts")
    try:
        while True:
            # Keep connection alive, receive pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "alerts")


@router.websocket("/ws/tasks")
async def ws_tasks(websocket: WebSocket):
    """Async task progress stream."""
    await ws_manager.connect(websocket, "tasks")
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "tasks")


@router.websocket("/ws/monitor/{paper_id}")
async def ws_monitor(websocket: WebSocket, paper_id: str):
    """Real-time paper trading monitor for a specific portfolio."""
    channel = f"monitor_{paper_id}"
    await ws_manager.connect(websocket, channel)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, channel)
