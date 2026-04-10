# coding: utf-8
"""
qmt_screener_mac.py — QMT 选股入口（复用已有选股引擎）
=======================================================

本脚本为轻量薄壳，核心逻辑全部来自项目已有模块：
  - engines/screener_engine.py  — 选股调度（数据获取 + 运行策略）
  - screeners/technical.py      — 技术面筛选（MA 排列、价格站均线、量比）
  - screeners/volume_price.py   — 量价异动筛选（Wyckoff/VSA）
  - data_providers/akshare_provider.py — 数据获取 + 代理绕过
  - cache/stock_cache.py        — SQLite 缓存（避免频繁打 API）

选股条件（SCREENER_ID="technical"）:
  - MA5 > MA20 > MA60 多头排列
  - 收盘价 > MA20（上升趋势）
  - RSI 30~70（非超买超卖）
  - MACD 金叉 / 柱状线上升
  - 量比 > 1.5（温和放量）

K 线数据来源（二选一）:
  USE_QMT_BRIDGE=False（默认）→ Mac 本地 akshare_provider 拉取（慢，有反爬限速）
  USE_QMT_BRIDGE=True         → 请求 Windows miniQMT 通过 xtdata 拉取
                                 （快，本地数据，需要 qmt_data_bridge.py 在 Windows 运行）

手动运行（从 strategies/ 目录）:
    python3 qmt_screener_mac.py [--save-only]

通过 OpenClaw Discord /选股 命令触发（commands/index.ts 调用）:
    python3 qmt_screener_mac.py --save-only

依赖（与 screener_engine 共享）:
    pip install akshare pandas
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error

# ── macOS 系统代理绕过（必须在 import requests/akshare 之前执行）──────────
# ClashX 等代理工具通过 macOS 系统代理(scutil)被 Python urllib3 继承，
# 导致连 eastmoney/xueqiu 等 CN 金融 API ʱ RemoteDisconnected。
# 此进程只访问 CN 金融 API，安全地清除所有代理。
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
except ImportError:
    pass

# ── 设置 Python 路径，使本脚本能 import engines.screener_engine 等模块 ──────────
# 本文件位于 python/strategies/，父目录 python/ 是包根
_PYTHON_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _PYTHON_ROOT not in sys.path:
    sys.path.insert(0, _PYTHON_ROOT)

# =====================================================================
# 配置项
# =====================================================================

# 选股策略：
#   "technical"    — MA 多头排列 + MA20 站稳 + 量比（使用 K 线，需网络拉取）
#   "volume_price" — 量价异动（Wyckoff/VSA），更适合短线
SCREENER_ID = "technical"

# 选股数量上限（最终推给 QMT 的股票数）
# 50 ֻ → 技术面初筛更宽，给 6 个分类视图更多候选池，
# 避免少量强势股霸占所有分类。后续 TS 端每类只取 top 5/15 展示。
SCREEN_TOP_N = 50

# K 线天数（技术指标计算用）
KLINE_DAYS = 120

# 股票池（空字符串 = ȫ A 股；可以用 "hs300"/"zz500"/"zz1000"/"cyb"/"kc50" 等）
UNIVERSE = ""

# 推送到 OpenClaw 网关的 URL
OPENCLAW_STOCKLIST_URL = "http://127.0.0.1:18789/quant/api/stocklist"

# ── QMT 数据桥配置（USE_QMT_BRIDGE=True 时生效）─────────────────────
#
# 设为 True 后，K 线数据由 Windows miniQMT 通过 xtdata 拉取：
#   优点：数据本地、无延迟、无反爬虫限制、支持全市场
#   前提：Windows 上需同时运行 qmt_data_bridge.py
#
USE_QMT_BRIDGE = os.environ.get("USE_QMT_BRIDGE", "true").lower() in ("1", "true", "yes")

# Mac OpenClaw 网关地址（数据桥中转用；localhost 即足够，网关本机跑）
GATEWAY_URL = "http://127.0.0.1:18789"

# 等待 Windows 返回 K 线数据的最长时间（秒）
BRIDGE_TIMEOUT = 90

# ── 直连模式配置（Mac 直接拉取 Windows xtdata HTTP 服务）──────────────
#
# Windows 需运行 qmt_data_bridge.py --serve
# 设置 WINDOWS_XTDATA_URL 后会优先使用直连模式，跳过网关中转
# 留空则使用传统的网关轮询模式
#
WINDOWS_XTDATA_URL = os.environ.get("WINDOWS_XTDATA_URL", "http://127.0.0.1:58088")
# 直连模式超时（秒）— xtdata 本地数据一般 10~30s 返回
# 直连超时：每批 100 ֻԼ 30-60s，留余量
DIRECT_PULL_TIMEOUT = int(os.environ.get("DIRECT_PULL_TIMEOUT", "90"))



# =====================================================================
# 工具函数
# =====================================================================

# 预检缓存：同一次选股调用中只探测一次 bridge 状态
_bridge_probe_cache: dict[str, object] = {}

def _to_qmt_code(stock_id: str, factors: dict | None = None) -> str | None:
    """
    将 6 位股票代码转换为 QMT 格式（600519 → 600519.SH）。

    沪深 A 股规则：
      - 6xxxxx / 5xxxxx → .SH（上交所主板 / ETF）
      - 688xxx           → .SH（科创板）
      - 0xxxxx / 3xxxxx → .SZ（深交所主板 / 创业板）
    排除北交所（8xxxxx / 4xxxxx）；调用方不应传入 ETF(5xx)，
    但保留 SH 标记以防万一（QMT 会忽略不支持的代码）。
    """
    code = stock_id.strip().lstrip("0") if len(stock_id) > 6 else stock_id.strip()
    # 使用原始 6 位代码判断交易所
    c = stock_id.strip().zfill(6)
    if c.startswith("6") or c.startswith("5") or c.startswith("688"):
        return f"{c}.SH"
    if c.startswith("0") or c.startswith("3"):
        return f"{c}.SZ"
    # 北交所等：不纳入
    return None


def _fetch_klines_via_bridge(codes_qmt: list[str]) -> dict[str, list[dict]] | None:
    """
    通过 Mac 网关中转，请求 Windows miniQMT 拉取 K 线数据。

    流程:
      1. POST /quant/api/data-request  → 入队，返回 requestId
      2. 轮询 GET /quant/api/data-response?requestId=<id> 直到数据到达

    Args:
        codes_qmt: QMT 格式股票代码列表，如 ["600519.SH", "000001.SZ"]

    Returns:
        {stock_id_6digit: [kline_bars]} 或 None（超时/失败）
    """
    # Step 1: 发起请求
    body = json.dumps(
        {"codes": codes_qmt, "period": "1d", "count": KLINE_DAYS},
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{GATEWAY_URL}/quant/api/data-request",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp_json = json.loads(resp.read().decode("utf-8"))
            request_id: str = resp_json.get("requestId", "")
            if not request_id:
                print("[bridge] 网关未返回 requestId，桥接失败")
                return None
    except Exception as e:
        print(f"[bridge] 发起请求失败: {e}")
        return None

    print(f"[bridge] 请求已入队 requestId={request_id[:8]}…，等待 Windows 响应（最多 {BRIDGE_TIMEOUT}s）…")

    # Step 2: 轮询等待结果
    poll_url = f"{GATEWAY_URL}/quant/api/data-response?requestId={request_id}"
    deadline = time.time() + BRIDGE_TIMEOUT
    while time.time() < deadline:
        try:
            poll_req = urllib.request.Request(poll_url, method="GET")
            with urllib.request.urlopen(poll_req, timeout=10) as resp:
                if resp.status == 204:
                    remaining = int(deadline - time.time())
                    print(f"[bridge] 等待中… ({remaining}s 剩余)", end="\r")
                    time.sleep(3)
                    continue
                result = json.loads(resp.read().decode("utf-8"))
                data: dict[str, list[dict]] = result.get("data", {})
                print(f"\n[bridge] 收到 {len(data)} 只股票K线（来自 Windows xtdata）")
                return data
        except urllib.error.HTTPError as e:
            if e.code == 204:
                time.sleep(3)
                continue
            print(f"\n[bridge] 轮询失败 HTTP {e.code}: {e.read().decode()[:100]}")
            return None
        except Exception as e:
            print(f"\n[bridge] 轮询异常: {e}")
            time.sleep(3)

    print(f"\n[bridge] 超时（{BRIDGE_TIMEOUT}s），Windows 无响应")
    return None


def _fetch_klines_direct(codes_qmt: list[str], count_override: int | None = None) -> dict[str, list[dict]] | None:
    """
    直连 Windows xtdata HTTP 服务（或 Mac RECEIVER 中转）拉取 K 线数据。

    自动分批请求（每批 BATCH_SIZE 只），避免单次请求过大导致超时。
    预检 RECEIVER /health：如果 Windows bridge 不在线则立即跳过。

    Args:
        codes_qmt: QMT 格式股票代码列表，如 ["600519.SH", "000001.SZ"]
        count_override: 覆盖默认 K 线天数（用于增量拉取，如 10 天）

    Returns:
        {stock_id_6digit: [kline_bars]} 或 None（失败）
    """
    if not WINDOWS_XTDATA_URL:
        return None

    # ── 预检：检查 RECEIVER 是否有活跃的 Windows bridge 连接 ──
    # 如果 RECEIVER 运行在 push-receiver 模式但 Windows 没连接，
    # 所有请求都会阻塞至超时。先发一个小测试请求快速检测。
    # 同一次选股调用中，只做一次预检（避免增量+全量两次探测）。
    if not _bridge_probe_cache.get("checked"):
        _bridge_probe_cache["checked"] = True
        health_url = WINDOWS_XTDATA_URL.rstrip("/") + "/health"
        try:
            hreq = urllib.request.Request(health_url, method="GET")
            with urllib.request.urlopen(hreq, timeout=5) as hresp:
                health = json.loads(hresp.read().decode("utf-8"))
                # push-receiver 模式需要 Windows bridge 轮询
                if health.get("mode") == "push-receiver":
                    # 优先检查 bridge_connected 字段（RECEIVER 新版支持）
                    if health.get("bridge_connected") is False:
                        poll_age = health.get("bridge_last_poll_age")
                        age_info = f"（上次轮询 {poll_age}s 前）" if poll_age is not None else "（从未连接）"
                        print(f"[direct] Windows bridge 未连接{age_info}，跳过直连")
                        _bridge_probe_cache["ok"] = False
                    elif health.get("bridge_connected") is True:
                        print("[direct] Windows bridge 在线（健康检查确认）")
                        _bridge_probe_cache["ok"] = True
                    elif "bridge_connected" not in health:
                        # 旧版 RECEIVER 无 bridge_connected 字段，用探测请求验证
                        probe_url = WINDOWS_XTDATA_URL.rstrip("/") + "/klines"
                        probe_body = json.dumps(
                            {"codes": [codes_qmt[0]], "period": "1d", "count": 5, "timeout": 15},
                            ensure_ascii=False,
                        ).encode("utf-8")
                        probe_req = urllib.request.Request(
                            probe_url, data=probe_body,
                            headers={"Content-Type": "application/json"}, method="POST",
                        )
                        try:
                            with urllib.request.urlopen(probe_req, timeout=20) as presp:
                                probe_result = json.loads(presp.read().decode("utf-8"))
                                if probe_result.get("status") != "ok":
                                    print(f"[direct] 探测失败: Windows bridge 未响应，跳过直连")
                                    _bridge_probe_cache["ok"] = False
                                else:
                                    print("[direct] Windows bridge 探测成功")
                                    _bridge_probe_cache["ok"] = True
                        except (urllib.error.HTTPError, urllib.error.URLError, Exception) as pe:
                            print(f"[direct] Windows bridge 探测超时/失败 ({pe})，跳过直连")
                            _bridge_probe_cache["ok"] = False
                else:
                    # 非 push-receiver 模式（直连 xtdata），直接可用
                    _bridge_probe_cache["ok"] = True
        except Exception as he:
            print(f"[direct] RECEIVER 健康检查失败 ({he})，跳过直连")
            _bridge_probe_cache["ok"] = False

    if not _bridge_probe_cache.get("ok"):
        return None

    url = WINDOWS_XTDATA_URL.rstrip("/") + "/klines"
    BATCH_SIZE = 100  # 每批最多 100 只，单次约 30-60s
    kline_count = count_override or KLINE_DAYS
    total = len(codes_qmt)
    all_data: dict[str, list[dict]] = {}

    mode_label = f"增量{kline_count}天" if count_override else f"全量{kline_count}天"
    print(f"[direct] 直连 {WINDOWS_XTDATA_URL}, {total} 只股票（{mode_label}，分 {(total + BATCH_SIZE - 1) // BATCH_SIZE} 批）…")

    for i in range(0, total, BATCH_SIZE):
        batch = codes_qmt[i:i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        total_batches = (total + BATCH_SIZE - 1) // BATCH_SIZE

        body = json.dumps(
            {"codes": batch, "period": "1d", "count": kline_count, "timeout": DIRECT_PULL_TIMEOUT},
            ensure_ascii=False,
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=DIRECT_PULL_TIMEOUT + 10) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if result.get("status") != "ok":
                    err_msg = result.get('error', 'δ֪')
                    print(f"[direct] 批次 {batch_num}/{total_batches} 错误: {err_msg}")
                    # Windows bridge 超时/断连 → 中止后续批次
                    if "timeout" in err_msg.lower():
                        print(f"[direct] Windows bridge 超时，跳过后续批次")
                        break
                    continue
                data: dict[str, list[dict]] = result.get("data", {})
                all_data.update(data)
                print(f"[direct] 批次 {batch_num}/{total_batches}: 收到 {len(data)} 只（累计 {len(all_data)}/{total}）")
        except urllib.error.HTTPError as he:
            # HTTP 408 = RECEIVER 等待 Windows 超时，立即中止
            print(f"[direct] 批次 {batch_num} HTTP {he.code}，Windows bridge 无响应，跳过后续批次")
            break
        except urllib.error.URLError as e:
            reason = str(e).lower()
            if "connection refused" in reason or "timed out" in reason or "timeout" in reason:
                print(f"[direct] 批次 {batch_num} 超时/不可达，跳过后续批次")
                break
            else:
                print(f"[direct] 批次 {batch_num} 失败: {e}")
                continue
        except Exception as e:
            print(f"[direct] 批次 {batch_num} 异常: {e}")
            continue

    if all_data:
        print(f"[direct] 总计收到 {len(all_data)}/{total} 只股票K线")
        return all_data
    return None


def _check_windows_health() -> bool:
    """检查 Windows xtdata HTTP 服务（或 RECEIVER 中转）是否在线。"""
    if not WINDOWS_XTDATA_URL:
        return False
    url = WINDOWS_XTDATA_URL.rstrip("/") + "/health"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result.get("status") == "ok"
    except Exception:
        return False


# =====================================================================
# 核心：调用 screener_engine
# =====================================================================

def screen_a_shares() -> tuple[list[str], list[dict]]:
    """
    调用已有选股引擎，返回 (QMT 格式代码列表, 富数据结果列表)。

    内部流程（由 screener_engine 负责）：
      1. 从新浪财经拉取全 A 股实时行情（或读 SQLite 缓存）
      2. 按 UNIVERSE 过滤股票池（全 A / 指数成分）
      3. 拉取 K 线数据（akshare_provider，带代理绕过）
      4. 运行 technical / volume_price 筛选策略
      5. 返回按 score 排序的结果
    """
    # 重置 bridge 预检缓存（每次选股重新探测）
    _bridge_probe_cache.clear()
    try:
        from engines.screener_engine import run_screener
    except ImportError as e:
        print(f"[screener] 无法导入 screener_engine: {e}")
        print(f"[screener] 请确认 {_PYTHON_ROOT} 目录结构完整")
        sys.exit(1)

    print(f"[screener] 使用策略: {SCREENER_ID}, 股票池: {UNIVERSE or 'ȫ A 股'}, "
          f"K线: {KLINE_DAYS}天, 目标数量: {SCREEN_TOP_N}")

    if USE_QMT_BRIDGE and WINDOWS_XTDATA_URL:
        data_source = f"Windows xtdata (直连 {WINDOWS_XTDATA_URL})"
    elif USE_QMT_BRIDGE:
        data_source = "Windows xtdata (网关中转)"
    else:
        data_source = "Mac akshare"
    print(f"[screener] K线数据源: {data_source}")

    config: dict = {
        "action": "run_screener",
        "screenerId": SCREENER_ID,
        "market": "cn_stock",
        "universe": UNIVERSE,
        "codes": [],
        "limit": SCREEN_TOP_N,
        "params": {},
        "includeKlines": True,      # technical/volume_price 均需要 K 线
        "klineDays": KLINE_DAYS,
    }

    # ── QMT 数据桥路径：先拿股票列表，再请求 Windows xtdata 拉 K 线 ─────
    if USE_QMT_BRIDGE:
        import engines.screener_engine as _eng

        # Step A: 获取股票池（无 K 线，只要名单）
        print("[screener] 获取股票池…")
        if UNIVERSE:
            stock_data, _ = _eng._fetch_index_universe(UNIVERSE)  # type: ignore[attr-defined]
        else:
            stock_data, _ = _eng._fetch_cn_universe()  # type: ignore[attr-defined]

        if not stock_data:
            print("[screener] 实时股票池获取失败，尝试使用本地缓存股票池…")
            try:
                from cache.stock_cache import StockCache

                cache = StockCache()
                cached_stock_data = cache.get_stock_list()
                if cached_stock_data:
                    stock_data = cached_stock_data
                    print(f"[screener] 缓存股票池恢复成功: {len(stock_data)} ֻ")
                else:
                    print("[screener] 无法获取股票池，且缓存为空，退出")
                    return [], []
            except Exception as e:
                print(f"[screener] 无法获取股票池，缓存回退失败: {e}")
                return [], []

        # Step B: 检查 SQLite 缓存，分三类处理：
        #   1. 新鲜缓存（最近 2 天有数据）→ 直接使用
        #   2. 过期缓存（有数据但不够新）→ 先使用过期数据，增量拉取缺失天数
        #   3. 无缓存 → 全量拉取
        from cache.stock_cache import StockCache
        cache = StockCache()
        klines_from_cache: dict[str, list[dict]] = {}
        codes_need_fetch: list[str] = []          # 完全无缓存，需全量拉取
        codes_need_incremental: list[str] = []    # 有过期缓存，只需增量拉取
        stale_cache_latest: dict[str, str] = {}   # sid → 过期缓存最新日期 (YYYYMMDD)

        # Determine the staleness threshold: if latest bar is >2 calendar days old,
        # the data needs refreshing (accounts for weekends/holidays)
        from datetime import datetime as _dt, timedelta as _td
        _stale_cutoff_ts = int((_dt.now() - _td(days=2)).timestamp() * 1000)

        for sid in stock_data:
            bars, _, cached_end = cache.get_klines(sid, "cn_stock", "daily")
            if bars and len(bars) >= KLINE_DAYS * 0.6:
                # Check if the latest bar is recent enough
                latest_ts = max(b.get("timestamp", 0) for b in bars[-5:])
                if latest_ts >= _stale_cutoff_ts:
                    klines_from_cache[sid] = bars
                    continue
                # Stale cache — use it but also request incremental update
                klines_from_cache[sid] = bars  # 先用过期数据
                qmt = _to_qmt_code(sid)
                if qmt and cached_end:
                    codes_need_incremental.append(qmt)
                    # cached_end is "YYYY-MM-DD", convert to "YYYYMMDD" for bridge
                    stale_cache_latest[sid] = cached_end.replace("-", "")
                continue
            # No cache at all
            qmt = _to_qmt_code(sid)
            if qmt:
                codes_need_fetch.append(qmt)

        print(
            f"[screener] 股票池 {len(stock_data)} ֻ: "
            f"新鲜缓存 {len(klines_from_cache) - len(codes_need_incremental)}, "
            f"过期缓存(增量更新) {len(codes_need_incremental)}, "
            f"无缓存(全量拉取) {len(codes_need_fetch)}"
        )

        # Step C: 拉取数据（增量 + 全量）
        #   优先级: 直连 xtdata HTTP → 网关轮询 bridge → fallback akshare → stale cache
        klines_from_bridge: dict[str, list[dict]] | None = None
        all_codes_to_fetch = codes_need_fetch + codes_need_incremental
        # 对增量请求，只需最近几天数据（不是全量 KLINE_DAYS 天）
        incremental_count = 10  # 拉最近 10 天足够覆盖节假日

        if all_codes_to_fetch:
            # 优先尝试直连 Windows xtdata HTTP 服务
            if WINDOWS_XTDATA_URL:
                # 分两批：全量 + 增量
                if codes_need_fetch:
                    print(f"[screener] 全量拉取 {len(codes_need_fetch)} 只…")
                    klines_from_bridge = _fetch_klines_direct(codes_need_fetch)
                if codes_need_incremental:
                    print(f"[screener] 增量拉取 {len(codes_need_incremental)} 只（最近 {incremental_count} 天）…")
                    incremental_data = _fetch_klines_direct(
                        codes_need_incremental, count_override=incremental_count
                    )
                    if incremental_data:
                        if klines_from_bridge is None:
                            klines_from_bridge = {}
                        klines_from_bridge.update(incremental_data)
            # 直连失败/未配置，降级为网关轮询模式（只对全量请求）
            if klines_from_bridge is None and codes_need_fetch:
                klines_from_bridge = _fetch_klines_via_bridge(codes_need_fetch)
            # Last resort: if bridge failed for some codes, use stale cache data
            # so they still get screened (better stale than missing)
            if klines_from_bridge is not None:
                fetched_sids = set()
                for qmt_code in all_codes_to_fetch:
                    sid = qmt_code.split(".")[0] if "." in qmt_code else qmt_code
                    if sid in klines_from_bridge:
                        fetched_sids.add(sid)
                stale_fallback = 0
                for sid in stock_data:
                    if sid not in klines_from_cache and sid not in fetched_sids:
                        bars, _, _ = cache.get_klines(sid, "cn_stock", "daily")
                        if bars and len(bars) >= KLINE_DAYS * 0.6:
                            klines_from_cache[sid] = bars
                            stale_fallback += 1
                if stale_fallback:
                    print(f"[screener] {stale_fallback} 只使用过期缓存兜底（bridge 未覆盖）")
        elif klines_from_cache:
            print("[screener] 全部命中缓存，跳过数据请求")

        # Step D: 合并 bridge 数据 + 缓存数据，并保存新数据到缓存
        merged_klines: dict[str, list[dict]] = dict(klines_from_cache)
        if klines_from_bridge:
            # 将 bridge 返回的数据保存到 SQLite 缓存
            saved = 0
            for sid, bars in klines_from_bridge.items():
                try:
                    # bridge 返回的 bar 格式: {date, open, high, low, close, volume}
                    # StockCache.save_klines 需要 {timestamp, open, ...}
                    converted_bars = []
                    for bar in bars:
                        date_str = str(bar.get("date", ""))
                        if len(date_str) == 8:
                            # YYYYMMDD → timestamp
                            from datetime import datetime as _dt
                            ts = int(_dt.strptime(date_str, "%Y%m%d").timestamp() * 1000)
                        else:
                            continue
                        converted_bars.append({
                            "timestamp": ts,
                            "open": bar.get("open", 0),
                            "high": bar.get("high", 0),
                            "low": bar.get("low", 0),
                            "close": bar.get("close", 0),
                            "volume": bar.get("volume", 0),
                        })
                    if converted_bars:
                        cache.save_klines(sid, converted_bars, "cn_stock", "daily")
                        saved += 1
                    # 增量合并：如果该股票已有缓存数据，拼接增量 bars
                    if sid in klines_from_cache and converted_bars:
                        existing = klines_from_cache[sid]
                        existing_ts = {b.get("timestamp") for b in existing}
                        new_bars = [b for b in converted_bars if b["timestamp"] not in existing_ts]
                        merged = existing + new_bars
                        merged.sort(key=lambda b: b["timestamp"])
                        merged_klines[sid] = merged
                    elif converted_bars:
                        merged_klines[sid] = converted_bars
                except Exception:
                    pass
            incremental_saved = sum(1 for sid in klines_from_bridge if sid in stale_cache_latest)
            full_saved = saved - incremental_saved
            parts = []
            if full_saved > 0:
                parts.append(f"全量 {full_saved}")
            if incremental_saved > 0:
                parts.append(f"增量 {incremental_saved}")
            print(f"[screener] 已缓存 {saved} 只股票K线到 SQLite ({', '.join(parts) if parts else saved})")

        if merged_klines:
            config["klines_data"] = merged_klines
        else:
            print("[screener] bridge 无数据且缓存为空，降级为本地 akshare")

    result = run_screener(config)

    if "error" in result:
        print(f"[screener] 选股引擎报错: {result['error']}")
        sys.exit(1)

    if result.get("errors"):
        for err in result["errors"]:
            if not err.startswith("(数据来自"):   # 缓存提示不打印
                print(f"[screener] 警告: {err}", file=sys.stderr)

    raw_results: list[dict] = result.get("results", [])
    total = result.get("totalScreened", 0)
    print(f"[screener] 全市场 {total} ֻ → 筛选出 {len(raw_results)} ֻ")

    # ── Step 2: Enrich screened stocks with F-Score / fundamental data ──
    # Technical screener only provides MA/MACD/RSI; we add PE/PB/ROE/F-Score
    if raw_results and SCREENER_ID in ("technical", "volume_price"):
        winner_codes = [item.get("stock_id", "") for item in raw_results[:SCREEN_TOP_N]]
        winner_codes = [c for c in winner_codes if c]
        if winner_codes:
            print(f"[screener] 正在为 {len(winner_codes)} 只筛选结果补充基本面 (F-Score)...")
            try:
                from data_providers.financial_provider import (
                    fetch_financials_batch,
                    financial_to_dict,
                )
                fin_data = fetch_financials_batch(
                    winner_codes, "cn_stock", max_workers=2, chunk_size=10, chunk_delay=1.5,
                )
                enriched_count = 0
                for item in raw_results[:SCREEN_TOP_N]:
                    sid = item.get("stock_id", "")
                    snap = fin_data.get(sid)
                    if snap is not None:
                        fd = financial_to_dict(snap)
                        # Merge financial factors into item.factors without overwriting
                        factors = item.get("factors", {})
                        for k in ("pe", "pb", "roe", "roa", "debt_ratio", "f_score",
                                  "f_score_details", "gross_margin", "gross_margin_prev",
                                  "current_ratio",
                                  "revenue_growth_yoy", "net_profit_growth_yoy",
                                  "operating_cf", "ocf_to_net_profit",
                                  "ar_turnover_days",
                                  "industry", "total_market_val_yi"):
                            if k in fd and fd[k] is not None:
                                factors[k] = fd[k]
                        item["factors"] = factors
                        # Add F-Score signals
                        fs = fd.get("f_score")
                        fs_details = fd.get("f_score_details", {})
                        if fs is not None:
                            signals = item.get("signals", [])
                            signals.append(f"F-Score: {fs}/9")
                            _labels = {
                                "positive_roa": "ROA为正",
                                "positive_ocf": "经营现金流为正",
                                "roa_increasing": "ROA同比改善",
                                "ocf_quality": "现金流>净利润",
                                "leverage_decreasing": "负债率下降",
                                "current_ratio_improving": "流动比率改善",
                                "no_dilution": "未增发",
                                "gross_margin_improving": "毛利率改善",
                                "asset_turnover_improving": "资产周转率改善",
                            }
                            for key, val in fs_details.items():
                                label = _labels.get(key, key)
                                signals.append(f"{'✓' if val else '✗'} {label}")
                            item["signals"] = signals
                        # Preserve name from financial data if better
                        if fd.get("name") and not item.get("name"):
                            item["name"] = fd["name"]
                        enriched_count += 1
                print(f"[screener] 基本面补充完成: {enriched_count}/{len(winner_codes)} ֻ")
            except Exception as e:
                print(f"[screener] 基本面补充失败 (非致命): {e}", file=sys.stderr)

    # ── Step 3: Compute kline-derived indicators ──
    # Price changes (3m/6m/12m), 20-day volatility, 120-day RPS
    if raw_results and merged_klines:
        import math
        # Collect 120-day returns for all stocks in the full pool to compute RPS
        all_returns_120d: dict[str, float] = {}
        for sid, bars in merged_klines.items():
            if bars and len(bars) >= 2:
                closes = [b.get("close", 0) for b in bars if b.get("close", 0) > 0]
                if len(closes) >= 2:
                    all_returns_120d[sid] = (closes[-1] / closes[0] - 1) * 100

        for item in raw_results[:SCREEN_TOP_N]:
            sid = item.get("stock_id", "")
            bars = merged_klines.get(sid, [])
            if not bars:
                continue
            closes = [b.get("close", 0) for b in bars if b.get("close", 0) > 0]
            if len(closes) < 2:
                continue

            factors = item.get("factors", {})

            # Price change over N trading days (~21 days/month)
            cur = closes[-1]
            if len(closes) >= 63:  # ~3 months
                factors["chg_3m"] = round((cur / closes[-63] - 1) * 100, 2)
            if len(closes) >= 120:  # ~6 months (use all 120 days)
                factors["chg_6m"] = round((cur / closes[0] - 1) * 100, 2)

            # 20-day annualized volatility
            if len(closes) >= 21:
                daily_returns = []
                for j in range(len(closes) - 20, len(closes)):
                    if closes[j - 1] > 0:
                        daily_returns.append(closes[j] / closes[j - 1] - 1)
                if daily_returns:
                    mean_r = sum(daily_returns) / len(daily_returns)
                    var = sum((r - mean_r) ** 2 for r in daily_returns) / len(daily_returns)
                    factors["volatility_20d"] = round(math.sqrt(var) * math.sqrt(252) * 100, 1)

            # 120-day RPS (relative price strength percentile vs full market)
            ret_120 = all_returns_120d.get(sid)
            if ret_120 is not None and len(all_returns_120d) > 10:
                rank = sum(1 for v in all_returns_120d.values() if v <= ret_120)
                factors["rps_120"] = round(rank / len(all_returns_120d) * 100, 1)

            item["factors"] = factors

        print(f"[screener] K线衍生指标补充完成 (涨跌幅/波动率/RPS)")

    # ── Step 3b: Compute composite investment score ──
    # Combines valuation, growth, profitability, quality (F-Score), and
    # technical momentum into a single 0–100 score for ranking.
    # This replaces the pure F-Score ranking that let expensive/low-growth
    # stocks rank too highly.
    if raw_results:
        for item in raw_results[:SCREEN_TOP_N]:
            f = item.get("factors", {})
            inv_score = 0.0
            inv_details: dict[str, float] = {}

            # ── A. Valuation (0–25 points) ──
            # Low PE/PB is better; penalise expensive stocks heavily.
            val_score = 0.0
            pe = f.get("pe")
            if pe is not None and pe > 0:
                if pe <= 15:
                    val_score += 12  # Value territory
                elif pe <= 25:
                    val_score += 8  # Reasonable
                elif pe <= 40:
                    val_score += 4  # Fair
                elif pe <= 60:
                    val_score += 1  # Expensive
                # PE > 60: 0 points
            pb = f.get("pb")
            if pb is not None and pb > 0:
                if pb <= 1.5:
                    val_score += 8
                elif pb <= 3:
                    val_score += 5
                elif pb <= 5:
                    val_score += 2
                # PB > 5: 0 points
            # Bonus for low PS
            ps = f.get("ps")
            if ps is not None and ps > 0 and ps <= 2:
                val_score += 5
            elif ps is not None and ps > 0 and ps <= 5:
                val_score += 2
            inv_details["valuation"] = min(val_score, 25)

            # ── B. Growth (0–25 points) ──
            growth_score = 0.0
            rev_g = f.get("revenue_growth_yoy")
            if rev_g is not None:
                if rev_g >= 30:
                    growth_score += 12
                elif rev_g >= 15:
                    growth_score += 8
                elif rev_g >= 5:
                    growth_score += 4
                elif rev_g >= 0:
                    growth_score += 1
                # Negative growth: 0
            np_g = f.get("net_profit_growth_yoy")
            if np_g is not None:
                if np_g >= 50:
                    growth_score += 10
                elif np_g >= 20:
                    growth_score += 7
                elif np_g >= 5:
                    growth_score += 4
                elif np_g >= 0:
                    growth_score += 1
            # PEG reward: PE / earnings growth
            if pe and pe > 0 and np_g and np_g > 5:
                peg = pe / np_g
                if peg <= 1:
                    growth_score += 3  # Under-valued relative to growth
                elif peg <= 2:
                    growth_score += 1
            inv_details["growth"] = min(growth_score, 25)

            # ── C. Profitability (0–25 points) ──
            prof_score = 0.0
            roe = f.get("roe")
            if roe is not None:
                if roe >= 20:
                    prof_score += 10
                elif roe >= 15:
                    prof_score += 7
                elif roe >= 10:
                    prof_score += 4
                elif roe >= 5:
                    prof_score += 2
                # ROE < 5%: 0
            gm = f.get("gross_margin")
            if gm is not None:
                if gm >= 50:
                    prof_score += 6
                elif gm >= 30:
                    prof_score += 4
                elif gm >= 20:
                    prof_score += 2
            # Cash flow quality
            ocf = f.get("operating_cf")
            if ocf is not None and ocf > 0:
                prof_score += 3
            ocf_np = f.get("ocf_to_net_profit")
            if ocf_np is not None and ocf_np > 1:
                prof_score += 3  # Cash earnings > accounting earnings
            elif ocf_np is not None and ocf_np > 0.5:
                prof_score += 1
            # Low debt bonus
            dr = f.get("debt_ratio")
            if dr is not None and dr < 40:
                prof_score += 3
            elif dr is not None and dr < 60:
                prof_score += 1
            inv_details["profitability"] = min(prof_score, 25)

            # ── D. Quality & Momentum (0–25 points) ──
            qual_score = 0.0
            fs = f.get("f_score")
            if fs is not None:
                # F-Score: 0-9, map to 0-10 points
                qual_score += min(fs * 10 / 9, 10)
            # Technical score contribution (existing 0-6.3, map to 0-8)
            tech_raw = item.get("score", 0)
            qual_score += min(tech_raw * 8 / 6.3, 8)
            # RPS bonus
            rps = f.get("rps_120")
            if rps is not None:
                if rps >= 80:
                    qual_score += 5
                elif rps >= 60:
                    qual_score += 3
                elif rps >= 40:
                    qual_score += 1
            # Low volatility bonus (stable)
            vol = f.get("volatility_20d")
            if vol is not None and vol < 30:
                qual_score += 2
            inv_details["quality_momentum"] = min(qual_score, 25)

            inv_score = (inv_details.get("valuation", 0)
                         + inv_details.get("growth", 0)
                         + inv_details.get("profitability", 0)
                         + inv_details.get("quality_momentum", 0))

            f["inv_score"] = round(inv_score, 1)
            f["inv_details"] = inv_details

            # Investment grade: A (≥60), B (≥45), C (≥30), D (<30)
            if inv_score >= 60:
                f["inv_grade"] = "A"
            elif inv_score >= 45:
                f["inv_grade"] = "B"
            elif inv_score >= 30:
                f["inv_grade"] = "C"
            else:
                f["inv_grade"] = "D"

            item["factors"] = f

        print("[screener] 综合投资评分完成 (估值/成长/盈利/质量)")

    # ── Step 4: Fill missing industry via serial stock_individual_info_em ──
    # Eastmoney rate-limits concurrent calls; serial + delay is more reliable.
    # Uses 3s delay between calls + 5s initial cooldown after batch fetch.
    # Skipped gracefully if akshare is not importable (e.g. different Python env).
    if raw_results:
        import time as _time
        missing_industry = [
            item for item in raw_results[:SCREEN_TOP_N]
            if not item.get("factors", {}).get("industry")
        ]
        if missing_industry:
            try:
                import akshare as ak
            except ImportError:
                ak = None  # type: ignore[assignment]
                print("[screener] akshare 不可用，跳过行业分类补充", file=sys.stderr)
            if ak is not None:
                _time.sleep(5)  # Cooldown after batch financial fetch
                filled = 0
                for item in missing_industry:
                    sid = item.get("stock_id", "")
                    code_num = sid.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
                    try:
                        df_info = ak.stock_individual_info_em(symbol=code_num)
                        if df_info is not None and not df_info.empty:
                            info_map: dict[str, Any] = {}
                            for _, row in df_info.iterrows():
                                info_map[str(row.iloc[0])] = row.iloc[1]
                            ind = info_map.get("行业")
                            if ind and str(ind).strip():
                                item.setdefault("factors", {})["industry"] = str(ind).strip()
                                filled += 1
                            # Also fill market cap if missing
                            if not item.get("factors", {}).get("total_market_val_yi"):
                                mval = info_map.get("总市值")
                                if mval:
                                    try:
                                        item["factors"]["total_market_val_yi"] = round(float(mval) / 1e8, 2)
                                    except (ValueError, TypeError):
                                        pass
                    except Exception:
                        pass  # Best-effort; already have financial data
                    _time.sleep(3)  # Respect Eastmoney rate limits
                if filled:
                    print(f"[screener] 行业分类补充: {filled}/{len(missing_industry)} ֻ")

    # 转换为 QMT 代码格式，同时构建 rich_results 保持 code↔name 对齐
    qmt_codes: list[str] = []
    rich_results: list[dict] = []
    print(f"[screener] 最终结果 (QMT 格式):")
    idx = 0
    for item in raw_results[:SCREEN_TOP_N]:
        stock_id = item.get("stock_id", "")
        qmt = _to_qmt_code(stock_id, item.get("factors"))
        if not qmt:
            continue
        qmt_codes.append(qmt)
        idx += 1
        name  = item.get("name", "")
        score = item.get("score", 0)
        sigs  = ", ".join(item.get("signals", [])[:2])
        print(f"  {idx:2}. {qmt}  {name}  score={score:.2f}  [{sigs}]")
        rich_results.append({
            "code": qmt,
            "name": name,
            "score": round(score, 2),
            "signals": item.get("signals", []),
            "factors": item.get("factors", {}),
        })

    if not qmt_codes:
        print("[screener] 筛选结果为空")
        return [], []

    return qmt_codes, rich_results


# =====================================================================
# 推送到 OpenClaw 网关
# =====================================================================

def push_to_gateway(stocks: list[str]) -> bool:
    """
    POST 股票列表到 OpenClaw 网关 /quant/api/stocklist。
    Windows QMT 在 before_trading_start 中通过 GET 拉取。
    """
    body = json.dumps(stocks, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        OPENCLAW_STOCKLIST_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = resp.read().decode("utf-8")
            print(f"[screener] 推送成功: {result[:120]}")
            return True
    except urllib.error.HTTPError as e:
        print(f"[screener] 推送失败 HTTP {e.code}: {e.read().decode()[:200]}")
        return False
    except Exception as e:
        print(f"[screener] 推送异常: {e}")
        return False


# =====================================================================
# 入口
# =====================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--save-only",
        action="store_true",
        help="只打印 JSON 结果到 stdout，不推送到网关（供 OpenClaw /选股 命令使用）",
    )
    args_parsed = parser.parse_args()

    print(f"[screener] {time.strftime('%Y-%m-%d %H:%M:%S')}")

    stocks, rich_results = screen_a_shares()

    if not stocks:
        print("[screener] 筛选结果为空")
        sys.exit(1)

    if args_parsed.save_only:
        # 最后一行输出 JSON，供调用方（commands/index.ts）parse
        # 输出格式: {"codes": [...], "results": [...]}
        # 兼容旧格式: 如果只需要 codes，调用方可以取 .codes
        output = {
            "codes": stocks,
            "results": rich_results,
        }
        print(json.dumps(output, ensure_ascii=False))
        sys.exit(0)

    ok = push_to_gateway(stocks)
    if ok:
        print("[screener] 已推送，Windows QMT before_trading_start 将自动拉取")
    else:
        print("[screener] 推送失败")
        sys.exit(1)
