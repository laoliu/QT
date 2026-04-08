"""
Pre-fetch script: pull all stock data from remote APIs and populate the SQLite cache.

Usage:
    python -m cache.prefetch          # fetch all
    python -m cache.prefetch --list   # stock list only
    python -m cache.prefetch --fund   # fundamentals only
    python -m cache.prefetch --names  # code-name map only
"""
from __future__ import annotations

import sys
import time
from typing import Any


def prefetch_stock_list() -> dict[str, dict[str, Any]]:
    """Fetch stock list from Sina and cache it."""
    from cache.stock_cache import get_cache

    cache = get_cache()

    # Check if already cached and fresh
    cached = cache.get_stock_list()
    if cached:
        print(f"[Ԥȡ] 股票列表已有缓存 ({len(cached)} ֻ)，跳过")
        return cached

    print("[Ԥȡ] 正在从 Sina 获取 A 股列表...")
    t0 = time.time()

    try:
        import akshare as ak
        df = ak.stock_zh_a_spot()
        stock_data: dict[str, dict[str, Any]] = {}

        for _, row in df.iterrows():
            raw_code = str(row.get("代码", ""))
            if not raw_code:
                continue
            code = raw_code.lower()
            prefix = ""
            if code.startswith(("sh", "sz", "bj")):
                prefix = code[:2]
                code = code[2:]

            if not code:
                continue

            record: dict[str, Any] = {"stock_id": code, "name": str(row.get("名称", ""))}
            for cn_col, en_key in {
                "最新价": "price", "涨跌额": "change_amt", "涨跌幅": "change_pct",
                "买入": "bid", "卖出": "ask", "昨收": "prev_close",
                "今开": "open", "最高": "high", "最低": "low",
                "成交量": "volume", "成交额": "turnover",
            }.items():
                val = row.get(cn_col)
                if val is not None:
                    try:
                        record[en_key] = float(val)
                    except (ValueError, TypeError):
                        record[en_key] = val

            if prefix == "sh":
                record["exchange"] = "SH"
            elif prefix == "sz":
                record["exchange"] = "SZ"
            elif prefix == "bj":
                record["exchange"] = "BJ"
            elif code.startswith("6"):
                record["exchange"] = "SH"
            elif code.startswith(("0", "3")):
                record["exchange"] = "SZ"
            else:
                record["exchange"] = "BJ"

            stock_data[code] = record

        elapsed = time.time() - t0
        print(f"[Ԥȡ] 获取到 {len(stock_data)} 只股票 ({elapsed:.1f}s)")

        cache.save_stock_list(stock_data)
        return stock_data

    except Exception as e:
        print(f"[Ԥȡ] Sina 获取失败: {e}，尝试 fallback...", file=sys.stderr)
        # Fallback to code-name list
        try:
            import akshare as ak
            df = ak.stock_info_a_code_name()
            stock_data = {}
            for _, row in df.iterrows():
                code = str(row.get("code", ""))
                name = str(row.get("name", ""))
                if not code or "ST" in name:
                    continue
                exchange = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ"
                stock_data[code] = {"stock_id": code, "name": name, "exchange": exchange}

            elapsed = time.time() - t0
            print(f"[Ԥȡ] Fallback: 获取到 {len(stock_data)} 只股票 ({elapsed:.1f}s)")
            cache.save_stock_list(stock_data)
            return stock_data
        except Exception as e2:
            print(f"[Ԥȡ] Fallback 也失败: {e2}", file=sys.stderr)
            return {}


def prefetch_code_name_map():
    """Fetch and cache code→name mapping."""
    from cache.stock_cache import get_cache

    cache = get_cache()

    # Check if already cached
    existing = cache.get_code_name_map()
    if existing:
        print(f"[Ԥȡ] 代码名称映射已有缓存 ({len(existing)} 条)，跳过")
        return

    print("[Ԥȡ] 正在获取代码名称映射...")
    t0 = time.time()

    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        mapping = {str(r["code"]): str(r["name"]) for _, r in df.iterrows()}
        elapsed = time.time() - t0
        print(f"[Ԥȡ] 获取到 {len(mapping)} 条映射 ({elapsed:.1f}s)")
        cache.save_code_name_map(mapping)
    except Exception as e:
        print(f"[Ԥȡ] 获取代码名称映射失败: {e}", file=sys.stderr)


def prefetch_fundamentals(stock_data: dict[str, dict[str, Any]], top_n: int = 500):
    """Fetch fundamentals for top stocks from Xueqiu and cache them."""
    from cache.stock_cache import get_cache
    from concurrent.futures import ThreadPoolExecutor, as_completed

    cache = get_cache()

    # Sort by turnover (most active first)
    sorted_ids = sorted(
        stock_data.keys(),
        key=lambda sid: stock_data.get(sid, {}).get("turnover", 0) or 0,
        reverse=True,
    )
    candidates = sorted_ids[:top_n]

    # Check cache for existing fundamentals
    cached_data, missing = cache.get_fundamentals(candidates)
    if cached_data:
        print(f"[Ԥȡ] 基本面缓存命中 {len(cached_data)} 只，还需获取 {len(missing)} ֻ")

    if not missing:
        print("[Ԥȡ] 基本面数据全部已缓存，跳过")
        return

    print(f"[Ԥȡ] 正在从雪球获取 {len(missing)} 只股票的基本面数据 (PE/PB/EPS)...")
    t0 = time.time()

    def _code_to_xq(code: str) -> str:
        if code.startswith("6"):
            return f"SH{code}"
        if code.startswith(("0", "3")):
            return f"SZ{code}"
        if code.startswith(("4", "8", "9")):
            return f"BJ{code}"
        return f"SH{code}"

    _XQ_ITEM_MAP = {
        "市盈率(动)": "pe", "市盈率(TTM)": "pe_ttm", "市盈率(静)": "pe_static",
        "市净率": "pb", "每股收益": "eps", "每股净资产": "bvps",
        "股息率(TTM)": "dividend_yield", "总市值": "total_market_val",
        "流通值": "circulating_market_val", "52周最高": "week52_high",
        "52周最低": "week52_low", "周转率": "turnover_rate",
        "行业": "industry", "上市时间": "listing_date",
    }

    def fetch_one(sid: str):
        import akshare as ak
        xq_sym = _code_to_xq(sid)
        try:
            df = ak.stock_individual_spot_xq(symbol=xq_sym)
            if df is None or df.empty:
                return sid, None
            result: dict[str, Any] = {}
            for _, row in df.iterrows():
                item = str(row.get("item", ""))
                value = row.get("value")
                if item in _XQ_ITEM_MAP and value is not None:
                    en_key = _XQ_ITEM_MAP[item]
                    try:
                        fval = float(value)
                        result[en_key] = fval
                        if en_key == "total_market_val":
                            result["total_market_val_yi"] = fval / 1e8
                        elif en_key == "circulating_market_val":
                            result["circulating_market_val_yi"] = fval / 1e8
                    except (ValueError, TypeError):
                        result[en_key] = str(value)
            return sid, result
        except Exception:
            return sid, None

    fetched: dict[str, dict[str, Any]] = {}
    errors = 0

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(fetch_one, sid): sid for sid in missing}
        done = 0
        for future in as_completed(futures):
            sid, result = future.result()
            done += 1
            if result:
                fetched[sid] = result
            else:
                errors += 1
            if done % 50 == 0:
                print(f"  ... {done}/{len(missing)} 完成 (成功 {len(fetched)}, 失败 {errors})")

    elapsed = time.time() - t0
    print(f"[Ԥȡ] 获取完成: {len(fetched)} 成功, {errors} 失败 ({elapsed:.1f}s)")

    if fetched:
        cache.save_fundamentals(fetched)


def main():
    import sys

    args = set(sys.argv[1:])
    do_all = not args or "--all" in args

    # Step 1: Code-name map (fast, needed by other functions)
    if do_all or "--names" in args:
        prefetch_code_name_map()

    # Step 2: Stock list
    stock_data = {}
    if do_all or "--list" in args:
        stock_data = prefetch_stock_list()

    # Step 3: Fundamentals (needs stock list)
    if do_all or "--fund" in args:
        if not stock_data:
            from cache.stock_cache import get_cache
            stock_data = get_cache().get_stock_list() or {}
        if stock_data:
            top_n = 500
            for a in args:
                if a.startswith("--top="):
                    try:
                        top_n = int(a.split("=")[1])
                    except ValueError:
                        pass
            prefetch_fundamentals(stock_data, top_n=top_n)
        else:
            print("[Ԥȡ] 没有股票列表数据，无法获取基本面数据")

    # Final stats
    from cache.stock_cache import get_cache
    stats = get_cache().stats()
    print("\n=== 缓存统计 ===")
    print(f"数据库: {stats['db_path']}")
    print(f"大小: {stats['db_size_mb']} MB")
    print(f"股票列表: {stats['stock_list']['unique_stocks']} ֻ / {stats['stock_list']['cached_dates']} 天")
    print(f"基本面:   {stats['fundamentals']['unique_stocks']} ֻ")
    print(f"K线:      {stats['klines']['total_bars']} 条 / {stats['klines']['unique_symbols']} ֻ")
    print(f"代码映射: {stats['code_name_map']['entries']} 条")


if __name__ == "__main__":
    main()
