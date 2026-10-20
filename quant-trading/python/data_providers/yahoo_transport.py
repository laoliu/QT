"""Explicit Yahoo transport, isolated from domestic API proxy overrides."""

import json
import logging
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib import request


def discover_proxy() -> str:
    explicit = os.environ.get("QUANT_US_PROXY") or os.environ.get("YFINANCE_PROXY")
    if explicit:
        return explicit
    for key in ("https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY", "http_proxy", "HTTP_PROXY"):
        if os.environ.get(key):
            return os.environ[key]
    # Domestic providers may replace getproxies(); the Windows registry remains readable.
    proxies = (request.getproxies_registry() if os.name == "nt" else request.getproxies())
    return proxies.get("https") or proxies.get("http") or ""


YAHOO_PROXY = discover_proxy()
REQUEST_TIMEOUT = 5.0
REQUEST_BUDGET = 20.0
COOLDOWN_SECONDS = 300


def _state_path() -> Path:
    root = os.environ.get("QUANT_DATA_DIR")
    return (Path(root) if root else Path.home() / ".quant-trading") / "cache" / "yahoo-cooldown.json"


def in_cooldown() -> bool:
    try:
        return float(json.loads(_state_path().read_text(encoding="utf-8"))["retry_after"]) > time.time()
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _start_cooldown() -> None:
    path = _state_path()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix="yahoo-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump({"retry_after": time.time() + COOLDOWN_SECONDS}, stream)
        temporary.replace(path)
    except OSError:
        pass
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def create_session():
    from curl_cffi import CurlOpt
    from curl_cffi.requests import Session

    class BoundedYahooSession(Session):
        def __init__(self):
            self.deadline = time.monotonic() + REQUEST_BUDGET
            self.unavailable = False
            super().__init__(impersonate="chrome", trust_env=False,
                             proxy=YAHOO_PROXY or "",
                             curl_options={CurlOpt.NOPROXY: "", CurlOpt.PROXY: YAHOO_PROXY or ""})

        def request(self, method, url, *args, **kwargs):
            remaining = self.deadline - time.monotonic()
            if self.unavailable or in_cooldown() or remaining <= 0:
                raise TimeoutError("Yahoo Finance temporarily unavailable; retry deferred")
            kwargs["timeout"] = min(REQUEST_TIMEOUT, remaining)
            try:
                response = super().request(method, url, *args, **kwargs)
                if response.status_code == 429:
                    raise RuntimeError("Yahoo Finance rate limited (HTTP 429)")
                return response
            except Exception:
                self.unavailable = True
                _start_cooldown()
                raise

    return BoundedYahooSession()


@contextmanager
def concise_yahoo_logs():
    """Replace dependency per-ticker tracebacks with one caller-owned warning."""
    logger = logging.getLogger("yfinance")
    previous = logger.level
    logger.setLevel(logging.CRITICAL)
    try:
        yield
    finally:
        logger.setLevel(previous)
