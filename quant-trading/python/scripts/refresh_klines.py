#!/usr/bin/env python3
"""
Batch refresh all A-share K-line data.

Usage:
    cd extensions/quant-trading/python
    python3 scripts/refresh_klines.py [--max N]

Data sources (auto-selected):
  1. Windows xtdata bridge (fast, batch mode, ~100 stocks/request)
  2. akshare eastmoney API (slower, per-stock, no Windows needed)

This script:
  1. Reads all stock codes from the stock list cache
  2. Checks which ones have stale K-line data (latest bar >2 days old)
  3. Fetches fresh K-lines from bridge or akshare
  4. Saves everything to SQLite cache
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# Add parent dir to path so we can import project modules
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

WINDOWS_XTDATA_URL = os.environ.get("WINDOWS_XTDATA_URL", "http://127.0.0.1:58088")
KLINE_DAYS = 120
BATCH_SIZE = 100
TIMEOUT = 90
# Rate limit for akshare: seconds between requests
AKSHARE_DELAY = 0.35


def _to_qmt_code(stock_id: str) -> str | None:
    """Convert 6-digit code to QMT format (600519 → 600519.SH)."""
    if not stock_id or len(stock_id) != 6:
        return None
    first = stock_id[0]
    if first in ("8", "4"):
        return None  # Skip BJ stocks
    if first in ("6", "5"):
        return f"{stock_id}.SH"
    if first in ("0", "3"):
        return f"{stock_id}.SZ"
    return None


def _qmt_to_plain(qmt: str) -> str:
    """Convert QMT code (600519.SH) to plain 6-digit code (600519)."""
    return qmt.split(".")[0]


def _check_bridge() -> bool:
    """Check if Windows xtdata bridge is reachable."""
    try:
        req = urllib.request.Request(f"{WINDOWS_XTDATA_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read())
            print(f"✓ Bridge 在线: {health}")
            return True
    except Exception as e:
        print(f"⚠ Bridge 不可达 ({WINDOWS_XTDATA_URL}): {e}")
        return False


def _fetch_via_bridge(codes: list[str]) -> dict[str, list[dict]]:
    """Batch fetch K-lines from Windows xtdata bridge.

    Args:
        codes: list of QMT codes (e.g., ["600519.SH", "000001.SZ"])

    Returns:
        {plain_6digit_code: [bars_with_timestamp]}
    """
    total = len(codes)
    total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE
    result: dict[str, list[dict]] = {}
    failed_batches = 0
    url = WINDOWS_XTDATA_URL.rstrip("/") + "/klines"

    for i in range(0, total, BATCH_SIZE):
        batch = codes[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1

        body = json.dumps(
            {"codes": batch, "period": "1d", "count": KLINE_DAYS, "timeout": TIMEOUT},
            ensure_ascii=False,
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT + 10) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                if resp_data.get("status") != "ok":
                    print(f"  ✗ 批次 {batch_num}/{total_batches} 错误: {resp_data.get('error', 'δ֪')}")
                    failed_batches += 1
                    continue
                data = resp_data.get("data", {})
                # Convert QMT codes → plain codes, convert date → timestamp
                for qmt_code, bars in data.items():
                    plain = _qmt_to_plain(qmt_code)
                    converted = []
                    for bar in bars:
                        date_str = str(bar.get("date", ""))
                        if len(date_str) == 8:
                            ts = int(datetime.strptime(date_str, "%Y%m%d").timestamp() * 1000)
                        else:
                            continue
                        converted.append({
                            "timestamp": ts,
                            "open": bar.get("open", 0),
                            "high": bar.get("high", 0),
                            "low": bar.get("low", 0),
                            "close": bar.get("close", 0),
                            "volume": bar.get("volume", 0),
                        })
                    if converted:
                        result[plain] = converted
                print(f"  ✓ 批次 {batch_num}/{total_batches}: {len(data)} ֻ (累计 {len(result)}/{total})")
        except urllib.error.URLError as e:
            reason = str(e).lower()
            if "connection refused" in reason or "timed out" in reason:
                print(f"  ✗ 批次 {batch_num} 超时/不可达，停止")
                break
            print(f"  ✗ 批次 {batch_num} 失败: {e}")
            failed_batches += 1
        except Exception as e:
            print(f"  ✗ 批次 {batch_num} 异常: {e}")
            failed_batches += 1

    print(f"\nBridge 拉取完成: {len(result)}/{total} 只成功, {failed_batches} 批失败")
    return result


def _fetch_via_akshare(codes: list[str], cache) -> dict[str, list[dict]]:
    """Fetch K-lines one by one via akshare. Auto-saves to cache via get_klines.

    Args:
        codes: list of QMT codes (e.g., ["600519.SH"])
        cache: StockCache instance (unused — akshare provider handles caching)

    Returns:
        {plain_6digit_code: [bars_with_timestamp]}
    """
    from data_providers.akshare_provider import get_klines

    total = len(codes)
    result: dict[str, list[dict]] = {}
    failed = 0
    skipped = 0

    # Date range: last KLINE_DAYS trading days
    end_date = datetime.now().strftime("%Y%m%d")
    start_dt = datetime.now() - timedelta(days=int(KLINE_DAYS * 1.5))
    start_date = start_dt.strftime("%Y%m%d")

    eta_min = int(total * AKSHARE_DELAY / 60)
    print(f"\n使用 akshare 拉取 {total} 只股票 K 线（每只约 {AKSHARE_DELAY}s 间隔）")
    print(f"预计耗时: ~{eta_min} 分钟\n")

    for idx, qmt in enumerate(codes, 1):
        plain = _qmt_to_plain(qmt)

        if idx % 100 == 0 or idx == 1 or idx == total:
            elapsed = idx * AKSHARE_DELAY
            remaining = (total - idx) * AKSHARE_DELAY
            print(f"  进度: {idx}/{total} ({idx * 100 // total}%)  "
                  f"成功: {len(result)}  失败: {failed}  "
                  f"剩余: ~{int(remaining / 60)}m")

        try:
            # akshare_provider.get_klines handles cache check + fetch + save internally
            bars = get_klines(
                symbol=plain,
                interval="1d",
                start=start_date,
                end=end_date,
                market="cn_stock",
            )
            if bars and len(bars) > 0:
                result[plain] = bars
            else:
                skipped += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                print(f"  ✗ {plain}: {e}", file=sys.stderr)
            elif failed == 6:
                print(f"  ✗ (后续错误将不再逐条显示)", file=sys.stderr)

        # Rate limit to avoid being blocked
        if idx < total:
            time.sleep(AKSHARE_DELAY)

    print(f"\nakshare 拉取完成: {len(result)}/{total} 只成功, {failed} 失败, {skipped} 无数据")
    return result


def main():
    # Parse args
    max_stocks = None
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--max" and i + 1 < len(args):
            max_stocks = int(args[i + 1])

    # Step 1: Check bridge
    bridge_ok = _check_bridge()
    if not bridge_ok:
        print("  将使用 akshare 从东方财富拉取数据（较慢但无需 Windows）\n")

    # Step 2: Get all stock codes from cache
    from cache.stock_cache import StockCache
    cache = StockCache()
    stock_list = cache.get_stock_list()

    # If today's list is missing (e.g., past midnight), try yesterday
    if not stock_list:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        stock_list = cache.get_stock_list(yesterday)
        if stock_list:
            print(f"使用昨日 ({yesterday}) 的股票列表")

    if not stock_list:
        # Try to get stock list from kline_data table directly
        conn = cache._ensure_conn()
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM kline_data WHERE market = 'cn_stock'"
        ).fetchall()
        if rows:
            stock_list = {r[0]: {} for r in rows}
            print(f"从 K 线缓存中提取股票列表")

    if not stock_list:
        print("缓存无股票列表，尝试从新浪拉取...")
        try:
            from engines.factor_filter import _fetch_cn_stock_data
            stock_list, errors = _fetch_cn_stock_data()
            if errors:
                for e in errors[:5]:
                    print(f"  ⚠ {e}")
        except ImportError:
            pass
        if not stock_list:
            print("✗ 无法获取股票列表")
            sys.exit(1)

    print(f"股票列表: {len(stock_list)} ֻ")

    # Step 3: Check which stocks need K-line refresh (single SQL query)
    stale_cutoff = datetime.now() - timedelta(days=2)
    stale_cutoff_ts = int(stale_cutoff.timestamp() * 1000)
    stale_cutoff_date = stale_cutoff.strftime("%Y-%m-%d")

    # Query: for each symbol, get latest trade_date and bar count
    conn = cache._ensure_conn()
    fresh_symbols: set[str] = set()
    symbol_bar_counts: dict[str, int] = {}

    rows = conn.execute("""
        SELECT symbol, MAX(trade_date) as latest_date, COUNT(*) as bar_count
        FROM kline_data
        WHERE market = 'cn_stock' AND period = 'daily'
        GROUP BY symbol
    """).fetchall()
    for symbol, latest_date, bar_count in rows:
        symbol_bar_counts[symbol] = bar_count
        if latest_date >= stale_cutoff_date and bar_count >= KLINE_DAYS * 0.6:
            fresh_symbols.add(symbol)

    codes_need_fetch: list[str] = []
    codes_fresh = 0
    codes_no_cache = 0
    codes_stale = 0

    for sid in stock_list:
        qmt = _to_qmt_code(sid)
        if not qmt:
            continue
        if sid in fresh_symbols:
            codes_fresh += 1
            continue
        if sid in symbol_bar_counts:
            codes_stale += 1
        else:
            codes_no_cache += 1
        codes_need_fetch.append(qmt)

    print(f"已是最新: {codes_fresh}, 过期需刷新: {codes_stale}, 无缓存: {codes_no_cache}")
    print(f"需要拉取: {len(codes_need_fetch)} ֻ")

    if not codes_need_fetch:
        print("✓ 所有 K 线数据都是最新的")
        return

    # Apply --max limit
    if max_stocks and len(codes_need_fetch) > max_stocks:
        print(f"  --max {max_stocks}: 仅拉取前 {max_stocks} ֻ")
        codes_need_fetch = codes_need_fetch[:max_stocks]

    # Step 4: Fetch K-lines
    if bridge_ok:
        all_data = _fetch_via_bridge(codes_need_fetch)
        # Bridge data needs explicit save to cache
        if all_data:
            saved = 0
            for sid, bars in all_data.items():
                if bars:
                    try:
                        cache.save_klines(sid, bars, "cn_stock", "daily")
                        saved += 1
                    except Exception:
                        pass
            print(f"✓ 已保存 {saved} 只股票 K 线到缓存")
    else:
        # akshare provider auto-saves to cache inside get_klines()
        all_data = _fetch_via_akshare(codes_need_fetch, cache)

    # Step 5: Verify (single SQL query)
    verify_rows = conn.execute("""
        SELECT symbol, MAX(trade_date) as latest_date, COUNT(*) as bar_count
        FROM kline_data
        WHERE market = 'cn_stock' AND period = 'daily'
        GROUP BY symbol
    """).fetchall()

    total_in_cache = len(verify_rows)
    fresh_in_cache = sum(1 for _, latest, _ in verify_rows if latest >= stale_cutoff_date)

    print(f"\n=== 缓存状态 ===")
    print(f"股票列表: {len(stock_list)} ֻ")
    print(f"有 K 线数据: {total_in_cache} ֻ")
    print(f"K 线最新(2天内): {fresh_in_cache} ֻ")


if __name__ == "__main__":
    main()
