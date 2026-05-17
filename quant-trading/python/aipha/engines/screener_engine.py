"""
Screener Engine — Python bridge for advanced stock screening strategies.

Orchestrates the full screening pipeline:
  1. Fetch stock universe (from factor_filter or direct codes)
  2. Enrich with financial data (financial_provider)
  3. Optionally attach K-line data (for technical/volume screeners)
  4. Run selected screener strategy
  5. Return structured results

Called from TypeScript via stdin JSON → stdout JSON protocol.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Optional


# Index code mapping for universe parameter
_INDEX_MAP: dict[str, tuple[str, str]] = {
    "hs300":  ("000300", "沪深300"),
    "zz500":  ("000905", "中证500"),
    "zz1000": ("000852", "中证1000"),
    "sz50":   ("000016", "上证50"),
    "cyb":    ("399006", "创业板指"),
    "kc50":   ("000688", "科创50"),
    "zz100":  ("000903", "中证100"),
}


def run_screener(config: dict) -> dict:
    """Run a screener strategy from JSON config.

    Input config:
    {
      "action": "run_screener",
      "screenerId": "canslim" | "fscore" | "magic_formula" | "technical" | "volume_price" | "peg",
      "market": "cn_stock" | "us_stock",
      "codes": ["600519", ...],  // optional: specific stocks to screen
      "universe": "hs300" | "zz500" | "zz1000" | "sz50" | "cyb" | "kc50" | "all",  // stock pool
      "limit": 20,
      "params": {},  // optional: screener-specific parameters
      "includeKlines": false,  // set true for technical/volume_price
      "klineDays": 120,  // K-line history days
    }

    Returns:
    {
      "screenerId": str,
      "screenerName": str,
      "results": [{"stock_id": ..., "name": ..., "score": ..., "factors": ...}],
      "count": int,
      "totalScreened": int,
      "errors": [...]
    }
    """
    errors: list[str] = []

    screener_id = config.get("screenerId", "")
    market = config.get("market", "cn_stock")
    codes = config.get("codes", [])
    universe = config.get("universe", "")  # e.g. hs300, zz500, sz50
    limit = config.get("limit", 20)
    params = config.get("params", {})
    include_klines = config.get("includeKlines", False)
    kline_days = config.get("klineDays", 120)

    # Auto-detect: technical, volume_price, overnight, and dragon_master need K-lines
    if screener_id in ("technical", "volume_price", "overnight", "dragon_master"):
        include_klines = True

    # When specific codes are given, disable screener min-score filters
    # so all requested stocks appear in results (just with lower scores)
    if codes:
        if "min_fscore" not in params:
            params["min_fscore"] = 0
        if "min_score" not in params:
            params["min_score"] = 0

    # Get screener instance
    from aipha.screeners.base import get_screener, list_screener_ids

    screener = get_screener(screener_id, params)
    if screener is None:
        available = list_screener_ids()
        return {
            "error": f"Unknown screener: '{screener_id}'. Available: {available}",
            "results": [],
            "count": 0,
        }

    # Step 1: Build stock universe
    stock_data: dict[str, dict[str, Any]] = {}

    if codes:
        # User provided specific codes — build minimal data
        for code in codes:
            stock_data[code] = {"stock_id": code, "name": code, "market": market}
    elif universe and universe != "all" and market == "cn_stock":
        # Fetch index constituents as stock pool
        print(f"正在获取 {universe} 指数成分股...", file=sys.stderr)
        stock_data, fetch_errors = _fetch_index_universe(universe)
        errors.extend(fetch_errors)
    elif market == "cn_stock":
        # Fetch full A-share universe from existing factor_filter
        print("正在获取 A 股全市场数据...", file=sys.stderr)
        stock_data, fetch_errors = _fetch_cn_universe()
        errors.extend(fetch_errors)
    else:
        errors.append(f"Non-code screening for {market} not yet supported. "
                       f"Please provide 'codes' for US/HK stocks.")

    if not stock_data:
        return {
            "screenerId": screener_id,
            "screenerName": screener.screener_name,
            "results": [],
            "count": 0,
            "totalScreened": 0,
            "errors": errors or ["无法获取股票数据"],
        }

    total_screened = len(stock_data)
    print(f"股票池: {total_screened} stocks", file=sys.stderr)

    # Step 2: Enrich with financial data
    # For fundamental screeners (canslim, fscore, magic_formula, peg), we need financials
    fundamental_screeners = {"canslim", "fscore", "magic_formula", "peg"}
    if screener_id in fundamental_screeners:
        print("正在获取财务数据...", file=sys.stderr)
        stock_data, fin_errors = _enrich_financials(stock_data, market, limit)
        errors.extend(fin_errors)

    # Step 3: Attach K-line data for technical screeners
    # klines_data: optional pre-fetched klines from external source (e.g. QMT bridge)
    # format: {stock_id_6digit: [{open, high, low, close, volume, date}, ...]}
    klines_data = config.get("klines_data")  # None → use akshare_provider as usual
    if include_klines:
        print("正在获取K线数据...", file=sys.stderr)
        stock_data, kl_errors = _attach_klines(stock_data, market, kline_days, klines_data)
        errors.extend(kl_errors)

    # Step 4: Run screener
    print(f"运行筛选策略: {screener.screener_name}...", file=sys.stderr)
    try:
        results = screener.screen(stock_data, market, limit)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {
            "screenerId": screener_id,
            "screenerName": screener.screener_name,
            "results": [],
            "count": 0,
            "totalScreened": total_screened,
            "errors": errors + [f"Screener error: {e}"],
        }

    # Step 5: Format results
    result_dicts = [r.to_dict() for r in results]

    response: dict[str, Any] = {
        "screenerId": screener_id,
        "screenerName": screener.screener_name,
        "results": result_dicts,
        "count": len(result_dicts),
        "totalScreened": total_screened,
    }
    if errors:
        response["errors"] = errors

    return response


def _fetch_cn_universe() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch A-share stock universe using factor_filter's data pipeline."""
    try:
        from aipha.engines.factor_filter import _fetch_cn_stock_data
        return _fetch_cn_stock_data()
    except Exception as e:
        return {}, [f"Failed to fetch CN universe: {e}"]


def _fetch_index_universe(universe: str) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch index constituents as stock pool (e.g. hs300, zz500).

    Data source cascade:
      1. Full market cache/cascade (fast) → filter by index member codes
      2. QMT /sector_members for index member list
      3. akshare index_stock_cons (original slow path)

    When the full market data is already cached, this resolves in <1s.
    """
    errors: list[str] = []
    stock_data: dict[str, dict[str, Any]] = {}

    index_info = _INDEX_MAP.get(universe)
    if not index_info:
        available = list(_INDEX_MAP.keys())
        return {}, [f"Unknown universe '{universe}'. Available: {available}"]

    index_code, index_name = index_info

    # ── Strategy A: Get member codes, then slice from full-market cache ──
    member_codes = _fetch_index_member_codes(universe, index_code, index_name)
    if member_codes:
        # Try to get full market data (cheaply — usually cached)
        full_data, _ = _fetch_cn_universe()
        if full_data:
            for code in member_codes:
                if code in full_data:
                    entry = dict(full_data[code])
                    entry["universe"] = universe
                    stock_data[code] = entry
            if stock_data:
                print(
                    f"[universe] {index_name}: {len(stock_data)}/{len(member_codes)} "
                    f"只成分股 (from cached full market)", file=sys.stderr,
                )
                return stock_data, []

        # Full market not available — build minimal entries from member codes
        for code in member_codes:
            stock_data[code] = {
                "stock_id": code,
                "name": code,
                "market": "cn_stock",
                "universe": universe,
            }
        print(f"[universe] {index_name}: {len(stock_data)} 只成分股 (codes only)", file=sys.stderr)
        return stock_data, []

    # ── Strategy B: akshare index_stock_cons (original fallback) ──
    try:
        import akshare as ak
        print(f"[universe] akshare index_stock_cons({index_code}) ...", file=sys.stderr)
        df = ak.index_stock_cons(symbol=index_code)
        if df is None or df.empty:
            return {}, [f"{index_name}({index_code}) 成分股数据为空"]

        code_col = df.columns[0]
        name_col = df.columns[1] if len(df.columns) > 1 else None

        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            name = str(row[name_col]).strip() if name_col else code
            stock_data[code] = {
                "stock_id": code,
                "name": name,
                "market": "cn_stock",
                "universe": universe,
            }

        print(f"[universe] {index_name}: {len(stock_data)} 只成分股", file=sys.stderr)
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        errors.append(f"Failed to fetch {index_name} constituents: {e}")

    return stock_data, errors


def _fetch_index_member_codes(universe: str, index_code: str, index_name: str) -> list[str]:
    """Get index member codes via fast sources (cache → QMT → TDX).

    Returns a list of 6-digit stock codes, or empty list on failure.
    """
    # ── 1. SQLite cache for index members ──
    try:
        from cache.stock_cache import get_cache
        cache = get_cache()
        cached = cache.get_meta(f"index_members:{universe}")
        if cached:
            import json as _json, time as _time
            data = _json.loads(cached)
            # TTL: 24 hours (index constituents change infrequently)
            if data.get("ts") and (_time.time() - data["ts"]) < 86400:
                codes = data.get("codes", [])
                if codes:
                    print(f"[universe] {index_name} 成分股命中缓存 ({len(codes)} ֻ)", file=sys.stderr)
                    return codes
    except Exception:
        pass

    def _save_member_cache(codes: list[str]) -> None:
        try:
            import json as _json, time as _time
            from cache.stock_cache import get_cache
            cache = get_cache()
            cache.set_meta(f"index_members:{universe}",
                           _json.dumps({"codes": codes, "ts": _time.time()}))
        except Exception:
            pass

    # ── 2. QMT bridge /sector_members ──
    try:
        from aipha.engines.factor_filter import _read_qmt_bridge_url
        import json as _json, urllib.request
        bridge_url = _read_qmt_bridge_url()
        if bridge_url:
            payload = _json.dumps({"sector": index_name}).encode()
            req = urllib.request.Request(
                f"{bridge_url}/sector_members",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = _json.loads(resp.read().decode())
                if result.get("status") == "ok" and result.get("data"):
                    # Normalize: strip exchange suffix (600519.SH → 600519)
                    codes = [c.split(".")[0] for c in result["data"] if c]
                    if codes:
                        print(f"[universe] {index_name}: QMT 返回 {len(codes)} 只成分股", file=sys.stderr)
                        _save_member_cache(codes)
                        return codes
    except Exception:
        pass

    # ── 3. akshare index_stock_cons (still faster than doing full screen) ──
    try:
        import akshare as ak
        df = ak.index_stock_cons(symbol=index_code)
        if df is not None and not df.empty:
            code_col = df.columns[0]
            codes = [str(row[code_col]).strip() for _, row in df.iterrows()]
            codes = [c for c in codes if c]
            if codes:
                print(f"[universe] {index_name}: akshare 返回 {len(codes)} 只成分股", file=sys.stderr)
                _save_member_cache(codes)
                return codes
    except Exception:
        pass

    return []


def _enrich_financials(
    stock_data: dict[str, dict[str, Any]],
    market: str,
    limit: int,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Enrich stock data with financial statement metrics.

    For CN stocks, tries data sources in priority order:
      0. JQData (聚宽, best quality, requires account)
      1. Batch PE/PB via East Money push2 (~8s for all)
      2. Batch financials via AKShare (业绩报表+资产负债表+现金流量表, ~20s for all)
      2b. baostock backfill for top candidates missing key fields
      3. baostock fallback if AKShare fails
      4. per-stock fetch_financials_batch as last resort
    """
    errors: list[str] = []
    codes = list(stock_data.keys())

    if market == "cn_stock":
        # ── Step 0: Try JQData batch (best quality, requires account) ──
        jq_user = os.environ.get("JQDATA_USERNAME", "")
        jq_pass = os.environ.get("JQDATA_PASSWORD", "")
        if jq_user and jq_pass:
            jq_ok = _enrich_financials_jqdata(stock_data, errors)
            if jq_ok:
                enriched_cnt = sum(
                    1 for c in codes
                    if stock_data.get(c, {}).get("roe") is not None
                )
                print(f"[financial] JQData 批量财报: {enriched_cnt}/{len(codes)} 只已补充财务数据", file=sys.stderr)
                return stock_data, errors
            print("[financial] JQData 失败，回退 AKShare ...", file=sys.stderr)

        # ── Step 1: Batch PE/PB via East Money push2 ──
        _enrich_pe_pb_eastmoney(stock_data)

        # ── Step 2: Batch financials via AKShare (bulk 三表) ──
        batch_ok = _enrich_financials_batch_akshare(stock_data, errors)
        if batch_ok:
            enriched_cnt = sum(
                1 for c in codes
                if stock_data.get(c, {}).get("roe") is not None
                or stock_data.get(c, {}).get("roa") is not None
            )
            print(f"[financial] AKShare 批量财报: {enriched_cnt}/{len(codes)} 只已补充财务数据", file=sys.stderr)

            # ── Step 2b: baostock backfill for top candidates missing key fields ──
            # Only backfill stocks likely to rank high (have ROE > 0) but missing
            # critical F-Score fields. Capped at limit to keep it fast (~30s max).
            _FSCORE_KEYS = {"roa", "operating_cf", "current_ratio"}
            missing_codes = []
            for c in codes:
                d = stock_data.get(c, {})
                # Only consider stocks that have some data but missing key derived fields
                if d.get("roe") is not None and d.get("roe", 0) > 0:
                    missing = [k for k in _FSCORE_KEYS if d.get(k) is None]
                    if missing and len(missing_codes) < limit:
                        missing_codes.append(c)
            if missing_codes:
                _enrich_financials_baostock(stock_data, missing_codes, errors)

            return stock_data, errors

        # ── Step 3 fallback: AKShare failed, try baostock for top candidates ──
        print("[financial] AKShare 批量失败，尝试 baostock 逐股获取", file=sys.stderr)
        scored_fallback = sorted(
            codes,
            key=lambda c: stock_data.get(c, {}).get("pe") or 999,
        )
        bs_codes = scored_fallback[:min(len(scored_fallback), limit * 3)]
        bs_cnt = _enrich_financials_baostock(stock_data, bs_codes, errors)
        if bs_cnt > 0:
            return stock_data, errors

        # ── Step 4 fallback: baostock also failed, try per-stock financial_provider ──
        print("[financial] baostock 也失败，回退 financial_provider 逐股获取", file=sys.stderr)

    # Fallback: pick top candidates by PE quality, enrich per-stock
    scored_codes = []
    for c in codes:
        d = stock_data.get(c, {})
        pe = d.get("pe") or d.get("pe_ttm") or 999
        roe_est = d.get("eps", 0) / max(d.get("bvps", 1), 0.01) * 100 if d.get("bvps") else 0
        q = (100 - min(pe, 100)) + roe_est
        scored_codes.append((c, q))
    scored_codes.sort(key=lambda x: x[1], reverse=True)

    max_fin = min(len(scored_codes), limit * 2)
    top_codes = [c for c, _ in scored_codes[:max_fin]]
    if top_codes:
        try:
            from aipha.data.providers.financial_provider import fetch_financials_batch, financial_to_dict
            delay = 1.0 if len(top_codes) <= 100 else 2.0
            fin_data = fetch_financials_batch(
                top_codes, market, max_workers=2, chunk_size=20, chunk_delay=delay
            )
            enriched = 0
            for code, snap in fin_data.items():
                if code in stock_data:
                    fin_dict = financial_to_dict(snap)
                    existing_name = stock_data[code].get("name", "")
                    stock_data[code].update(fin_dict)
                    if not stock_data[code].get("name") and existing_name:
                        stock_data[code]["name"] = existing_name
                    enriched += 1
            print(f"[financial] enriched {enriched} stocks with detailed financials", file=sys.stderr)
        except Exception as e:
            errors.append(f"Financial data fetch failed: {e}")
            traceback.print_exc(file=sys.stderr)

    return stock_data, errors


def _enrich_financials_batch_akshare(
    stock_data: dict[str, dict[str, Any]],
    errors: list[str],
) -> bool:
    """Batch-enrich ALL stocks with AKShare East Money financial statements.

    Fetches 3 batch APIs in parallel-ish:
      - stock_yjbb_em (业绩报表): ROE, gross margin, EPS, operating CF per share
      - stock_zcfz_em (资产负债表): total assets, total liabilities, debt ratio
      - stock_xjll_em (现金流量表): operating/investing/financing cash flow

    All 3 return ~5200 rows each, taking ~7s per call (~20s total).
    This replaces the old per-stock crawling that took 2-3 minutes for 40 stocks.
    """
    import time

    try:
        import akshare as ak
    except ImportError:
        errors.append("akshare not installed, cannot batch enrich")
        return False

    # Determine latest available report period (use trailing quarter)
    from datetime import datetime
    now = datetime.now()
    year = now.year
    month = now.month
    # 三季报 typically available by late Oct, 年报 by late Apr
    if month >= 11:
        report_date = f"{year}0930"     # 三季报
    elif month >= 5:
        report_date = f"{year - 1}1231" # 上年年报
    elif month >= 9:
        report_date = f"{year}0630"     # 中报
    else:
        report_date = f"{year - 1}0930" # 上年三季报

    print(f"[financial] AKShare 批量获取报告期 {report_date} ...", file=sys.stderr)
    t0 = time.time()

    # ── 1. 业绩报表 (key metrics: ROE, gross margin, EPS, operating CF) ──
    yjbb = None
    try:
        yjbb = ak.stock_yjbb_em(date=report_date)
        print(f"  [业绩报表] {len(yjbb)} 行 ({time.time()-t0:.1f}s)", file=sys.stderr)
    except Exception as e:
        errors.append(f"stock_yjbb_em failed: {e}")
        print(f"  [业绩报表] 失败: {e}", file=sys.stderr)

    # ── 2. 资产负债表 (total assets, liabilities, debt ratio) ──
    zcfz = None
    try:
        t1 = time.time()
        zcfz = ak.stock_zcfz_em(date=report_date)
        print(f"  [资产负债表] {len(zcfz)} 行 ({time.time()-t1:.1f}s)", file=sys.stderr)
    except Exception as e:
        errors.append(f"stock_zcfz_em failed: {e}")
        print(f"  [资产负债表] 失败: {e}", file=sys.stderr)

    # ── 3. 现金流量表 (operating cash flow) ──
    xjll = None
    try:
        t2 = time.time()
        xjll = ak.stock_xjll_em(date=report_date)
        print(f"  [现金流量表] {len(xjll)} 行 ({time.time()-t2:.1f}s)", file=sys.stderr)
    except Exception as e:
        errors.append(f"stock_xjll_em failed: {e}")
        print(f"  [现金流量表] 失败: {e}", file=sys.stderr)

    if yjbb is None and zcfz is None and xjll is None:
        return False

    # Build lookup dicts by stock code
    def _build_lookup(df, code_col: str = "股票代码") -> dict[str, dict]:
        if df is None or df.empty:
            return {}
        lk: dict[str, dict] = {}
        for _, row in df.iterrows():
            code = str(row.get(code_col, "")).strip()
            if code:
                lk[code] = row.to_dict()
        return lk

    yjbb_lk = _build_lookup(yjbb)
    zcfz_lk = _build_lookup(zcfz)
    xjll_lk = _build_lookup(xjll)

    def _sf(v: Any) -> Optional[float]:
        """Safe float conversion, None for NaN/invalid."""
        if v is None:
            return None
        try:
            import math
            f = float(v)
            return None if math.isnan(f) else f
        except (ValueError, TypeError):
            return None

    enriched = 0
    codes = list(stock_data.keys())
    for code in codes:
        d = stock_data[code]
        yj = yjbb_lk.get(code, {})
        zc = zcfz_lk.get(code, {})
        xj = xjll_lk.get(code, {})

        if not yj and not zc and not xj:
            continue

        # ── From 业绩报表 ──
        if yj:
            roe = _sf(yj.get("净资产收益率"))
            if roe is not None:
                d["roe"] = roe

            eps = _sf(yj.get("每股收益"))
            if eps is not None:
                d["eps"] = eps

            bvps = _sf(yj.get("每股净资产"))
            if bvps is not None:
                d["bvps"] = bvps

            gross_margin = _sf(yj.get("销售毛利率"))
            if gross_margin is not None:
                d["gross_margin"] = gross_margin

            ocf_ps = _sf(yj.get("每股经营现金流量"))
            if ocf_ps is not None:
                d["ocf_per_share"] = ocf_ps

            net_profit = _sf(yj.get("净利润-净利润"))
            if net_profit is not None:
                d["net_profit"] = net_profit

            revenue = _sf(yj.get("营业总收入-营业总收入"))
            if revenue is not None:
                d["revenue"] = revenue

            rev_growth = _sf(yj.get("营业总收入-同比增长"))
            if rev_growth is not None:
                d["revenue_growth_yoy"] = rev_growth

            np_growth = _sf(yj.get("净利润-同比增长"))
            if np_growth is not None:
                d["net_profit_growth_yoy"] = np_growth

            name = yj.get("股票简称")
            if name and not d.get("name"):
                d["name"] = str(name)

        # ── From 资产负债表 ──
        if zc:
            total_assets = _sf(zc.get("资产-总资产"))
            total_liab = _sf(zc.get("负债-总负债"))
            equity = _sf(zc.get("股东权益合计"))

            if total_assets is not None:
                d["total_assets"] = total_assets

            debt_ratio = _sf(zc.get("资产负债率"))
            if debt_ratio is not None:
                d["debt_ratio"] = debt_ratio
            elif total_assets and total_liab:
                d["debt_ratio"] = total_liab / total_assets * 100

            if total_liab is not None and equity and equity > 0:
                d["debt_to_equity"] = total_liab / equity * 100

            # Compute ROA = net_profit / total_assets * 100
            np_val = d.get("net_profit")
            if np_val is not None and total_assets and total_assets > 0:
                d["roa"] = np_val / total_assets * 100

            # Compute asset_turnover = revenue / total_assets
            rev_val = d.get("revenue")
            if rev_val is not None and total_assets and total_assets > 0:
                d["asset_turnover"] = rev_val / total_assets

        # ── From 现金流量表 ──
        if xj:
            operating_cf = _sf(xj.get("经营性现金流-现金流量净额"))
            if operating_cf is not None:
                d["operating_cf"] = operating_cf

            free_cf = None
            inv_cf = _sf(xj.get("投资性现金流-现金流量净额"))
            if operating_cf is not None and inv_cf is not None:
                free_cf = operating_cf + inv_cf
                d["free_cash_flow"] = free_cf

        # ── Derived: ocf_to_net_profit ──
        ocf = d.get("operating_cf")
        np2 = d.get("net_profit")
        if ocf is not None and np2 is not None and np2 != 0:
            d["ocf_to_net_profit"] = ocf / abs(np2)

        # ── Derived: current_ratio (simple estimate from balance sheet) ──
        # stock_zcfz_em doesn't give current assets/liabilities directly.
        # Use cash + receivables + inventory vs payables as rough proxy.
        if zc:
            cash = _sf(zc.get("资产-货币资金")) or 0
            recv = _sf(zc.get("资产-应收账款")) or 0
            inv = _sf(zc.get("资产-存货")) or 0
            payable = _sf(zc.get("负债-应付账款")) or 0
            prepaid = _sf(zc.get("负债-预收账款")) or 0
            current_assets_est = cash + recv + inv
            current_liab_est = payable + prepaid
            if current_liab_est > 0:
                d["current_ratio"] = current_assets_est / current_liab_est

        # ── shares_outstanding (from PE + EPS or market cap) ──
        pe_val = d.get("pe") or d.get("pe_ttm")
        eps_val = d.get("eps")
        mktcap = d.get("mktcap")
        price = d.get("close") or d.get("price")
        if not d.get("shares_outstanding"):
            if mktcap and price and price > 0:
                d["shares_outstanding"] = mktcap / price
            elif pe_val and eps_val and eps_val > 0 and price and price > 0:
                implied_earnings = price / pe_val
                if abs(implied_earnings - eps_val) / eps_val < 0.5:
                    # Rough match, compute shares
                    np3 = d.get("net_profit")
                    if np3 and np3 > 0 and eps_val > 0:
                        d["shares_outstanding"] = np3 / eps_val

        enriched += 1

    elapsed = time.time() - t0
    print(f"[financial] AKShare 批量完成: {enriched}/{len(codes)} ֻ ({elapsed:.1f}s)", file=sys.stderr)
    return enriched > 0


def _enrich_financials_jqdata(
    stock_data: dict[str, dict[str, Any]],
    errors: list[str],
) -> bool:
    """Batch-enrich ALL stocks with JQData (聚宽) financial indicators.

    Uses jqdatasdk `get_fundamentals` to query:
      - indicator table: ROE, ROA, gross margin, net margin, EPS, operating CF/share
      - valuation table: PE, PB, market cap, turnover ratio
      - balance table: total assets, total liabilities
      - cash_flow table: net operating CF

    Single API call returns up to 5000 rows. Requires JQDATA_USERNAME/JQDATA_PASSWORD env.
    """
    import time

    username = os.environ.get("JQDATA_USERNAME", "")
    password = os.environ.get("JQDATA_PASSWORD", "")
    if not username or not password:
        return False

    try:
        import jqdatasdk as jq
    except ImportError:
        errors.append("jqdatasdk not installed")
        return False

    # Suppress jqdatasdk auth output
    import io as _io
    _old_stdout = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        jq.auth(username, password)
    except Exception as e:
        sys.stdout = _old_stdout
        errors.append(f"JQData auth failed: {e}")
        print(f"[jqdata] 认证失败: {e}", file=sys.stderr)
        return False
    finally:
        sys.stdout = _old_stdout

    print("[jqdata] 认证成功, 批量获取财务数据 ...", file=sys.stderr)
    t0 = time.time()

    # Find latest available trading day (trial accounts may have data delay)
    from datetime import datetime, timedelta
    from jqdatasdk import valuation, indicator, balance, cash_flow, query

    today_str = datetime.now().strftime("%Y-%m-%d")
    query_date = today_str

    # Probe for the latest date with actual data (trial accounts lag ~3 months)
    probe_q = query(valuation.code).limit(1)
    probe_df = jq.get_fundamentals(probe_q, date=query_date)
    if probe_df is None or probe_df.empty:
        # Binary search backwards to find data boundary
        for days_back in [7, 30, 60, 90, 120, 180, 365]:
            test_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            probe_df = jq.get_fundamentals(probe_q, date=test_date)
            if probe_df is not None and not probe_df.empty:
                query_date = test_date
                print(f"[jqdata] 试用账号数据延迟, 使用 {query_date} 的数据", file=sys.stderr)
                break
        else:
            errors.append("JQData: no data available for any recent date")
            print("[jqdata] 无可用数据", file=sys.stderr)
            return False

    def _sf(v: Any) -> Optional[float]:
        if v is None:
            return None
        try:
            import math
            f = float(v)
            return None if math.isnan(f) else f
        except (ValueError, TypeError):
            return None

    # Convert 6-digit codes to JQ format: 600519 → 600519.XSHG
    def _to_jq(code: str) -> str:
        if code.startswith("6"):
            return f"{code}.XSHG"
        elif code.startswith(("0", "3")):
            return f"{code}.XSHE"
        else:
            return f"{code}.XSHE"  # BJ stocks fallback

    codes = list(stock_data.keys())
    jq_codes = [_to_jq(c) for c in codes]

    enriched = 0

    try:
        # ── 1. Valuation data (PE/PB/market cap) — latest trading day ──

        # Query valuation for all stocks (auto-paginated by jqdatasdk)
        q_val = query(
            valuation.code,
            valuation.pe_ratio,        # PE (TTM)
            valuation.pb_ratio,        # PB
            valuation.market_cap,      # 总市值 (亿)
            valuation.circulating_market_cap,  # 流通市值
            valuation.turnover_ratio,  # 换手率
        ).filter(valuation.code.in_(jq_codes))

        df_val = jq.get_fundamentals(q_val, date=query_date)
        t1 = time.time()
        print(f"  [估值] {len(df_val)} 行 ({t1-t0:.1f}s)", file=sys.stderr)

        # Build valuation lookup
        val_lk: dict[str, dict] = {}
        if df_val is not None and not df_val.empty:
            for _, row in df_val.iterrows():
                jq_code = str(row.get("code", ""))
                short = jq_code.split(".")[0]
                val_lk[short] = row.to_dict()

        # ── 2. Financial indicators (ROE/ROA/margins/EPS) — latest report ──
        q_ind = query(
            indicator.code,
            indicator.roe,                  # ROE
            indicator.roa,                  # ROA
            indicator.gross_profit_margin,  # 毛利率
            indicator.net_profit_margin,    # 净利率
            indicator.eps,                  # EPS
            indicator.operating_profit,     # 营业利润
            indicator.inc_revenue_year_on_year,     # 营收同比
            indicator.inc_net_profit_year_on_year,  # 净利润同比
        ).filter(indicator.code.in_(jq_codes))

        df_ind = jq.get_fundamentals(q_ind, date=query_date)
        t2 = time.time()
        print(f"  [财务指标] {len(df_ind)} 行 ({t2-t1:.1f}s)", file=sys.stderr)

        ind_lk: dict[str, dict] = {}
        if df_ind is not None and not df_ind.empty:
            for _, row in df_ind.iterrows():
                jq_code = str(row.get("code", ""))
                short = jq_code.split(".")[0]
                ind_lk[short] = row.to_dict()

        # ── 3. Balance sheet (total assets, liabilities, equity) ──
        q_bal = query(
            balance.code,
            balance.total_assets,
            balance.total_liability,
            balance.total_owner_equities,
            balance.total_current_assets,
            balance.total_current_liability,
        ).filter(balance.code.in_(jq_codes))

        df_bal = jq.get_fundamentals(q_bal, date=query_date)
        t3 = time.time()
        print(f"  [资产负债] {len(df_bal)} 行 ({t3-t2:.1f}s)", file=sys.stderr)

        bal_lk: dict[str, dict] = {}
        if df_bal is not None and not df_bal.empty:
            for _, row in df_bal.iterrows():
                jq_code = str(row.get("code", ""))
                short = jq_code.split(".")[0]
                bal_lk[short] = row.to_dict()

        # ── 4. Cash flow (operating CF) ──
        q_cf = query(
            cash_flow.code,
            cash_flow.net_operate_cash_flow,
            cash_flow.net_invest_cash_flow,
            cash_flow.net_finance_cash_flow,
        ).filter(cash_flow.code.in_(jq_codes))

        df_cf = jq.get_fundamentals(q_cf, date=query_date)
        t4 = time.time()
        print(f"  [现金流] {len(df_cf)} 行 ({t4-t3:.1f}s)", file=sys.stderr)

        cf_lk: dict[str, dict] = {}
        if df_cf is not None and not df_cf.empty:
            for _, row in df_cf.iterrows():
                jq_code = str(row.get("code", ""))
                short = jq_code.split(".")[0]
                cf_lk[short] = row.to_dict()

    except Exception as e:
        errors.append(f"JQData query failed: {e}")
        print(f"[jqdata] 查询失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return False

    # ── Merge into stock_data ──
    for code in codes:
        d = stock_data[code]
        val = val_lk.get(code, {})
        ind = ind_lk.get(code, {})
        bal = bal_lk.get(code, {})
        cf = cf_lk.get(code, {})

        if not val and not ind and not bal and not cf:
            continue

        # Valuation
        pe = _sf(val.get("pe_ratio"))
        if pe is not None and d.get("pe") is None:
            d["pe"] = pe
            d["pe_ttm"] = pe

        pb = _sf(val.get("pb_ratio"))
        if pb is not None and d.get("pb") is None:
            d["pb"] = pb

        mktcap = _sf(val.get("market_cap"))
        if mktcap is not None:
            d["total_market_val_yi"] = mktcap
            d["mktcap"] = mktcap * 1e8  # 亿 → Ԫ

        # Indicators
        roe = _sf(ind.get("roe"))
        if roe is not None:
            d["roe"] = roe

        roa = _sf(ind.get("roa"))
        if roa is not None:
            d["roa"] = roa

        gm = _sf(ind.get("gross_profit_margin"))
        if gm is not None:
            d["gross_margin"] = gm

        nm = _sf(ind.get("net_profit_margin"))
        if nm is not None:
            d["net_margin"] = nm

        eps = _sf(ind.get("eps"))
        if eps is not None:
            d["eps"] = eps

        rev_growth = _sf(ind.get("inc_revenue_year_on_year"))
        if rev_growth is not None:
            d["revenue_growth_yoy"] = rev_growth

        np_growth = _sf(ind.get("inc_net_profit_year_on_year"))
        if np_growth is not None:
            d["net_profit_growth_yoy"] = np_growth

        # Balance sheet
        total_assets = _sf(bal.get("total_assets"))
        total_liab = _sf(bal.get("total_liability"))
        equity = _sf(bal.get("total_owner_equities"))
        cur_assets = _sf(bal.get("total_current_assets"))
        cur_liab = _sf(bal.get("total_current_liability"))

        if total_assets:
            d["total_assets"] = total_assets

        if total_assets and total_liab:
            d["debt_ratio"] = total_liab / total_assets * 100

        if total_liab and equity and equity > 0:
            d["debt_to_equity"] = total_liab / equity * 100

        if cur_assets and cur_liab and cur_liab > 0:
            d["current_ratio"] = cur_assets / cur_liab

        # Derive ROA if not from indicator
        if d.get("roa") is None and d.get("net_profit") and total_assets and total_assets > 0:
            d["roa"] = d["net_profit"] / total_assets * 100

        # Asset turnover
        if d.get("revenue") and total_assets and total_assets > 0:
            d["asset_turnover"] = d["revenue"] / total_assets

        # Cash flow
        operating_cf = _sf(cf.get("net_operate_cash_flow"))
        if operating_cf is not None:
            d["operating_cf"] = operating_cf

        inv_cf = _sf(cf.get("net_invest_cash_flow"))
        if operating_cf is not None and inv_cf is not None:
            d["free_cash_flow"] = operating_cf + inv_cf

        # ocf_to_net_profit
        np_val = d.get("net_profit")
        if operating_cf is not None and np_val and np_val != 0:
            d["ocf_to_net_profit"] = operating_cf / abs(np_val)

        # shares_outstanding from market cap
        price = d.get("close") or d.get("price")
        if not d.get("shares_outstanding") and d.get("mktcap") and price and price > 0:
            d["shares_outstanding"] = d["mktcap"] / price

        enriched += 1

    elapsed = time.time() - t0
    print(f"[jqdata] 批量完成: {enriched}/{len(codes)} ֻ ({elapsed:.1f}s)", file=sys.stderr)
    return enriched > 0


def _enrich_financials_baostock(
    stock_data: dict[str, dict[str, Any]],
    codes: list[str],
    errors: list[str],
) -> int:
    """Fallback: enrich stocks with baostock per-stock financial queries.

    baostock is free, requires no registration or token.
    Fields: ROE, ROA (derived), gross margin, net margin, current ratio,
    debt ratio, net profit growth, revenue growth, operating CF ratio.

    Only used for stocks missing critical F-Score fields after AKShare.
    ~0.15s per stock, so limited to top N candidates.
    """
    import time

    try:
        import baostock as bs
    except ImportError:
        errors.append("baostock not installed")
        return 0

    # Determine latest report period
    from datetime import datetime
    now = datetime.now()
    year = now.year
    month = now.month
    if month >= 11:
        q_year, quarter = year, 3
    elif month >= 5:
        q_year, quarter = year - 1, 4
    elif month >= 9:
        q_year, quarter = year, 2
    else:
        q_year, quarter = year - 1, 3

    print(f"[baostock] 补充 {len(codes)} ֻ ({q_year}Q{quarter}) ...", file=sys.stderr)
    t0 = time.time()

    # Suppress baostock's print to stdout (corrupts JSON output)
    import io as _io
    _old_stdout = sys.stdout
    sys.stdout = _io.StringIO()
    try:
        lg = bs.login()
    finally:
        sys.stdout = _old_stdout
    if lg.error_code != "0":
        errors.append(f"baostock login failed: {lg.error_msg}")
        return 0

    def _to_bs_code(code: str) -> str:
        if code.startswith("6"):
            return f"sh.{code}"
        return f"sz.{code}"

    def _sf(v: str) -> Optional[float]:
        if not v or v == "":
            return None
        try:
            import math
            f = float(v)
            return None if math.isnan(f) else f
        except (ValueError, TypeError):
            return None

    enriched = 0
    for code in codes:
        d = stock_data.get(code, {})
        bs_code = _to_bs_code(code)

        try:
            # ── Profit data: ROE, margins, EPS, net profit ──
            rs = bs.query_profit_data(code=bs_code, year=q_year, quarter=quarter)
            if rs.error_code == "0" and rs.next():
                row = rs.get_row_data()
                fields = rs.fields
                row_dict = dict(zip(fields, row))

                roe = _sf(row_dict.get("roeAvg"))
                if roe is not None and d.get("roe") is None:
                    d["roe"] = roe * 100  # baostock returns decimal

                gm = _sf(row_dict.get("gpMargin"))
                if gm is not None and d.get("gross_margin") is None:
                    d["gross_margin"] = gm * 100

                nm = _sf(row_dict.get("npMargin"))
                if nm is not None and d.get("net_margin") is None:
                    d["net_margin"] = nm * 100

                np_val = _sf(row_dict.get("netProfit"))
                if np_val is not None and d.get("net_profit") is None:
                    d["net_profit"] = np_val

                eps_ttm = _sf(row_dict.get("epsTTM"))
                if eps_ttm is not None and d.get("eps") is None:
                    d["eps"] = eps_ttm

                total_share = _sf(row_dict.get("totalShare"))
                if total_share is not None and d.get("shares_outstanding") is None:
                    d["shares_outstanding"] = total_share

            # ── Growth data: YOY net profit, equity ──
            rs2 = bs.query_growth_data(code=bs_code, year=q_year, quarter=quarter)
            if rs2.error_code == "0" and rs2.next():
                row2 = rs2.get_row_data()
                fields2 = rs2.fields
                rd2 = dict(zip(fields2, row2))

                yoy_ni = _sf(rd2.get("YOYNI"))
                if yoy_ni is not None and d.get("net_profit_growth_yoy") is None:
                    d["net_profit_growth_yoy"] = yoy_ni * 100

                yoy_asset = _sf(rd2.get("YOYAsset"))
                if yoy_asset is not None and d.get("revenue_growth_yoy") is None:
                    # YOYAsset is total asset growth, not revenue; best approximation
                    pass

            # ── Balance data: current ratio, debt ratio ──
            rs3 = bs.query_balance_data(code=bs_code, year=q_year, quarter=quarter)
            if rs3.error_code == "0" and rs3.next():
                row3 = rs3.get_row_data()
                fields3 = rs3.fields
                rd3 = dict(zip(fields3, row3))

                cr = _sf(rd3.get("currentRatio"))
                if cr is not None and d.get("current_ratio") is None:
                    d["current_ratio"] = cr

                lia = _sf(rd3.get("liabilityToAsset"))
                if lia is not None and d.get("debt_ratio") is None:
                    d["debt_ratio"] = lia * 100

            # ── Cash flow data: OCF to net profit ──
            rs4 = bs.query_cash_flow_data(code=bs_code, year=q_year, quarter=quarter)
            if rs4.error_code == "0" and rs4.next():
                row4 = rs4.get_row_data()
                fields4 = rs4.fields
                rd4 = dict(zip(fields4, row4))

                cfo_np = _sf(rd4.get("CFOToNP"))
                if cfo_np is not None and d.get("ocf_to_net_profit") is None:
                    d["ocf_to_net_profit"] = cfo_np

                cfo_or = _sf(rd4.get("CFOToOR"))
                if cfo_or is not None and d.get("operating_cf") is None:
                    # Derive operating_cf from ratio + net_profit
                    np2 = d.get("net_profit")
                    if np2 and np2 > 0:
                        d["operating_cf"] = cfo_np * np2 if cfo_np else None

                # ROA: derive from net_profit and total_assets or use netProfit/totalAssets
                if d.get("roa") is None and d.get("net_profit") and d.get("total_assets"):
                    d["roa"] = d["net_profit"] / d["total_assets"] * 100

            enriched += 1
        except Exception:
            pass

    try:
        _old_stdout = sys.stdout
        sys.stdout = _io.StringIO()
        try:
            bs.logout()
        finally:
            sys.stdout = _old_stdout
    except Exception:
        pass

    elapsed = time.time() - t0
    print(f"[baostock] 补充完成: {enriched}/{len(codes)} ֻ ({elapsed:.1f}s)", file=sys.stderr)
    return enriched


def _attach_klines(
    stock_data: dict[str, dict[str, Any]],
    market: str,
    kline_days: int,
    klines_data: dict[str, list[dict]] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Attach K-line history data to stock_data for technical analysis.

    Args:
        klines_data: Optional pre-fetched klines keyed by 6-digit stock_id.
                     When provided, skips akshare fetching and injects directly.
                     Format: {"600519": [{open, high, low, close, volume, date}, ...]}
    """
    errors: list[str] = []

    # Fast path: inject pre-fetched klines (e.g., from QMT bridge)
    if klines_data is not None:
        attached = 0
        for stock_id, info in stock_data.items():
            bars = klines_data.get(stock_id)
            if bars:
                info["klines"] = bars
                attached += 1
        print(f"[K线注入] 外部数据: {attached}/{len(stock_data)} 只股票已注入K线", file=sys.stderr)
        return stock_data, errors

    # Default path: fetch klines
    from datetime import datetime, timedelta

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=kline_days + 30)).strftime("%Y-%m-%d")  # Buffer

    codes = list(stock_data.keys())

    # Use existing kline fetching (with cache)
    try:
        if market == "cn_stock":
            # Try Tushare first (faster, no 300-code limit)
            tushare_token = os.environ.get("TUSHARE_TOKEN", "")
            if tushare_token:
                attached = _attach_klines_tushare(
                    stock_data, codes, tushare_token, start_date, end_date
                )
                if attached > 0:
                    print(f"[klines] Tushare: attached {attached}/{len(codes)} stocks with K-line data", file=sys.stderr)
                    return stock_data, errors

            # Batch fetch: cache → QMT batch → per-stock fallback (TDX/EastMoney)
            from aipha.data.providers.akshare_provider import get_klines_batch

            _MAIN_PREFIXES = ("000", "001", "002", "003", "300", "301",
                              "600", "601", "603", "605", "688")
            main_codes = [c for c in codes if c.startswith(_MAIN_PREFIXES)]
            other_codes = [c for c in codes if not c.startswith(_MAIN_PREFIXES)]
            prioritized = main_codes + other_codes

            max_fetch = 300
            fetch_codes = prioritized[:max_fetch]

            batch_result = get_klines_batch(
                fetch_codes, start_date, end_date, market, kline_days
            )
            attached = 0
            for code, bars in batch_result.items():
                if bars and code in stock_data:
                    stock_data[code]["klines"] = bars
                    attached += 1
            print(f"[klines] batch attached {attached}/{len(fetch_codes)} stocks with K-line data", file=sys.stderr)
        else:
            from aipha.data.providers.provider_factory import fetch_klines_batch
            batch_result = fetch_klines_batch(codes[:100], start_date, end_date, market)
            attached = 0
            for code, df in batch_result.items():
                if df is not None and not df.empty:
                    bars = []
                    for idx, row in df.iterrows():
                        bars.append({
                            "timestamp": int(idx.timestamp() * 1000),
                            "open": float(row.get("open", 0)),
                            "high": float(row.get("high", 0)),
                            "low": float(row.get("low", 0)),
                            "close": float(row.get("close", 0)),
                            "volume": int(row.get("volume", 0)),
                        })
                    stock_data[code]["klines"] = bars
                    attached += 1
            print(f"[klines] attached {attached} US stocks with K-line data", file=sys.stderr)

    except Exception as e:
        errors.append(f"K-line fetch failed: {e}")
        traceback.print_exc(file=sys.stderr)

    return stock_data, errors


def _enrich_pe_pb_eastmoney(stock_data: dict[str, dict[str, Any]]) -> bool:
    """Batch-fetch PE/PB/market-cap for all A-shares via East Money push2 API.

    Single API, paginated (100/page), typically completes in ~8s for 5000+ stocks.
    Updates stock_data in-place. Returns True if data was successfully fetched.
    """
    import json
    import urllib.request

    _EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    _EM_FIELDS = "f12,f9,f23,f20,f21,f115"
    # f12=code, f9=PE(动), f23=PB, f20=total_mktcap, f21=circ_mktcap, f115=PE_TTM

    def _safe_float(v: Any) -> Optional[float]:
        try:
            f = float(v)
            return f if f != 0 else None
        except (ValueError, TypeError):
            return None

    try:
        print("[PE/PB] 东方财富批量获取 ...", file=sys.stderr)
        enriched = 0
        for page in range(1, 65):  # max 6400 stocks
            url = (
                f"https://push2.eastmoney.com/api/qt/clist/get?"
                f"np=1&fltt=2&invt=2&fid=f3&fs={_EM_FS}"
                f"&fields={_EM_FIELDS}&pn={page}&pz=100&po=1"
            )
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                inner = data.get("data")
                if not inner:
                    break
                items = inner.get("diff") or []
                if not items:
                    break
                for item in items:
                    code = str(item.get("f12", ""))
                    if code in stock_data:
                        pe = _safe_float(item.get("f9"))
                        pb = _safe_float(item.get("f23"))
                        pe_ttm = _safe_float(item.get("f115"))
                        mktcap = _safe_float(item.get("f20"))
                        nmc = _safe_float(item.get("f21"))
                        if pe is not None:
                            stock_data[code]["pe"] = pe
                        if pb is not None:
                            stock_data[code]["pb"] = pb
                        if pe_ttm is not None:
                            stock_data[code]["pe_ttm"] = pe_ttm
                        if mktcap is not None:
                            stock_data[code]["mktcap"] = mktcap
                        if nmc is not None:
                            stock_data[code]["nmc"] = nmc
                        if pe is not None or pb is not None:
                            enriched += 1

        print(f"[PE/PB] 东方财富: {enriched} 只股票已补充PE/PB", file=sys.stderr)
        return enriched > 0
    except Exception as e:
        print(f"[PE/PB] 东方财富失败: {e}", file=sys.stderr)
        return False


def _attach_klines_tushare(
    stock_data: dict[str, dict[str, Any]],
    codes: list[str],
    token: str,
    start_date: str,
    end_date: str,
) -> int:
    """Fetch K-line data via Tushare Pro daily() and attach to stock_data.

    Unlike akshare, tushare can batch-fetch by trade_date (all stocks at once),
    making it much faster for full-market screening.
    Returns the number of stocks with klines attached.
    """
    try:
        import tushare as ts
        ts.set_token(token)
        pro = ts.pro_api()

        start_fmt = start_date.replace("-", "")
        end_fmt = end_date.replace("-", "")

        # Build ts_code mapping: 600519 → 600519.SH
        code_to_ts: dict[str, str] = {}
        ts_to_code: dict[str, str] = {}
        for code in codes:
            if code.startswith("6"):
                ts_code = f"{code}.SH"
            elif code.startswith(("0", "3")):
                ts_code = f"{code}.SZ"
            else:
                ts_code = f"{code}.BJ"
            code_to_ts[code] = ts_code
            ts_to_code[ts_code] = code

        # Fetch daily data for all codes in batches (tushare limits per-call results)
        # Strategy: fetch by date range for each code individually but in parallel-safe way
        # More efficient: use trade_date to fetch all stocks for each day
        # But for kline history, per-stock fetch is more practical
        attached = 0
        max_fetch = min(len(codes), 500)
        for code in codes[:max_fetch]:
            try:
                ts_code = code_to_ts.get(code)
                if not ts_code:
                    continue
                df = pro.daily(ts_code=ts_code, start_date=start_fmt, end_date=end_fmt)
                if df is None or df.empty:
                    continue
                bars = []
                for _, row in df.iterrows():
                    trade_date = str(row["trade_date"])
                    bars.append({
                        "date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["vol"]),
                    })
                bars.reverse()  # tushare returns newest first
                if bars:
                    stock_data[code]["klines"] = bars
                    attached += 1
            except Exception:
                pass

        return attached
    except Exception as e:
        print(f"[klines] Tushare K线获取失败: {e}", file=sys.stderr)
        return 0


def list_available_screeners() -> dict:
    """List all registered screeners."""
    from aipha.screeners.base import get_all_screeners
    screeners = get_all_screeners()
    return {
        "screeners": [
            {
                "id": cls.screener_id,
                "name": cls.screener_name,
                "description": cls.screener_description,
                "supported_markets": cls.supported_markets,
            }
            for cls in screeners.values()
        ],
        "count": len(screeners),
    }


# ── CLI Entry ───────────────────────────────────────────────────

if __name__ == "__main__":
    # Ensure UTF-8 output on Windows (for ✓/✗ characters)
    sys.stdout.reconfigure(encoding="utf-8")

    config = json.loads(sys.stdin.read())
    action = config.get("action", "run_screener")

    if action == "list_screeners":
        result = list_available_screeners()
    elif action == "run_screener":
        result = run_screener(config)
    else:
        result = {"error": f"Unknown action: {action}. Use 'run_screener' or 'list_screeners'."}

    print(json.dumps(result, ensure_ascii=False, default=str))
