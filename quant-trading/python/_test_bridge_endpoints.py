#!/usr/bin/env python3
"""Test QMT bridge new endpoints: /sector_list, /sector_members, and multi-source cascade."""
import json
import os
import sys
import urllib.error
import urllib.request

def _read_bridge_url_from_config() -> str:
    """Read windowsBridgeUrl from quant-trading.json config."""
    config_path = os.path.expanduser("~/.quant-trading/quant-trading.json")
    try:
        with open(config_path, "r") as f:
            cfg = json.loads(f.read())
        url = (cfg.get("plugins", {}).get("entries", {})
               .get("quant-trading", {}).get("config", {})
               .get("tradingAccount", {}).get("qmt", {})
               .get("windowsBridgeUrl", ""))
        if url:
            return url
    except Exception:
        pass
    return os.environ.get("WINDOWS_XTDATA_URL", "http://127.0.0.1:18888")


BRIDGE_URL = _read_bridge_url_from_config()


def _post(path: str, body: dict, timeout: int = 15) -> dict:
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BRIDGE_URL}{path}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _get(path: str, timeout: int = 10) -> dict:
    req = urllib.request.Request(f"{BRIDGE_URL}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def main():
    print(f"Bridge URL: {BRIDGE_URL}")
    print("=" * 60)

    # 1. Health check
    print("\n[1] GET /health")
    try:
        h = _get("/health")
        print(f"  status: {h.get('status')}")
        print(f"  xtdata: {h.get('xtdata')}")
        print(f"  trading: {h.get('trading')}")
        print(f"  mode: {h.get('mode', 'serve')}")
        print(f"  version: {h.get('version', '?')}")
        is_serve_mode = h.get("xtdata") is not None or h.get("version") is not None
        is_receiver = h.get("mode") == "push-receiver"
        if is_receiver:
            print("  ** This is the Mac push-receiver, not the Windows QMT bridge!")
            print("  ** Set WINDOWS_XTDATA_URL to point to your Windows IP")
            # Still continue to test endpoints - they may forward to Windows
    except Exception as e:
        print(f"  ERROR: {e}")
        print("  Bridge not reachable. Aborting.")
        return

    # 2. Sector list (industry)
    print("\n[2] POST /sector_list (industry)")
    try:
        r = _post("/sector_list", {"sector_type": "industry"})
        sectors = r.get("data", [])
        print(f"  status: {r.get('status')}")
        print(f"  count: {r.get('count', len(sectors))}")
        if sectors:
            print(f"  first 10: {sectors[:10]}")
        else:
            print("  (empty)")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 3. Sector list (concept)
    print("\n[3] POST /sector_list (concept)")
    try:
        r = _post("/sector_list", {"sector_type": "concept"})
        sectors = r.get("data", [])
        print(f"  count: {r.get('count', len(sectors))}")
        if sectors:
            print(f"  first 10: {sectors[:10]}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 4. Sector members (电力)
    print("\n[4] POST /sector_members (电力)")
    try:
        r = _post("/sector_members", {"sector": "电力"})
        members = r.get("data", [])
        print(f"  status: {r.get('status')}")
        print(f"  sector: {r.get('sector')}")
        print(f"  count: {r.get('count', len(members))}")
        if members:
            print(f"  first 10 codes: {members[:10]}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 5. Sector members (电力设备)
    print("\n[5] POST /sector_members (电力设备)")
    try:
        r = _post("/sector_members", {"sector": "电力设备"})
        members = r.get("data", [])
        print(f"  count: {r.get('count', len(members))}")
        if members:
            print(f"  first 10 codes: {members[:10]}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 6. Sector members (半导体)
    print("\n[6] POST /sector_members (半导体)")
    try:
        r = _post("/sector_members", {"sector": "半导体"})
        members = r.get("data", [])
        print(f"  count: {r.get('count', len(members))}")
        if members:
            print(f"  first 5 codes: {members[:5]}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 7. Test multi-source cascade (via sector_data module)
    print("\n[7] Multi-source sector_members cascade ('电力')")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from data_providers.sector_data import fetch_sector_members_multi
        members, source = fetch_sector_members_multi("电力")
        print(f"  source: {source}")
        print(f"  count: {len(members)}")
        if members:
            print(f"  samples: {[(m['code'], m['name']) for m in members[:5]]}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 8. Multi-source ZT pool
    print("\n[8] Multi-source zt_pool_multi (20260320)")
    try:
        from data_providers.sector_data import fetch_zt_pool_multi
        result = fetch_zt_pool_multi("20260320", use_previous=True)
        stocks = result.get("stocks", [])
        print(f"  source: {result.get('source')}")
        print(f"  count: {len(stocks)}")
        if stocks:
            for s in stocks[:5]:
                print(f"    {s['code']} {s.get('name','')} {s.get('industry','')} lbc={s.get('lian_ban_count',0)}")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 9. Multi-source fund flow
    print("\n[9] Multi-source fund_flow")
    try:
        from data_providers.sector_data import fetch_fund_flow_multi
        flow, source = fetch_fund_flow_multi()
        print(f"  source: {source}")
        print(f"  count: {len(flow)}")
        if flow:
            top5 = sorted(flow.items(), key=lambda x: x[1].get("mainNetIn", 0), reverse=True)[:5]
            for code, d in top5:
                print(f"    {code}: mainNetIn={d.get('mainNetIn',0):.0f} pct={d.get('mainNetInPct',0):.1f}%")
    except Exception as e:
        print(f"  ERROR: {e}")

    # 10. Existing klines endpoint (sanity check)
    print("\n[10] POST /klines (600519.SH, sanity)")
    try:
        r = _post("/klines", {"codes": ["600519.SH"], "period": "1d", "count": 3})
        print(f"  status: {r.get('status')}")
        kdata = r.get("data", {})
        print(f"  stocks returned: {len(kdata)}")
        for code, bars in kdata.items():
            print(f"  {code}: {len(bars)} bars, last={bars[-1] if bars else 'none'}")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
    except Exception as e:
        print(f"  ERROR: {e}")

    print("\n" + "=" * 60)
    print("Done!")


if __name__ == "__main__":
    main()
