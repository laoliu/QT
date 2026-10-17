"""
realtime_provider — 实时行情接口（miniQMT data_bridge）

为真龙战法等盘中策略提供实时行情数据。
通过 HTTP 调用 Windows 端 qmt_data_bridge 服务获取：
  - 实时报价 (tick snapshots)
  - 5档盘口 (bid/ask levels)
  - 实时封单分析

接口设计：
  - fetch_realtime_quotes(codes) → 批量获取实时行情
  - fetch_realtime_quote(code) → 单只股票行情
  - get_bridge_url() → 读取配置的bridge地址

配置来源：
  ~/.quant-trading/quant-trading.json → plugins.entries.quant-trading.config
      .tradingAccount.qmt.windowsBridgeUrl
  默认: http://192.168.3.30:18888
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from typing import Any, Optional


def get_bridge_url() -> str:
    """Read bridge URL from quant-trading config or env.

    Priority:
      1. WINDOWS_XTDATA_URL env var
      2. ~/.quant-trading/quant-trading.json config
      3. Default fallback
    """
    # Env override
    env_url = os.environ.get("WINDOWS_XTDATA_URL")
    if env_url:
        return env_url.rstrip("/")

    # Read from config
    try:
        config_path = os.path.expanduser("~/.quant-trading/quant-trading.json")
        with open(config_path) as f:
            cfg = json.load(f)
        url = (
            cfg.get("plugins", {})
            .get("entries", {})
            .get("quant-trading", {})
            .get("config", {})
            .get("tradingAccount", {})
            .get("qmt", {})
            .get("windowsBridgeUrl", "")
        )
        if url:
            return url.rstrip("/")
    except Exception:
        pass

    return "http://192.168.3.30:18888"


def _bridge_post(path: str, body: dict, timeout: int = 10) -> dict:
    """POST to bridge endpoint, return JSON response."""
    url = f"{get_bridge_url()}{path}"
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": f"Bridge 请求失败: {e}", "url": url}


def is_bridge_available(timeout: int = 3) -> bool:
    """Check if the Windows bridge is reachable."""
    url = f"{get_bridge_url()}/health"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") == "ok"
    except Exception:
        return False


def fetch_realtime_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    """Batch fetch realtime quotes from miniQMT bridge.

    Args:
        codes: Stock codes in standard format, e.g. ["600519", "000001"]

    Returns:
        {
            "600519": {
                "price": float,
                "open": float,
                "high": float,
                "low": float,
                "prevClose": float,
                "volume": int,
                "amount": float,
                "bids": [{"price": float, "volume": int}, ...],  # 5 levels
                "asks": [{"price": float, "volume": int}, ...],
            },
            ...
        }

    Bridge format requires QMT codes (600519.SH), auto-converts.
    """
    if not codes:
        return {}

    # Convert to QMT format
    qmt_codes = [_to_qmt_code(c) for c in codes]

    result = _bridge_post("/quote", {"codes": qmt_codes})
    if result.get("error"):
        return {}

    # Normalize response: strip exchange suffix from keys
    normalized: dict[str, dict[str, Any]] = {}
    for key, val in result.items():
        if key in ("error", "url"):
            continue
        stock_id = key.split(".")[0] if "." in key else key
        if isinstance(val, dict):
            normalized[stock_id] = val

    return normalized


def fetch_realtime_quote(code: str) -> dict[str, Any]:
    """Fetch single stock realtime quote.

    Returns quote dict or empty dict on failure.
    """
    quotes = fetch_realtime_quotes([code])
    return quotes.get(code, {})


def _to_qmt_code(code: str) -> str:
    """Convert standard code to QMT format.

    600519 → 600519.SH
    000001 → 000001.SZ
    113050 → 113050.SH  (沪市转债)
    127060 → 127060.SZ  (深市转债)
    """
    c = code.split(".")[0]  # strip any existing suffix
    if c.startswith(("6", "688", "500", "510", "512", "513", "515", "518")):
        return f"{c}.SH"
    # 沪市可转债 11xxxx
    if c.startswith("11"):
        return f"{c}.SH"
    return f"{c}.SZ"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Convenience: create fetcher callable for dragon_master engine
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def create_realtime_fetcher():
    """Create a realtime fetcher function for dragon_master.run_full_analysis().

    Usage:
        from engines.realtime_provider import create_realtime_fetcher
        result = run_full_analysis(realtime_fetcher=create_realtime_fetcher())

    Returns None if bridge is not available (graceful degradation).
    """
    if not is_bridge_available():
        return None

    def _fetcher(codes: list[str]) -> dict[str, dict[str, Any]]:
        return fetch_realtime_quotes(codes)

    return _fetcher


def create_klines_fetcher(period: str = "daily", count: int = 30):
    """Create a K-lines fetcher function for dragon_master.

    Fetches daily klines from the bridge for volume analysis.

    Usage:
        from engines.realtime_provider import create_klines_fetcher
        result = run_full_analysis(klines_fetcher=create_klines_fetcher())
    """
    def _fetcher(code: str) -> list[dict[str, Any]]:
        qmt_code = _to_qmt_code(code)
        result = _bridge_post("/klines", {
            "codes": [qmt_code],
            "period": period,
            "count": count,
        })
        if result.get("error"):
            return []

        # Find data for this code
        for key, bars in result.items():
            if key in ("error", "url"):
                continue
            if isinstance(bars, list):
                return bars
        return []

    if not is_bridge_available():
        return None

    return _fetcher
