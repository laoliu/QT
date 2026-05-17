"""Realtime data provider via miniQMT HTTP bridge.

Fetches real-time quotes and klines from the XTData HTTP bridge
running on the Windows trading server. Provides factory functions
for integration with other engines.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from typing import Any, Dict, List, Optional

__all__ = [
    "RealtimeProvider", "fetch_realtime_quotes",
    "is_bridge_available", "get_bridge_url",
]

logger = logging.getLogger(__name__)

_DEFAULT_BRIDGE_URL = "http://127.0.0.1:58088"


def get_bridge_url() -> str:
    """Get XTData bridge URL from environment or config."""
    url = os.environ.get("WINDOWS_XTDATA_URL")
    if url:
        return url.rstrip("/")
    config_path = os.path.expanduser("~/.openclaw/openclaw.json")
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r") as f:
                cfg = json.load(f)
            url = cfg.get("xtdata_url")
            if url:
                return url.rstrip("/")
        except Exception:
            pass
    return _DEFAULT_BRIDGE_URL


def is_bridge_available(timeout: int = 3) -> bool:
    """Check if the XTData bridge is reachable."""
    url = get_bridge_url()
    try:
        req = urllib.request.Request(f"{url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        return False


def fetch_realtime_quotes(
    codes: List[str], timeout: int = 5,
) -> Dict[str, Dict[str, Any]]:
    """Fetch realtime quotes for multiple stock codes.

    Parameters
    ----------
    codes : list of str
        Stock codes like ['600519', '000001'].

    Returns
    -------
    Mapping of code -> quote dict.
    """
    url = get_bridge_url()
    qmt_codes = [_to_qmt_code(c) for c in codes]
    payload = json.dumps({"codes": qmt_codes}).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{url}/quote",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("Realtime quote fetch failed: %s", e)
        return {}


def _to_qmt_code(code: str) -> str:
    """Convert plain code to QMT format: '600519' -> '600519.SH'."""
    code = code.split(".")[0].strip()
    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


class RealtimeProvider:
    """Realtime data provider wrapping the XTData HTTP bridge."""

    def __init__(self, bridge_url: Optional[str] = None) -> None:
        self._url = (bridge_url or get_bridge_url()).rstrip("/")

    @property
    def url(self) -> str:
        return self._url

    def is_available(self, timeout: int = 3) -> bool:
        try:
            req = urllib.request.Request(f"{self._url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=timeout):
                return True
        except Exception:
            return False

    def get_quote(self, code: str) -> Dict[str, Any]:
        result = self.get_quotes([code])
        qmt = _to_qmt_code(code)
        return result.get(qmt, {})

    def get_quotes(self, codes: List[str]) -> Dict[str, Dict[str, Any]]:
        qmt_codes = [_to_qmt_code(c) for c in codes]
        payload = json.dumps({"codes": qmt_codes}).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{self._url}/quote",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning("Quote fetch failed: %s", e)
            return {}

    def get_klines(
        self, code: str, period: str = "1d", count: int = 100,
    ) -> List[Dict[str, Any]]:
        """Fetch klines from bridge."""
        qmt = _to_qmt_code(code)
        payload = json.dumps({
            "code": qmt, "period": period, "count": count,
        }).encode("utf-8")
        try:
            req = urllib.request.Request(
                f"{self._url}/klines",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("Klines fetch failed: %s", e)
            return []
