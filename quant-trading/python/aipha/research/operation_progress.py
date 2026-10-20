"""Append-only phase observations; timestamps are never operating receipts."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid


class OperationProgress:
    def __init__(self, root, day):
        self.root, self.day = Path(root), day
        self.attempt_id = uuid.uuid4().hex
        self.stages = []
        self.tick = None

    def __enter__(self):
        return self

    def update(self, phase, **fields):
        from .daily_operation import _json
        now = datetime.now(timezone.utc).isoformat()
        tick = time.monotonic()
        if self.stages and self.tick is not None:
            self.stages[-1].update(finished_at=now, duration_seconds=round(tick - self.tick, 3))
        self.stages.append({"phase": phase, "started_at": now, **fields})
        self.tick = tick
        event = {"attempt_id": self.attempt_id, "date": self.day, "pid": os.getpid(),
                 "phase": phase, "observed_at": now, "stages": self.stages}
        _json(self.root / "progress.json", event)
        # One small event per transition; no market data, credentials or exception text.
        with (self.root / "phase_events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({k: v for k, v in event.items() if k != "stages"}) + "\n")

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.update("failed", failed_phase=self.stages[-1]["phase"] if self.stages else None,
                        error_type=exc_type.__name__)
        return False
