"""
Multi-source sector & limit-up data provider.

Cascading fallback chain for board members, limit-up pools, and fund flow:
  1. XTQuant bridge (/sector_members) — fastest, local data, no rate limit
  2. Eastmoney direct HTTP — no AKShare middle layer, more stable
  3. AKShare (current default) — convenience wrapper, but flaky
  4. Tushare Pro (if token configured) — paid API, stable

Usage:
    from data_providers.sector_data import (
        fetch_sector_members_multi,
        fetch_zt_pool_multi,
        fetch_fund_flow_multi,
        fetch_sector_fund_flow_direct,
    )
    members = fetch_sector_members_multi("电力")
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_PYTHON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PYTHON_ROOT)

from data_providers.sector_index import (
    get_sector_member_codes as _csv_sector_codes,
    get_sector_members as _csv_sector_members_raw,
    is_loaded as _csv_is_loaded,
)
from engines.lianban import _safe_float, _safe_int

# ── Config ──


def _read_bridge_url_from_config() -> str:
    """Read windowsBridgeUrl from ~/.quant-trading/quant-trading.json config."""
    try:
        config_path = os.path.expanduser("~/.quant-trading/quant-trading.json")
        if os.path.exists(config_path):
            with open(config_path) as f:
                cfg = json.load(f)
            url = (cfg.get("plugins", {}).get("entries", {})
                   .get("quant-trading", {}).get("config", {})
                   .get("tradingAccount", {}).get("qmt", {})
                   .get("windowsBridgeUrl", ""))
            if url:
                return url.rstrip("/")
    except Exception:
        pass
    return ""


WINDOWS_XTDATA_URL = (
    os.environ.get("WINDOWS_XTDATA_URL")
    or _read_bridge_url_from_config()
    or "http://127.0.0.1:58088"
)
TUSHARE_TOKEN = os.environ.get("TUSHARE_TOKEN", "")

# Reduce proxy interference for Chinese finance sites
_NO_PROXY_DOMAINS = ".eastmoney.com,.sina.com.cn,.10jqka.com.cn,.tushare.pro"
for _env_key in ("no_proxy", "NO_PROXY"):
    _existing = os.environ.get(_env_key, "")
    _merged = ",".join(filter(None, [_existing, _NO_PROXY_DOMAINS]))
    os.environ[_env_key] = _merged


def _log(msg: str) -> None:
    print(f"[sector_data] {msg}", file=sys.stderr)


# ══════════════════════════════════════════════════════════════════
#  Source 1: XTQuant Bridge
# ══════════════════════════════════════════════════════════════════


def _bridge_available() -> bool:
    """Check if the Windows xtdata bridge is reachable."""
    try:
        req = urllib.request.Request(f"{WINDOWS_XTDATA_URL}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data.get("status") in ("ok", "degraded") and data.get("xtdata", False)
    except Exception:
        return False


def _bridge_sector_members(sector: str) -> list[str]:
    """Fetch sector member codes via XTQuant bridge /sector_members endpoint.

    Returns list of 6-digit stock codes, e.g. ["600519", "000001"].
    """
    try:
        payload = json.dumps({"sector": sector}).encode()
        req = urllib.request.Request(
            f"{WINDOWS_XTDATA_URL}/sector_members",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get("status") == "ok":
                return result.get("data", [])
    except Exception as exc:
        _log(f"bridge sector_members({sector}) failed: {exc}")
    return []


# ══════════════════════════════════════════════════════════════════
#  Source 2: Eastmoney Direct HTTP (bypass AKShare)
# ══════════════════════════════════════════════════════════════════

_EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://data.eastmoney.com/",
}


def _em_fetch_json(url: str, params: dict[str, str], timeout: int = 10) -> Any:
    """Fetch JSON from Eastmoney API with standard headers."""
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    full_url = f"{url}?{qs}"
    req = urllib.request.Request(full_url, headers=_EM_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _em_sector_members(industry: str) -> list[dict[str, Any]]:
    """Fetch board member stocks via Eastmoney push2 API (bypassing AKShare).

    This is the same underlying API that AKShare's stock_board_industry_cons_em
    calls, but without the AKShare wrapper layer.

    Returns list of {code, name, changePct, price, marketCap, turnoverRate, pe, pb, amount}.
    """
    # Step 1: resolve industry name to board code (bk code)
    bk_code = _em_resolve_board_code(industry)
    if not bk_code:
        return []

    # Step 2: fetch members
    try:
        data = _em_fetch_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "fid": "f3",
                "po": "1",
                "pz": "500",  # max 500 stocks per board
                "pn": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": f"b:{bk_code}+f:!50",
                "fields": "f12,f14,f3,f2,f20,f8,f9,f23,f6",
            },
        )
        items = ((data or {}).get("data") or {}).get("diff") or []
        if not items:
            return []
        members: list[dict[str, Any]] = []
        for item in items:
            members.append({
                "code": str(item.get("f12", "")),
                "name": str(item.get("f14", "")),
                "changePct": _safe_float(item.get("f3")),
                "price": _safe_float(item.get("f2")),
                "marketCap": _safe_float(item.get("f20")),
                "turnoverRate": _safe_float(item.get("f8")),
                "pe": _safe_float(item.get("f9")),
                "pb": _safe_float(item.get("f23")),
                "amount": _safe_float(item.get("f6")),
            })
        return members
    except Exception as exc:
        _log(f"em_sector_members({industry}) failed: {exc}")
        return []


def _em_resolve_board_code(industry: str) -> str:
    """Resolve industry name (e.g. '电力') to Eastmoney board code (e.g. 'BK0428').

    Fetches the full industry board list and matches by name.
    """
    try:
        data = _em_fetch_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "fid": "f3",
                "po": "1",
                "pz": "500",
                "pn": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": "m:90+t:2",  # industry boards
                "fields": "f12,f14",
            },
        )
        items = ((data or {}).get("data") or {}).get("diff") or []
        for item in items:
            board_name = str(item.get("f14", ""))
            board_code = str(item.get("f12", ""))
            if board_name == industry or industry in board_name or board_name in industry:
                return board_code
    except Exception as exc:
        _log(f"em_resolve_board_code({industry}) failed: {exc}")
    return ""


def _em_zt_pool(date: str) -> list[dict[str, Any]]:
    """Fetch limit-up pool directly from Eastmoney push2ex API.

    This is what AKShare's stock_zt_pool_em / stock_zt_pool_previous_em call
    under the hood.

    Returns list of {code, name, price, change_pct, industry, lian_ban_count, ...}.
    """
    try:
        data = _em_fetch_json(
            "https://push2ex.eastmoney.com/getTopicZTPool",
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Ession": "90",
                "date": date,
            },
        )
        pool = (data or {}).get("data") or {}
        pool_items = pool.get("pool") or []
        if not pool_items:
            return []
        stocks: list[dict[str, Any]] = []
        for item in pool_items:
            code = str(item.get("c", ""))
            stocks.append({
                "code": code,
                "name": str(item.get("n", "")),
                "price": _safe_float(item.get("p")) / 1000 if item.get("p") else 0,
                "change_pct": _safe_float(item.get("zdp")),
                "amount": _safe_float(item.get("amount")),
                "turnover_rate": _safe_float(item.get("hs")),
                "first_board_time": str(item.get("fbt", "")),
                "last_board_time": str(item.get("lbt", "")),
                "open_count": _safe_int(item.get("oc")),
                "lian_ban_count": _safe_int(item.get("lbc")),
                "industry": str(item.get("hybk", "")),
                "flow_value": _safe_float(item.get("ltsz")),
                "total_value": _safe_float(item.get("tshare")),
            })
        return stocks
    except Exception as exc:
        _log(f"em_zt_pool({date}) failed: {exc}")
        return []


def _em_zt_pool_previous(date: str) -> list[dict[str, Any]]:
    """Fetch yesterday's limit-up stocks via Eastmoney push2ex API.

    Returns same format as _em_zt_pool.
    """
    try:
        data = _em_fetch_json(
            "https://push2ex.eastmoney.com/getYesterdayZTPool",
            {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Ession": "90",
                "date": date,
            },
        )
        pool = (data or {}).get("data") or {}
        pool_items = pool.get("pool") or []
        if not pool_items:
            return []
        stocks: list[dict[str, Any]] = []
        for item in pool_items:
            code = str(item.get("c", ""))
            stocks.append({
                "code": code,
                "name": str(item.get("n", "")),
                "price": _safe_float(item.get("p")) / 1000 if item.get("p") else 0,
                "change_pct": _safe_float(item.get("zdp")),
                "amount": _safe_float(item.get("amount")),
                "turnover_rate": _safe_float(item.get("hs")),
                "open_count": _safe_int(item.get("oc")),
                "lian_ban_count": _safe_int(item.get("lbc", 1)),
                "industry": str(item.get("hybk", "")),
                "flow_value": _safe_float(item.get("ltsz")),
                "total_value": _safe_float(item.get("tshare")),
            })
        return stocks
    except Exception as exc:
        _log(f"em_zt_pool_previous({date}) failed: {exc}")
        return []


def _em_fund_flow_rank() -> dict[str, dict[str, Any]]:
    """Fetch today's fund flow ranking via Eastmoney direct API.

    Returns {code: {mainNetIn, mainNetInPct, ...}}.
    """
    try:
        data = _em_fetch_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "fid": "f62",
                "po": "1",
                "pz": "500",
                "pn": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2",
                "fields": "f12,f62,f184,f66,f72",
            },
        )
        items = ((data or {}).get("data") or {}).get("diff") or []
        if not items:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            code = str(item.get("f12", ""))
            if code:
                result[code] = {
                    "mainNetIn": _safe_float(item.get("f62")),
                    "mainNetInPct": _safe_float(item.get("f184")),
                    "superLargeNetIn": _safe_float(item.get("f66")),
                    "largeNetIn": _safe_float(item.get("f72")),
                }
        return result
    except Exception as exc:
        _log(f"em_fund_flow_rank failed: {exc}")
        return {}


def _em_sector_fund_flow() -> dict[str, dict[str, Any]]:
    """Fetch sector-level fund flow ranking via Eastmoney direct API.

    Returns {sector_name: {mainNetIn, changePct, ...}}.
    """
    try:
        data = _em_fetch_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "fid": "f62",
                "po": "1",
                "pz": "100",
                "pn": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": "m:90+t:2",
                "fields": "f14,f3,f62,f184",
            },
        )
        items = ((data or {}).get("data") or {}).get("diff") or []
        if not items:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            name = str(item.get("f14", ""))
            if name:
                result[name] = {
                    "changePct": _safe_float(item.get("f3")),
                    "mainNetIn": _safe_float(item.get("f62")),
                    "mainNetInPct": _safe_float(item.get("f184")),
                }
        return result
    except Exception as exc:
        _log(f"em_sector_fund_flow failed: {exc}")
        return {}


# ══════════════════════════════════════════════════════════════════
#  Source 3: AKShare (existing default)
# ══════════════════════════════════════════════════════════════════


def _akshare_sector_members(industry: str) -> list[dict[str, Any]]:
    """Fetch sector members via AKShare (wrapper around Eastmoney)."""
    try:
        import akshare as ak

        df = ak.stock_board_industry_cons_em(symbol=industry)
        if df is None or df.empty:
            return []
        members: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            members.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "changePct": _safe_float(row.get("涨跌幅")),
                "price": _safe_float(row.get("最新价")),
                "marketCap": _safe_float(row.get("总市值")),
                "turnoverRate": _safe_float(row.get("换手率")),
                "pe": _safe_float(row.get("市盈率-动态")),
                "pb": _safe_float(row.get("市净率")),
                "amount": _safe_float(row.get("成交额")),
            })
        return members
    except Exception as exc:
        _log(f"akshare sector_members({industry}) failed: {exc}")
        return []


def _akshare_zt_pool(date: str) -> dict[str, Any]:
    """Fetch ZT pool via AKShare (delegates to lianban engine)."""
    from engines.lianban import fetch_zt_pool
    return fetch_zt_pool(date)


def _akshare_zt_pool_previous(date: str) -> dict[str, Any]:
    """Fetch yesterday's ZT pool via AKShare (delegates to lianban engine)."""
    from engines.lianban import fetch_zt_pool_previous
    return fetch_zt_pool_previous(date)


def _akshare_fund_flow_rank() -> dict[str, dict[str, Any]]:
    """Fetch fund flow ranking via AKShare."""
    try:
        import akshare as ak

        df = ak.stock_individual_fund_flow_rank(indicator="今日")
        if df is None or df.empty:
            return {}
        result: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", ""))
            if code:
                result[code] = {
                    "mainNetIn": _safe_float(row.get("主力净流入-净额")),
                    "mainNetInPct": _safe_float(row.get("主力净流入-净占比")),
                    "superLargeNetIn": _safe_float(row.get("超大单净流入-净额")),
                    "largeNetIn": _safe_float(row.get("大单净流入-净额")),
                }
        return result
    except Exception as exc:
        _log(f"akshare fund_flow_rank failed: {exc}")
        return {}


# ══════════════════════════════════════════════════════════════════
#  Source 4: Tushare Pro (paid, stable)
# ══════════════════════════════════════════════════════════════════


def _tushare_sector_members(industry: str) -> list[dict[str, Any]]:
    """Fetch sector members via Tushare Pro ths_member API.

    Requires TUSHARE_TOKEN env var and sufficient Tushare points (≈2000).
    """
    if not TUSHARE_TOKEN:
        return []
    try:
        import tushare as ts

        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()
        # Tushare uses ths_member for 同花顺行业成员
        df = pro.ths_member(ts_code="", name=industry)
        if df is None or df.empty:
            return []
        members: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            code = str(row.get("code", row.get("con_code", "")))
            # Strip .SH/.SZ suffix
            code = code.split(".")[0] if "." in code else code
            members.append({
                "code": code,
                "name": str(row.get("con_name", row.get("name", ""))),
                "changePct": 0,
                "price": 0,
                "marketCap": 0,
                "turnoverRate": 0,
                "pe": 0,
                "pb": 0,
                "amount": 0,
            })
        return members
    except Exception as exc:
        _log(f"tushare sector_members({industry}) failed: {exc}")
        return []


def _tushare_zt_pool(date: str) -> list[dict[str, Any]]:
    """Fetch limit-up pool via Tushare Pro limit_list API.

    Requires ≈5000 Tushare points.
    """
    if not TUSHARE_TOKEN:
        return []
    try:
        import tushare as ts

        ts.set_token(TUSHARE_TOKEN)
        pro = ts.pro_api()
        df = pro.limit_list(trade_date=date, limit_type="U")
        if df is None or df.empty:
            return []
        stocks: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            code = str(row.get("ts_code", "")).split(".")[0]
            stocks.append({
                "code": code,
                "name": str(row.get("name", "")),
                "price": _safe_float(row.get("close")),
                "change_pct": _safe_float(row.get("pct_chg")),
                "amount": _safe_float(row.get("amount")),
                "turnover_rate": _safe_float(row.get("turnover_ratio")),
                "first_board_time": str(row.get("first_time", "")),
                "last_board_time": str(row.get("last_time", "")),
                "open_count": _safe_int(row.get("open_times")),
                "lian_ban_count": _safe_int(row.get("limit_times", 1)),
                "industry": str(row.get("industry", "")),
            })
        return stocks
    except Exception as exc:
        _log(f"tushare zt_pool({date}) failed: {exc}")
        return []


# ══════════════════════════════════════════════════════════════════
#  Unified Multi-Source APIs (cascading fallback)
# ══════════════════════════════════════════════════════════════════


def fetch_sector_members_multi(
    industry: str,
    stock_data: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch sector member stocks with cascading fallback.

    Tries: CSV local → Eastmoney direct → AKShare → XTQuant bridge → Tushare → universe fallback.
    CSV is first because it's always available, covers all 31 申万一级行业, and is free of
    network flakiness. Eastmoney/AKShare enrich with real-time data if CSV is unavailable.

    Returns:
        (members_list, source_name) — source_name is for logging/diagnostics.
    """
    # 0. CSV local index (offline, covers all 31 sectors)
    csv_members = _csv_sector_members_raw(industry)
    if csv_members:
        # CSV entries have {code, name, sector, free_float_mv} — map to standard shape
        members: list[dict[str, Any]] = []
        for s in csv_members:
            code = s.get("code", "")
            info = (stock_data or {}).get(code, {})
            members.append({
                "code": code,
                "name": s.get("name", "") or info.get("name", ""),
                "changePct": _safe_float(info.get("changePct", info.get("change_pct", 0))),
                "price": _safe_float(info.get("price", info.get("trade", 0))),
                "marketCap": s.get("free_float_mv", 0) or _safe_float(info.get("totalMarketCap", info.get("mktcap", 0))),
                "turnoverRate": _safe_float(info.get("turnoverRate", info.get("turnoverratio", 0))),
                "pe": _safe_float(info.get("pe", info.get("per", 0))),
                "pb": _safe_float(info.get("pb", info.get("pbnewmrq", 0))),
                "amount": _safe_float(info.get("amount", 0)),
            })
        _log(f"✓ {industry}: csv local returned {len(members)} members")
        return members, "csv_local"

    # 1. Eastmoney direct HTTP (stable, covers all industry boards)
    em_members = _em_sector_members(industry)
    if em_members:
        _log(f"✓ {industry}: eastmoney direct returned {len(em_members)} members")
        return em_members, "eastmoney"

    # 2. AKShare (convenience wrapper, but flaky)
    ak_members = _akshare_sector_members(industry)
    if ak_members:
        _log(f"✓ {industry}: akshare returned {len(ak_members)} members")
        return ak_members, "akshare"

    # 3. XTQuant bridge (limited sector coverage but good for indices)
    bridge_codes = _bridge_sector_members(industry)
    if bridge_codes:
        _log(f"✓ {industry}: bridge returned {len(bridge_codes)} members")
        # Bridge only returns codes, enrich with basic info from stock_data if available
        members: list[dict[str, Any]] = []
        for code in bridge_codes:
            info = (stock_data or {}).get(code, {})
            members.append({
                "code": code,
                "name": info.get("name", ""),
                "changePct": _safe_float(info.get("changePct", info.get("change_pct", 0))),
                "price": _safe_float(info.get("price", info.get("trade", 0))),
                "marketCap": _safe_float(info.get("totalMarketCap", info.get("mktcap", 0))),
                "turnoverRate": _safe_float(info.get("turnoverRate", info.get("turnoverratio", 0))),
                "pe": _safe_float(info.get("pe", info.get("per", 0))),
                "pb": _safe_float(info.get("pb", info.get("pbnewmrq", 0))),
                "amount": _safe_float(info.get("amount", 0)),
            })
        return members, "bridge"

    # 4. Tushare Pro (paid, stable if configured)
    ts_members = _tushare_sector_members(industry)
    if ts_members:
        _log(f"✓ {industry}: tushare returned {len(ts_members)} members")
        return ts_members, "tushare"

    # 5. Universe fallback (last resort — includes all stocks, not sector-specific)
    if stock_data:
        _log(f"⚠ {industry}: all sources failed, using universe fallback ({len(stock_data)} stocks)")
        members = []
        for code, data in stock_data.items():
            members.append({
                "code": code,
                "name": data.get("name", ""),
                "changePct": _safe_float(data.get("changePct", data.get("change_pct", 0))),
                "price": _safe_float(data.get("price", data.get("trade", 0))),
                "marketCap": _safe_float(data.get("totalMarketCap", data.get("mktcap", 0))),
                "turnoverRate": _safe_float(data.get("turnoverRate", data.get("turnoverratio", 0))),
                "pe": _safe_float(data.get("pe", data.get("per", 0))),
                "pb": _safe_float(data.get("pb", data.get("pbnewmrq", 0))),
                "amount": _safe_float(data.get("amount", 0)),
            })
        return members, "universe_fallback"

    _log(f"✗ {industry}: all sources failed and no stock_data fallback")
    return [], "none"


def fetch_zt_pool_multi(date: str, use_previous: bool = False) -> dict[str, Any]:
    """Fetch limit-up pool with cascading fallback.

    Tries: Eastmoney direct → AKShare → Tushare.

    Returns: {"stocks": [...], "date": "...", "source": "...", "error": ...}
    """
    # 1. Eastmoney direct
    if use_previous:
        em_stocks = _em_zt_pool_previous(date)
    else:
        em_stocks = _em_zt_pool(date)
    if em_stocks:
        _log(f"✓ zt_pool({date}, prev={use_previous}): eastmoney direct returned {len(em_stocks)}")
        return {"stocks": em_stocks, "date": date, "source": "eastmoney"}

    # 2. AKShare
    if use_previous:
        ak_result = _akshare_zt_pool_previous(date)
    else:
        ak_result = _akshare_zt_pool(date)
    if ak_result.get("stocks"):
        _log(f"✓ zt_pool({date}, prev={use_previous}): akshare returned {len(ak_result['stocks'])}")
        ak_result["source"] = "akshare"
        return ak_result

    # 3. Tushare
    ts_stocks = _tushare_zt_pool(date)
    if ts_stocks:
        _log(f"✓ zt_pool({date}, prev={use_previous}): tushare returned {len(ts_stocks)}")
        return {"stocks": ts_stocks, "date": date, "source": "tushare"}

    _log(f"✗ zt_pool({date}, prev={use_previous}): all sources failed")
    return {"stocks": [], "date": date, "source": "none", "error": "所有数据源均失败"}


def fetch_fund_flow_multi() -> tuple[dict[str, dict[str, Any]], str]:
    """Fetch fund flow ranking with cascading fallback.

    Tries: Eastmoney direct → AKShare.

    Returns: (flow_map, source_name)
    """
    # 1. Eastmoney direct
    em_flow = _em_fund_flow_rank()
    if em_flow:
        _log(f"✓ fund_flow: eastmoney direct returned {len(em_flow)}")
        return em_flow, "eastmoney"

    # 2. AKShare
    ak_flow = _akshare_fund_flow_rank()
    if ak_flow:
        _log(f"✓ fund_flow: akshare returned {len(ak_flow)}")
        return ak_flow, "akshare"

    _log("✗ fund_flow: all sources failed")
    return {}, "none"


def fetch_sector_fund_flow_direct() -> dict[str, dict[str, Any]]:
    """Fetch sector-level fund flow directly from Eastmoney.

    Returns: {sector_name: {changePct, mainNetIn, mainNetInPct}}
    """
    return _em_sector_fund_flow()
