"""WebSocket connection manager for real-time data push."""

from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Set

from fastapi import WebSocket, WebSocketDisconnect


class ConnectionManager:
    """Manage WebSocket connections grouped by channel."""

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str = "default"):
        await websocket.accept()
        if channel not in self._connections:
            self._connections[channel] = set()
        self._connections[channel].add(websocket)

    def disconnect(self, websocket: WebSocket, channel: str = "default"):
        if channel in self._connections:
            self._connections[channel].discard(websocket)

    async def broadcast(self, channel: str, data: dict):
        """Broadcast JSON message to all connections in a channel."""
        if channel not in self._connections:
            return
        dead: List[WebSocket] = []
        for ws in self._connections[channel]:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[channel].discard(ws)

    @property
    def connection_count(self) -> int:
        return sum(len(s) for s in self._connections.values())


# Global singleton
ws_manager = ConnectionManager()
