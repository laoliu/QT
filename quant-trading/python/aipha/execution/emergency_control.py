"""File-based stop requests and broker-verified acknowledgements across processes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".control-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_request(flag: Path) -> dict | None:
    try:
        content = flag.read_bytes()
    except FileNotFoundError:
        return None
    try:
        request = json.loads(content)
    except (ValueError, UnicodeError):
        request = {}
    if not isinstance(request, dict):
        request = {}
    request.setdefault("request_id", hashlib.sha256(content).hexdigest())
    return request


def control_status(flag: Path, *, stale_after: float = 5.0) -> dict:
    request = read_request(flag)
    workers = []
    errors = []
    for path in sorted((flag.parent / "kill_switch_control").glob("*.json")):
        try:
            worker = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(worker, dict):
                raise ValueError("Invalid worker record")
            if not worker.get("online", False) and (
                not request or worker.get("request_id") != request["request_id"]
            ) and not worker.get("unresolved_orders"):
                continue
            fresh = 0 <= time.time() - float(worker["heartbeat"]) <= stale_after
            matches = bool(request) and worker.get("request_id") == request["request_id"]
            worker["responsive"] = fresh and worker.get("online", False)
            worker["confirmed_for_request"] = bool(
                matches and worker.get("confirmed") and (fresh or not worker.get("online"))
            )
            workers.append(worker)
        except (OSError, ValueError, KeyError, TypeError):
            errors.append(path.name)
    # No agents, missing responses, unreadable records and stale responses must
    # never turn into an all-clear through vacuous truth or cached order state.
    confirmed = bool(request and workers and not errors) and all(
        worker["confirmed_for_request"] for worker in workers
    )
    return {
        "request_id": request["request_id"] if request else None,
        "broker_cancellation_confirmed": confirmed,
        "workers": workers, "control_errors": errors,
    }


class StopAgent:
    """Keep listening even when no market events arrive.

    query_open_orders must use a fresh broker query and raise on uncertain or
    unavailable data. The same lock serializes submissions and cancellation.
    """

    def __init__(
        self, flag: Path, query_open_orders: Callable[[], list[str]],
        cancel_order: Callable[[str], object], submission_lock,
        *, interval: float = 0.25, label: str = "broker",
    ) -> None:
        self.flag = flag
        self.query = query_open_orders
        self.cancel = cancel_order
        self.lock = submission_lock
        self.interval = interval
        self.worker_id = f"{os.getpid()}-{uuid.uuid4().hex}"
        self.path = flag.parent / "kill_switch_control" / f"{self.worker_id}.json"
        self.record = {
            "worker_id": self.worker_id, "pid": os.getpid(), "label": label,
            "request_id": None, "confirmed": False, "pending_orders": [],
            "error": None, "online": True,
        }
        self._exit = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Stop agent is already started")
        self.poll_once()  # registration must succeed before accepting orders
        self._thread = threading.Thread(target=self._run, daemon=True, name="broker-stop-agent")
        self._thread.start()

    def poll_once(self) -> None:
        try:
            request = read_request(self.flag)
            if request:
                with self.lock:
                    self.record.update(request_id=request["request_id"], confirmed=False, error=None)
                    pending = self.query()
                    self.record["pending_orders"] = pending
                    failures = []
                    for order_id in pending:
                        try:
                            self.cancel(order_id)
                        except Exception:
                            failures.append(order_id)
                    # Confirmation is exclusively based on a second broker query.
                    remaining = self.query()
                    self.record.update(confirmed=not remaining, pending_orders=remaining,
                                       error="Cancellation failed" if failures and remaining else None)
            else:
                self.record.update(request_id=None, confirmed=False, error=None, pending_orders=[])
        except Exception:
            self.record.update(confirmed=False, error="Broker stop/query unavailable")
            logger.exception("Broker emergency stop is not confirmed")
        self.record["heartbeat"] = time.time()
        atomic_json(self.path, self.record)

    def _run(self) -> None:
        while not self._exit.wait(self.interval):
            try:
                self.poll_once()
            except Exception:
                logger.exception("Cannot publish broker stop acknowledgement")

    def close(self) -> None:
        self._exit.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("Stop agent is still querying the broker; keep the connection open")
        self.poll_once()
        self.record.update(online=False, heartbeat=time.time())
        if self.record.get("request_id") is None:
            with self.lock:
                try:
                    self.record["pending_orders"] = self.query()
                    self.record["unresolved_orders"] = bool(self.record["pending_orders"])
                except Exception:
                    self.record.update(unresolved_orders=True, error="Final broker query unavailable")
            if not self.record["unresolved_orders"]:
                self.path.unlink(missing_ok=True)
                return
        else:
            self.record["unresolved_orders"] = not self.record["confirmed"]
        atomic_json(self.path, self.record)
