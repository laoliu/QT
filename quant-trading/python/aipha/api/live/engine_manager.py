"""Control an explicitly attached runner; never create a trading connection."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Protocol

from aipha.utils.paths import KILL_SWITCH_PATH
from aipha.execution.emergency_control import control_status

logger = logging.getLogger(__name__)


class StoppableRunner(Protocol):
    def emergency_stop(self, reason: str) -> None: ...


class EngineManager:
    def __init__(self) -> None:
        self._runner: StoppableRunner | None = None
        self._lock = RLock()

    def attach(self, runner: StoppableRunner) -> None:
        with self._lock:
            if self._runner is not None and self._runner is not runner:
                raise RuntimeError("A runner is already attached")
            if KILL_SWITCH_PATH.exists():
                runner.emergency_stop("Persistent kill switch is active")
            self._runner = runner

    @property
    def is_running(self) -> bool:
        # Attachment alone does not establish that an engine is running.
        state = getattr(self._runner, "state", None)
        return bool(getattr(state, "is_running", False))

    def status(self) -> dict:
        return {
            "kill_switch_active": KILL_SWITCH_PATH.exists(),
            "runner_attached": self._runner is not None,
            "is_running": self.is_running,
            **control_status(KILL_SWITCH_PATH),
        }

    def stop(self, reason: str, actor: str = "system", wait_seconds: float = 2.0) -> dict:
        with self._lock:
            payload = {
                "reason": reason, "actor": actor,
                "request_id": uuid.uuid4().hex,
                "activated_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                self._publish_flag(payload)
            except OSError:
                if self._runner is not None:
                    try:
                        self._runner.emergency_stop(reason)
                    except Exception:
                        logger.exception("Local stop also failed after flag persistence failure")
                raise
            logger.critical("Live emergency stop: actor=%s reason=%s", actor, reason)
            notified = False
            error = None
            if self._runner is not None:
                try:
                    self._runner.emergency_stop(reason)
                    notified = True
                except Exception:
                    logger.exception("Runner emergency stop failed; persistent flag remains active")
                    error = "Runner stop failed; check execution logs and broker orders"
            deadline = time.monotonic() + min(max(wait_seconds, 0.0), 10.0)
            snapshot = self.status()
            while not snapshot["broker_cancellation_confirmed"] and time.monotonic() < deadline:
                if snapshot["request_id"] != payload["request_id"]:
                    break
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
                snapshot = self.status()
            superseded = snapshot["request_id"] != payload["request_id"]
            confirmed = snapshot["broker_cancellation_confirmed"] and not superseded
            return {
                **snapshot, "runner_notified": notified,
                "requested_id": payload["request_id"], "superseded": superseded,
                "broker_cancellation_confirmed": confirmed,
                # 机器调用方不要把 kill_switch_active 当作券商已撤单。
                # 持久化急停成功与 broker 撤单确认是两个独立结果。
                "stop_state": "confirmed" if confirmed else "unconfirmed",
                "error": error,
            }

    @staticmethod
    def _publish_flag(payload: dict) -> None:
        KILL_SWITCH_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Publish only a fully written flag. Never clear an existing stop.
        fd, name = tempfile.mkstemp(dir=KILL_SWITCH_PATH.parent, prefix=".kill-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, KILL_SWITCH_PATH)
        finally:
            if os.path.exists(name):
                os.unlink(name)


_manager = EngineManager()


def get_engine_manager() -> EngineManager:
    return _manager
