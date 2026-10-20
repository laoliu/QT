"""Resolve a healthy data bridge, including the local QMT push receiver."""

import json
import os
import threading
import time
import urllib.request
from urllib.parse import urlsplit, urlunsplit

_lock = threading.Lock()
_resolved = {}


def resolve_bridge_url(configured: str = "") -> str:
    configured = (configured or os.environ.get("WINDOWS_XTDATA_URL") or
                  "http://127.0.0.1:18888").rstrip("/")
    with _lock:
        cached = _resolved.get(configured)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        candidates = [configured]
        parsed = urlsplit(configured)
        if parsed.hostname in ("127.0.0.1", "localhost", "::1") and parsed.port == 18888:
            host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
            port = os.environ.get("BRIDGE_RECEIVER_PORT", "18889")
            candidates.append(urlunsplit((parsed.scheme, f"{host}:{port}", parsed.path, "", "")))
        result = ""
        for candidate in candidates:
            try:
                with urllib.request.urlopen(f"{candidate}/health", timeout=0.6) as response:
                    health = json.load(response)
                healthy = health.get("status") in ("ok", "degraded") or health.get("ok") is True
                ready = health.get("bridge_connected") if "bridge_connected" in health else health.get("xtdata")
                if healthy and ready:
                    result = candidate
                    break
            except (OSError, ValueError):
                continue
        _resolved[configured] = (time.monotonic() + (30 if result else 3), result)
        return result
