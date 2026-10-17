"""
Factor-based stock screening and filtering engine.

Migrated from jtstock/stock_tools/factor_filter.py, modernized for Python 3.

This module provides a configurable, composable filtering pipeline for stocks
based on financial factors, rankings, industry membership, listing dates, etc.

Usage:
  filter_engine = FactorFilter([
    {"method": "single_factor", "factor": "roe", "value": 0.1, "isGreater": True},
    {"method": "factor_ranking", "factor": "pe", "range": [0, 0.3]},
    {"method": "filter_industry", "industry": "房地产"},
    {"method": "filter_listing_date", "year": 3, "isGreater": True},
  ])
  result_ids = filter_engine.filter(all_stock_data, trade_date)
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Callable, Optional

from .ranking import RankGroup, RankObject
from .helpers import get_pre_date_nearby, get_offset_days, get_standard_deviation


class FactorFilter:
    """Configurable multi-factor stock filter.

    Conditions are organized into groups:
      - A condition with `one_to_many=True` forms its own group
        (applied as a single pass over all stocks).
      - Consecutive conditions without `one_to_many` are grouped together
        (all must pass for a single stock to be included).
    """

    def __init__(self, conditions: list[dict[str, Any]]):
        self._raw_conditions = conditions
        self._condition_groups = self._divide_conditions(conditions)
        self._ranking_groups: dict[str, RankGroup] = {}
        self._temp_cache: dict[str, Any] = {}

        # Built-in filter methods
        self._methods: dict[str, Callable] = {
            "single_factor": self._filter_single_factor,
            "factor_ranking": self._filter_factor_ranking,
            "filter_industry": self._filter_industry,
            "find_industry": self._find_industry,
            "filter_listing_date": self._filter_listing_date,
            "continuous_compare": self._filter_continuous_compare,
            "continuous_each_other_compare": self._filter_continuous_yoy,
            "filter_market_cap": self._filter_market_cap,
            "filter_volatility": self._filter_volatility,
        }

    @staticmethod
    def _divide_conditions(conditions: list[dict]) -> list[list[dict]]:
        """Split conditions into groups for processing order."""
        groups: list[list[dict]] = []
        current_idx = 0
        for cond in conditions:
            if cond.get("one_to_many"):
                groups.append([cond])
                current_idx = len(groups)
            else:
                if len(groups) == current_idx:
                    groups.append([])
                groups[current_idx].append(cond)
        return groups

    def filter(
        self,
        stock_data: dict[str, dict[str, Any]],
        trade_date: int,
        listing_dates: Optional[dict[str, int]] = None,
        industries: Optional[dict[str, str]] = None,
    ) -> list[str]:
        """Apply all filter conditions and return matching stock IDs.

        Args:
          stock_data: { stock_id: { factor_name: value, ... } }
          trade_date: Current date as YYYYMMDD int.
          listing_dates: { stock_id: listing_date_int } for listing date filters.
          industries: { stock_id: industry_name } for industry filters.

        Returns:
          List of stock IDs that pass all conditions.
        """
        stock_ids = list(stock_data.keys())

        context = FilterContext(
            stock_data=stock_data,
            trade_date=trade_date,
            listing_dates=listing_dates or {},
            industries=industries or {},
            ranking_groups=self._ranking_groups,
        )

        for group in self._condition_groups:
            if not group:
                continue
            if group[0].get("one_to_many"):
                stock_ids = self._apply_one_to_many(stock_ids, context, group[0])
            else:
                stock_ids = self._apply_all_conditions(stock_ids, context, group)

        return stock_ids

    def _apply_one_to_many(
        self, stock_ids: list[str], ctx: "FilterContext", condition: dict
    ) -> list[str]:
        """Apply a single condition across all stocks (e.g., ranking filter)."""
        method = self._methods.get(condition.get("method", ""))
        if not method:
            return stock_ids
        return [sid for sid in stock_ids if method(stock_ids, sid, ctx, condition)]

    def _apply_all_conditions(
        self, stock_ids: list[str], ctx: "FilterContext", conditions: list[dict]
    ) -> list[str]:
        """Apply all conditions in a group — stock must pass ALL to survive."""
        result = []
        for sid in stock_ids:
            passes = True
            for cond in conditions:
                method = self._methods.get(cond.get("method", ""))
                if not method:
                    continue
                if not method(stock_ids, sid, ctx, cond):
                    passes = False
                    break
            if passes:
                result.append(sid)
        return result

    # ── Filter Method Implementations ────────────────────────────

    def _filter_single_factor(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Compare a single factor value against a threshold."""
        factor = cond["factor"]
        compare_val = cond["value"]
        is_greater = cond.get("isGreater", True)

        # Skip filter for stocks in a specific industry
        if "limit_industry" in cond:
            if ctx.industries.get(stock_id) == cond["limit_industry"]:
                return True

        val = ctx.get_factor(stock_id, factor)
        if val is None:
            return False

        if is_greater:
            return val > compare_val
        return val < compare_val

    def _filter_factor_ranking(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Filter by rank percentile of a factor."""
        factor = cond["factor"]
        rank_range = cond.get("range", [0, 1])

        if factor not in ctx.ranking_groups:
            ctx.ranking_groups[factor] = RankGroup()

        rg = ctx.ranking_groups[factor]
        if not rg.get_group(ctx.trade_date):
            # Build ranking for this factor/date
            objects: dict[str, RankObject] = {}
            for sid in stock_ids:
                val = ctx.get_factor(sid, factor)
                if val is None:
                    continue
                obj = RankObject(sid)
                obj.set_attr(factor, val)
                objects[sid] = obj
            rg.set_rank_type(factor, descending=True)
            rg.add_group(ctx.trade_date, objects)

        rank = rg.get_ranking(ctx.trade_date, stock_id, factor)
        if rank is None:
            return False
        volume = rg.get_group_size(ctx.trade_date)
        if volume == 0:
            return False
        ratio = rank / volume
        return rank_range[0] < ratio <= rank_range[1]

    def _filter_industry(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Exclude stocks in a specific industry."""
        target = cond.get("industry", "")
        return ctx.industries.get(stock_id, "") != target

    def _find_industry(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Only keep stocks in a specific industry."""
        target = cond.get("industry", "")
        return ctx.industries.get(stock_id, "") == target

    def _filter_listing_date(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Filter by listing age (years)."""
        listing_date = ctx.listing_dates.get(stock_id)
        if listing_date is None:
            return False
        years_threshold = cond.get("year", 3)
        is_greater = cond.get("isGreater", True)
        days = get_offset_days(listing_date, ctx.trade_date)
        if is_greater:
            return days >= years_threshold * 365
        return days < years_threshold * 365

    def _filter_continuous_compare(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Check if a factor exceeds a threshold for N consecutive periods."""
        factor = cond["factor"]
        periods = cond.get("time", 3)
        compare_val = cond["value"]
        is_greater = cond.get("isGreater", True)

        history = ctx.get_factor_history(stock_id, factor, periods)
        if not history or len(history) < periods:
            return False

        for val in history:
            if val is None:
                return False
            if is_greater and val < compare_val:
                return False
            if not is_greater and val > compare_val:
                return False
        return True

    def _filter_continuous_yoy(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Check if factor YoY growth exceeds a threshold for N consecutive periods."""
        factor = cond["factor"]
        periods = cond.get("time", 3)
        compare_val = cond["value"]
        is_greater = cond.get("isGreater", True)

        history = ctx.get_factor_history(stock_id, factor, periods)
        if not history or len(history) < periods:
            return False

        prev = None
        for val in history:
            if val is None:
                return False
            if prev is not None and prev != 0:
                growth = (val - prev) / abs(prev)
                if is_greater and growth < compare_val:
                    return False
                if not is_greater and growth > compare_val:
                    return False
            prev = val
        return True

    def _filter_market_cap(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Filter by total market cap (100M yuan units)."""
        val = ctx.get_factor(stock_id, "total_market_cap")
        if val is None:
            # Try computing from price * total_capital
            price = ctx.get_factor(stock_id, "price") or ctx.get_factor(stock_id, "close")
            cap = ctx.get_factor(stock_id, "total_capital")
            if price and cap:
                val = price * cap / 1e8
            else:
                return False
        compare_val = cond.get("value", 200)
        is_greater = cond.get("isGreater", False)
        if is_greater:
            return val > compare_val
        return val < compare_val

    def _filter_volatility(
        self, stock_ids: list[str], stock_id: str, ctx: "FilterContext", cond: dict
    ) -> bool:
        """Filter by historical volatility."""
        val = ctx.get_factor(stock_id, "history_volatility")
        if val is None:
            return False
        compare_val = cond.get("value", 0.5)
        is_greater = cond.get("isGreater", False)
        if is_greater:
            return val > compare_val
        return val < compare_val


class FilterContext:
    """Shared context for filter method implementations."""

    def __init__(
        self,
        stock_data: dict[str, dict[str, Any]],
        trade_date: int,
        listing_dates: dict[str, int],
        industries: dict[str, str],
        ranking_groups: dict[str, RankGroup],
    ):
        self.stock_data = stock_data
        self.trade_date = trade_date
        self.listing_dates = listing_dates
        self.industries = industries
        self.ranking_groups = ranking_groups

    def get_factor(self, stock_id: str, factor: str) -> Optional[float]:
        """Get a factor value for a stock."""
        data = self.stock_data.get(stock_id)
        if not data:
            return None
        return data.get(factor)

    def get_factor_history(self, stock_id: str, factor: str, periods: int) -> Optional[list[float]]:
        """Get historical factor values (e.g., from quarterly data embedded in stock_data)."""
        data = self.stock_data.get(stock_id)
        if not data:
            return None
        history_key = f"{factor}_history"
        history = data.get(history_key)
        if isinstance(history, list):
            return history[-periods:]
        # If no explicit history, just return current value as single-element
        val = data.get(factor)
        if val is not None:
            return [val]
        return None


# ── akshare Data Fetching ─────────────────────────────────────────

# Column mapping from akshare's stock_zh_a_spot() (Sina source) DataFrame
_SINA_COLUMN_MAP = {
    "代码": "raw_code",
    "名称": "name",
    "最新价": "price",
    "涨跌额": "change_amt",
    "涨跌幅": "change_pct",
    "买入": "bid",
    "卖出": "ask",
    "昨收": "prev_close",
    "今开": "open",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "turnover",
}

# Column mapping from Xueqiu individual stock spot data
_XQ_ITEM_MAP = {
    "市盈率(动)": "pe",
    "市盈率(TTM)": "pe_ttm",
    "市盈率(静)": "pe_static",
    "市净率": "pb",
    "每股收益": "eps",
    "每股净资产": "bvps",
    "股息率(TTM)": "dividend_yield",
    "总市值": "total_market_val",
    "流通值": "circulating_market_val",
    "52周最高": "week52_high",
    "52周最低": "week52_low",
    "周转率": "turnover_rate",
    "行业": "industry",
    "上市时间": "listing_date",
}

# Operator mapping for criteria filters
_OPERATOR_MAP = {
    "gt": lambda v, t: v is not None and v > t,
    "gte": lambda v, t: v is not None and v >= t,
    "ge": lambda v, t: v is not None and v >= t,
    "lt": lambda v, t: v is not None and v < t,
    "lte": lambda v, t: v is not None and v <= t,
    "le": lambda v, t: v is not None and v <= t,
    "eq": lambda v, t: v is not None and v == t,
    "ne": lambda v, t: v is not None and v != t,
    "between": lambda v, t: v is not None and isinstance(t, list) and len(t) == 2 and t[0] <= v <= t[1],
}


def _parse_code(raw_code: str) -> tuple[str, str]:
    """Parse raw code like 'sh600519' or 'bj920001' into (code, prefix).

    Returns (numeric_code, exchange_prefix).
    """
    raw = str(raw_code).lower()
    if raw.startswith(("sh", "sz", "bj")):
        return raw[2:], raw[:2]
    return raw, ""


def _code_to_xq_symbol(code: str) -> str:
    """Convert numeric code to Xueqiu symbol like 'SH600519'."""
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    if code.startswith(("4", "8", "9")):
        return f"BJ{code}"
    return f"SH{code}"


def _fetch_cn_stock_data() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch all A-share stock data from multiple sources.

    Data source cascade (stops at first success):
      1. Local SQLite cache (if fresh)
      2. QMT bridge (Windows xtdata HTTP bridge)
      3. 通达信 pytdx (free public TDX servers)
      4. Sina VIP direct HTTP API
      5. East Money push2 API
      6. akshare stock_zh_a_spot (slow fallback)
      7. akshare stock_info_a_code_name (code+name only, last resort)

    Returns (stock_data, errors).
    """
    # Try cache first
    try:
        from cache.stock_cache import get_cache
        cache = get_cache()
        cached = cache.get_stock_list()
        if cached:
            print(f"[选股] 命中缓存 ({len(cached)} ֻ)", file=sys.stderr)
            return cached, ["(数据来自本地缓存)"]
    except Exception as e:
        print(f"[选股] 缓存读取失败: {e}", file=sys.stderr)

    def _save_cache(data: dict[str, dict[str, Any]]) -> None:
        try:
            c = get_cache()
            c.save_stock_list(data)
        except Exception as e:
            print(f"[选股] 缓存保存失败: {e}", file=sys.stderr)

    # ── Source 2: QMT bridge ──
    stock_data, errs = _fetch_qmt_bridge_stocks()
    if stock_data:
        _save_cache(stock_data)
        return stock_data, errs

    # ── Source 2.5: Tushare Pro (fast, needs token) ──
    stock_data, errs = _fetch_tushare_stocks()
    if stock_data:
        _save_cache(stock_data)
        return stock_data, errs

    # ── Source 3: 通达信 pytdx ──
    stock_data, errs = _fetch_tdx_stocks()
    if stock_data:
        _save_cache(stock_data)
        return stock_data, errs

    # ── Source 4: Sina VIP direct HTTP ──
    stock_data, errs = _fetch_sina_vip_direct()
    if stock_data:
        _save_cache(stock_data)
        return stock_data, errs

    # ── Source 5: East Money push2 API ──
    stock_data, errs = _fetch_eastmoney_stocks()
    if stock_data:
        _save_cache(stock_data)
        return stock_data, errs

    # ── Source 6: akshare stock_zh_a_spot (slow but comprehensive) ──
    try:
        import akshare as ak
    except ImportError:
        return {}, ["所有数据源均不可用，akshare 未安装"]

    stock_data = {}
    try:
        print("[选股] 尝试 akshare stock_zh_a_spot (较慢) ...", file=sys.stderr)
        df = ak.stock_zh_a_spot()
        for _, row in df.iterrows():
            raw_code = str(row.get("代码", ""))
            if not raw_code:
                continue
            code, prefix = _parse_code(raw_code)
            if not code:
                continue

            record: dict[str, Any] = {"stock_id": code}
            for cn_col, en_key in _SINA_COLUMN_MAP.items():
                val = row.get(cn_col)
                if val is None:
                    continue
                if en_key in ("raw_code", "name"):
                    record[en_key] = str(val)
                else:
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

        if stock_data:
            _save_cache(stock_data)
            return stock_data, []
    except Exception:
        pass

    # ── Source 7: stock_info_a_code_name (code+name only, last resort) ──
    try:
        df_list = ak.stock_info_a_code_name()
        for _, row in df_list.iterrows():
            code = str(row.get("code", ""))
            name = str(row.get("name", ""))
            if not code:
                continue
            if "ST" in name or "*ST" in name:
                continue

            exchange = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ"
            stock_data[code] = {
                "stock_id": code,
                "name": name,
                "exchange": exchange,
            }

        if stock_data:
            _save_cache(stock_data)
        return stock_data, [
            "行情接口暂不可用，已使用股票列表 + 雪球数据"
        ]
    except Exception as e2:
        return {}, [f"获取 A 股数据失败: {e2}"]


# ══════════════════════════════════════════════════════════════════
#  Data Source: QMT Bridge
# ══════════════════════════════════════════════════════════════════


def _fetch_qmt_bridge_stocks() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch A-share data via QMT Windows bridge HTTP API.

    Uses /sector_members to get all A-share codes, then /quote for prices.
    Requires the QMT data bridge running on a Windows machine.

    Returns (stock_data, errors).
    """
    import json
    import urllib.request

    bridge_url = (
        os.environ.get("WINDOWS_XTDATA_URL")
        or _read_qmt_bridge_url()
        or "http://127.0.0.1:18888"
    )

    # Health check
    try:
        req = urllib.request.Request(f"{bridge_url}/health", method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            health = json.loads(resp.read().decode())
            if health.get("status") not in ("ok", "degraded") or not health.get("xtdata"):
                return {}, []
    except Exception:
        return {}, []

    print("[选股] 通过 QMT 数据桥获取全市场数据 ...", file=sys.stderr)

    # Get all A-share codes via sector_members with "沪深A股" sector
    all_codes: list[str] = []
    for sector in ["沪深A股", "全部A股"]:
        try:
            payload = json.dumps({"sector": sector}).encode()
            req = urllib.request.Request(
                f"{bridge_url}/sector_members",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
                if result.get("status") == "ok" and result.get("data"):
                    all_codes = result["data"]
                    break
        except Exception:
            continue

    if not all_codes:
        return {}, ["QMT 桥未返回股票列表"]

    # Normalize to QMT format (600519.SH) for /quote endpoint
    qmt_codes = []
    for c in all_codes:
        if "." in c:
            qmt_codes.append(c)
        elif c.startswith("6"):
            qmt_codes.append(f"{c}.SH")
        elif c.startswith(("0", "3")):
            qmt_codes.append(f"{c}.SZ")
        elif c.startswith(("4", "8")):
            qmt_codes.append(f"{c}.BJ")

    # Batch-fetch quotes (100 per request)
    stock_data: dict[str, dict[str, Any]] = {}
    batch_size = 100

    for i in range(0, len(qmt_codes), batch_size):
        batch = qmt_codes[i:i + batch_size]
        try:
            payload = json.dumps({"codes": batch}).encode()
            req = urllib.request.Request(
                f"{bridge_url}/quote",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                result = json.loads(resp.read().decode())
                if result.get("status") != "ok":
                    continue
                for code, q in result.get("data", {}).items():
                    price = float(q.get("price", 0) or 0)
                    if price <= 0:
                        continue
                    exchange = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ"
                    stock_data[code] = {
                        "stock_id": code,
                        "name": q.get("name", ""),
                        "price": price,
                        "open": float(q.get("open", 0) or 0),
                        "high": float(q.get("high", 0) or 0),
                        "low": float(q.get("low", 0) or 0),
                        "prev_close": float(q.get("prevClose", 0) or 0),
                        "volume": float(q.get("volume", 0) or 0),
                        "turnover": float(q.get("amount", 0) or 0),
                        "exchange": exchange,
                    }
        except Exception:
            continue

    if stock_data:
        # QMT quotes don't include names — enrich from TDX if possible
        unnamed = sum(1 for d in stock_data.values() if not d.get("name"))
        if unnamed > len(stock_data) * 0.5:
            _enrich_names_from_tdx(stock_data)
        print(f"[选股] QMT 桥获取完成: {len(stock_data)} 只股票", file=sys.stderr)
    return stock_data, []


def _read_qmt_bridge_url() -> str:
    """Read QMT bridge URL from config."""
    try:
        for state_dir in ["~/.quant-trading-dev", "~/.quant-trading"]:
            config_path = os.path.expanduser(f"{state_dir}/quant-trading.json")
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


def _enrich_names_from_tdx(stock_data: dict[str, dict[str, Any]]) -> None:
    """Enrich stock_data with names from TDX get_security_list.

    Modifies stock_data in-place. Also removes ST/delisted stocks.
    """
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return

    api = TdxHq_API()
    connected = False
    for host, port in _TDX_SERVERS:
        try:
            if api.connect(host, port, time_out=3):
                connected = True
                break
        except Exception:
            continue

    if not connected:
        return

    try:
        name_map: dict[str, str] = {}
        for market in [0, 1]:
            offset = 0
            while True:
                try:
                    batch = api.get_security_list(market, offset)
                except Exception:
                    break
                if not batch:
                    break
                for s in batch:
                    code = s.get("code", "")
                    name = s.get("name", "")
                    if code and name:
                        name_map[code] = name
                offset += len(batch)
                if len(batch) < 1000:
                    break
        api.disconnect()

        # Apply names and filter ST/delisted
        to_remove = []
        enriched = 0
        for code, d in stock_data.items():
            name = name_map.get(code, "")
            if name:
                d["name"] = name
                enriched += 1
            if "ST" in d.get("name", "") or "退" in d.get("name", ""):
                to_remove.append(code)
        for code in to_remove:
            del stock_data[code]
        if enriched:
            print(f"[选股] TDX 补充名称: {enriched} ֻ, 过滤ST: {len(to_remove)} ֻ", file=sys.stderr)
    except Exception:
        try:
            api.disconnect()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════
#  Data Source: Tushare Pro
# ══════════════════════════════════════════════════════════════════


def _fetch_tushare_stocks() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch A-share data via Tushare Pro API.

    Uses pro.stock_basic() for stock list + pro.daily() for latest prices.
    Requires TUSHARE_TOKEN env var (set by TypeScript bridge from config).

    Returns (stock_data, errors).
    """
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        return {}, []  # silently skip — no token configured

    try:
        import tushare as ts
    except ImportError:
        return {}, ["tushare 未安装"]

    try:
        ts.set_token(token)
        pro = ts.pro_api()

        print("[选股] 尝试 Tushare Pro stock_basic ...", file=sys.stderr)
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,symbol,name,area,industry,market,list_date")
        if df is None or df.empty:
            return {}, ["Tushare stock_basic 返回空数据"]

        stock_data: dict[str, dict[str, Any]] = {}
        for _, row in df.iterrows():
            code = str(row["symbol"])
            ts_code = str(row["ts_code"])
            name = str(row.get("name", code))

            if code.startswith("6"):
                exchange = "SH"
            elif code.startswith(("0", "3")):
                exchange = "SZ"
            else:
                exchange = "BJ"

            stock_data[code] = {
                "stock_id": code,
                "name": name,
                "exchange": exchange,
                "industry": row.get("industry", ""),
                "area": row.get("area", ""),
                "ts_code": ts_code,
            }

        if not stock_data:
            return {}, ["Tushare 未获取到股票数据"]

        # Fetch latest daily data for prices (batch — today or last trading day)
        from datetime import datetime, timedelta
        today = datetime.now().strftime("%Y%m%d")
        # Try today first, fallback to recent days
        daily_df = None
        for offset in range(0, 5):
            d = (datetime.now() - timedelta(days=offset)).strftime("%Y%m%d")
            try:
                daily_df = pro.daily(trade_date=d)
                if daily_df is not None and not daily_df.empty:
                    break
            except Exception:
                continue

        if daily_df is not None and not daily_df.empty:
            for _, row in daily_df.iterrows():
                ts_code = str(row["ts_code"])
                # Extract 6-digit code from ts_code (e.g. 600519.SH → 600519)
                code = ts_code.split(".")[0]
                if code in stock_data:
                    try:
                        stock_data[code]["price"] = float(row["close"])
                        stock_data[code]["open"] = float(row["open"])
                        stock_data[code]["high"] = float(row["high"])
                        stock_data[code]["low"] = float(row["low"])
                        stock_data[code]["pre_close"] = float(row["pre_close"])
                        stock_data[code]["volume"] = float(row["vol"])
                        stock_data[code]["amount"] = float(row["amount"])
                        change_pct = float(row["pct_chg"])
                        stock_data[code]["change_pct"] = change_pct
                    except (ValueError, TypeError):
                        pass

        print(f"[选股] Tushare Pro: {len(stock_data)} ֻ A 股", file=sys.stderr)
        return stock_data, []

    except Exception as e:
        print(f"[选股] Tushare Pro 失败: {e}", file=sys.stderr)
        return {}, [f"Tushare Pro failed: {e}"]


# ══════════════════════════════════════════════════════════════════
#  Data Source: 通达信 pytdx
# ══════════════════════════════════════════════════════════════════

_TDX_SERVERS = [
    ("180.153.18.170", 7709),
    ("60.12.136.250", 7709),
    ("218.75.126.9", 7709),
    ("119.147.212.81", 7709),
    ("101.227.73.20", 7709),
    ("14.215.128.18", 7709),
    ("59.173.18.140", 7709),
]


def _fetch_tdx_stocks() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch A-share data via 通达信 (pytdx) public servers.

    Uses get_security_list for stock code/name list, then get_security_quotes
    for real-time prices (batches of 80).

    Returns (stock_data, errors).
    """
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return {}, []

    api = TdxHq_API()
    connected = False

    for host, port in _TDX_SERVERS:
        try:
            ok = api.connect(host, port, time_out=3)
            if ok:
                connected = True
                print(f"[选股] 通达信连接成功: {host}:{port}", file=sys.stderr)
                break
        except Exception:
            continue

    if not connected:
        return {}, []

    try:
        # Phase 1: Get stock list (code + name) from get_security_list
        # pytdx returns batches of 1000; market 0=SZ, 1=SH
        code_name_map: dict[str, str] = {}
        for market in [0, 1]:
            offset = 0
            while True:
                try:
                    batch = api.get_security_list(market, offset)
                except Exception:
                    break
                if not batch:
                    break
                for s in batch:
                    code = s.get("code", "")
                    name = s.get("name", "")
                    if not code or not name:
                        continue
                    # Filter: only A-share stocks (by code prefix)
                    if market == 1 and code.startswith("6"):  # SH A-share
                        if "ST" not in name and "退" not in name:
                            code_name_map[code] = name
                    elif market == 0 and code.startswith(("000", "001", "002", "003", "300", "301")):  # SZ A-share
                        if "ST" not in name and "退" not in name:
                            code_name_map[code] = name
                offset += len(batch)
                if len(batch) < 1000:
                    break

        if not code_name_map:
            api.disconnect()
            return {}, []

        print(f"[选股] 通达信获取股票列表: {len(code_name_map)} ֻ", file=sys.stderr)

        # Phase 2: Batch-fetch real-time quotes (80 per call)
        stock_data: dict[str, dict[str, Any]] = {}
        codes_list = list(code_name_map.keys())
        batch_size = 80

        for i in range(0, len(codes_list), batch_size):
            batch = codes_list[i:i + batch_size]
            quote_params = []
            for code in batch:
                market = 1 if code.startswith("6") else 0
                quote_params.append((market, code))

            try:
                quotes = api.get_security_quotes(quote_params)
                if not quotes:
                    continue
                for q in quotes:
                    code = q.get("code", "")
                    price = float(q.get("price", 0) or 0)
                    if not code or price <= 0:
                        continue
                    name = code_name_map.get(code, "")
                    exchange = "SH" if code.startswith("6") else "SZ"
                    vol = float(q.get("vol", 0) or 0)
                    amount = float(q.get("amount", 0) or 0)
                    stock_data[code] = {
                        "stock_id": code,
                        "name": name,
                        "price": price,
                        "open": float(q.get("open", 0) or 0),
                        "high": float(q.get("high", 0) or 0),
                        "low": float(q.get("low", 0) or 0),
                        "prev_close": float(q.get("last_close", 0) or 0),
                        "volume": vol,
                        "turnover": amount,
                        "exchange": exchange,
                    }
                    if vol > 0:
                        change = price - float(q.get("last_close", 0) or 0)
                        stock_data[code]["change_amt"] = round(change, 4)
                        if q.get("last_close"):
                            stock_data[code]["change_pct"] = round(
                                change / float(q["last_close"]) * 100, 2
                            )
            except Exception:
                continue

        api.disconnect()

        if stock_data:
            print(f"[选股] 通达信获取完成: {len(stock_data)} 只股票", file=sys.stderr)
        return stock_data, []

    except Exception as e:
        try:
            api.disconnect()
        except Exception:
            pass
        return {}, [f"通达信数据获取失败: {e}"]


# ══════════════════════════════════════════════════════════════════
#  Data Source: East Money push2 API
# ══════════════════════════════════════════════════════════════════


def _fetch_eastmoney_stocks() -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch A-share data via East Money push2 API.

    Fetches up to 6000 stocks across multiple pages (100 per page).
    Fields: price, change%, PE, PB, market cap, volume, turnover.

    Returns (stock_data, errors).
    """
    import json
    import urllib.request
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # East Money field mapping:
    # f2=price, f3=change_pct, f4=change_amt, f5=vol(手), f6=turnover(Ԫ),
    # f8=turnover_ratio, f9=PE, f12=code, f13=market(0=SZ,1=SH),
    # f14=name, f15=high, f16=low, f17=open, f18=prev_close,
    # f20=total_mktcap(Ԫ), f21=circ_mktcap(Ԫ), f23=PB

    _EM_FIELDS = "f2,f3,f4,f5,f6,f8,f9,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23"
    _EM_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

    print("[选股] 使用东方财富获取全市场数据 ...", file=sys.stderr)

    def _safe_float(v: Any) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    def _fetch_page(page: int) -> tuple[int, list[dict[str, Any]]]:
        url = (
            f"https://push2.eastmoney.com/api/qt/clist/get?"
            f"np=1&fltt=2&invt=2&fid=f3&fs={_EM_FS}"
            f"&fields={_EM_FIELDS}&pn={page}&pz=100&po=1"
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            total = data.get("data", {}).get("total", 0)
            items = data.get("data", {}).get("diff", [])
            return total, items

    # First request to get total count
    try:
        total_count, first_items = _fetch_page(1)
    except Exception as e:
        return {}, [f"东方财富 API 连接失败: {e}"]

    if not first_items:
        return {}, ["东方财富 API 无数据"]

    total_pages = (total_count + 99) // 100

    # Fetch remaining pages concurrently (batches of 4)
    all_items: list[dict[str, Any]] = list(first_items)
    remaining_pages = list(range(2, min(total_pages + 1, 65)))  # Max 6400 stocks

    for batch_start in range(0, len(remaining_pages), 4):
        batch = remaining_pages[batch_start:batch_start + 4]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(_fetch_page, p): p for p in batch}
            for future in as_completed(futures):
                try:
                    _, items = future.result()
                    if items:
                        all_items.extend(items)
                except Exception:
                    pass

    # Parse items
    stock_data: dict[str, dict[str, Any]] = {}
    for s in all_items:
        code = str(s.get("f12", ""))
        market = s.get("f13", -1)
        name = str(s.get("f14", ""))
        if not code or not name:
            continue
        # Skip ST, delisted, Beijing exchange codes
        if "ST" in name or "退" in name:
            continue
        if code.startswith(("4", "8")):
            continue

        price = _safe_float(s.get("f2"))
        if price <= 0:
            continue

        exchange = "SH" if market == 1 else "SZ" if market == 0 else "BJ"
        pe_val = _safe_float(s.get("f9"))
        pb_val = _safe_float(s.get("f23"))

        stock_data[code] = {
            "stock_id": code,
            "name": name,
            "price": price,
            "change_amt": _safe_float(s.get("f4")),
            "change_pct": _safe_float(s.get("f3")),
            "open": _safe_float(s.get("f17")),
            "high": _safe_float(s.get("f15")),
            "low": _safe_float(s.get("f16")),
            "prev_close": _safe_float(s.get("f18")),
            "volume": _safe_float(s.get("f5")) * 100,  # 手 -> 股
            "turnover": _safe_float(s.get("f6")),
            "turnoverratio": _safe_float(s.get("f8")),
            "pe": pe_val if pe_val != 0 else None,
            "pb": pb_val if pb_val != 0 else None,
            "mktcap": _safe_float(s.get("f20")),
            "nmc": _safe_float(s.get("f21")),
            "exchange": exchange,
        }

    if stock_data:
        print(f"[选股] 东方财富获取完成: {len(stock_data)} 只股票", file=sys.stderr)
    return stock_data, []


def _fetch_sina_vip_direct(
    max_pages: int = 70,
    per_page: int = 80,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Fetch A-share stock data via Sina VIP direct HTTP API.

    Uses concurrent requests (8 workers) for ~5-10s fetch of 5000+ stocks,
    compared to ~60-120s via akshare's stock_zh_a_spot().

    Returns (stock_data, errors).
    """
    import json
    import urllib.request
    import re as _re
    from concurrent.futures import ThreadPoolExecutor, as_completed

    errors: list[str] = []

    print("[选股] 使用 Sina VIP 直连获取全市场数据 ...", file=sys.stderr)

    def _safe_float(v: Any) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    def _fetch_page(page: int) -> list[dict[str, Any]]:
        """Fetch a single page. Returns parsed items or empty list."""
        url = (
            f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            f"Market_Center.getHQNodeData?page={page}&num={per_page}&sort=mktcap"
            f"&asc=0&node=hs_a&symbol=&_s_r_a=sort"
        )
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Referer": "https://finance.sina.com.cn",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                fixed = _re.sub(r'(\{|,)\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)
                return json.loads(fixed)

    # Phase 1: Fetch pages in batches of 4 to avoid rate-limit
    all_items: list[dict[str, Any]] = []
    pages_fetched = 0
    batch_size = 4
    done = False

    for batch_start in range(1, max_pages + 1, batch_size):
        if done:
            break
        batch_end = min(batch_start + batch_size, max_pages + 1)
        batch_pages = list(range(batch_start, batch_end))

        with ThreadPoolExecutor(max_workers=batch_size) as pool:
            futures = {pool.submit(_fetch_page, p): p for p in batch_pages}
            page_results: dict[int, list[dict[str, Any]]] = {}

            for future in as_completed(futures):
                page_num = futures[future]
                try:
                    result = future.result()
                    if result:
                        page_results[page_num] = result
                except Exception:
                    pass

        # Assemble in page order; stop at first empty/missing page
        for p in batch_pages:
            items = page_results.get(p)
            if not items:
                done = True
                break
            all_items.extend(items)
            pages_fetched = p

    # Phase 2: Parse and filter stocks
    stock_data: dict[str, dict[str, Any]] = {}

    for s in all_items:
        sym = str(s.get("symbol", ""))
        code = sym[2:] if len(sym) > 2 and sym[:2] in ("sh", "sz") else sym
        if not code:
            continue
        name = str(s.get("name", ""))
        # Skip ST, delisted, Beijing exchange
        if "ST" in name or "退" in name:
            continue
        if code.startswith(("4", "8")):
            continue

        try:
            price = float(s.get("trade", 0) or 0)
        except (ValueError, TypeError):
            price = 0.0
        if price <= 0:
            continue

        exchange = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else "BJ"
        pe_val = _safe_float(s.get("per"))
        pb_val = _safe_float(s.get("pb"))

        stock_data[code] = {
            "stock_id": code,
            "raw_code": sym,
            "name": name,
            "price": price,
            "change_amt": _safe_float(s.get("pricechange")),
            "change_pct": _safe_float(s.get("changepercent")),
            "open": _safe_float(s.get("open")),
            "high": _safe_float(s.get("high")),
            "low": _safe_float(s.get("low")),
            "prev_close": _safe_float(s.get("settlement")),
            "volume": _safe_float(s.get("volume")),
            "turnover": _safe_float(s.get("amount")),
            "turnoverratio": _safe_float(s.get("turnoverratio")),
            "pe": pe_val or None,
            "pb": pb_val or None,
            "mktcap": _safe_float(s.get("mktcap")) * 1e4,  # Sina returns 万元
            "nmc": _safe_float(s.get("nmc")) * 1e4,
            "exchange": exchange,
        }

    total = len(stock_data)
    if total > 0:
        print(f"[选股] Sina VIP 获取完成: {total} 只股票 ({pages_fetched} ҳ)", file=sys.stderr)
    return stock_data, errors


def _fetch_single_xq(sid: str) -> tuple[str, dict[str, Any] | None, str | None]:
    """Fetch a single stock's Xueqiu data. Thread-safe helper."""
    try:
        import akshare as ak
    except ImportError:
        return sid, None, "akshare 未安装"

    xq_symbol = _code_to_xq_symbol(sid)
    try:
        df = ak.stock_individual_spot_xq(symbol=xq_symbol)
        if df is None or df.empty:
            return sid, None, None
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
        return sid, result, None
    except Exception as e:
        return sid, None, f"{sid}: {e}"


def _enrich_with_xueqiu(
    stock_ids: list[str],
    stock_data: dict[str, dict[str, Any]],
    max_enrich: int = 200,
    max_workers: int = 8,
) -> list[str]:
    """Enrich stock_data with PE/PB/EPS from Xueqiu for the given stock IDs.

    Uses local SQLite cache: checks cache first, only fetches missing stocks
    from Xueqiu. Uses ThreadPoolExecutor for parallel requests (~8x faster).
    Modifies stock_data in-place. Returns list of info/error messages.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    errors: list[str] = []
    enriched = 0
    to_enrich = stock_ids[:max_enrich]

    # Check cache for already-fetched fundamentals
    cache_hits = 0
    actually_fetch: list[str] = to_enrich
    try:
        from cache.stock_cache import get_cache
        cache = get_cache()
        cached_data, missing_codes = cache.get_fundamentals(to_enrich)
        if cached_data:
            # Apply cached data directly
            for sid, fdata in cached_data.items():
                stock_data[sid].update(fdata)
                cache_hits += 1
            actually_fetch = missing_codes
            if cache_hits > 0:
                print(
                    f"[缓存] 命中 {cache_hits} 只股票基本面缓存，"
                    f"还需从雪球获取 {len(actually_fetch)} ֻ",
                    file=sys.stderr,
                )
    except Exception as e:
        print(f"[缓存] 读取基本面缓存失败: {e}", file=sys.stderr)
        actually_fetch = to_enrich

    if not actually_fetch:
        errors.insert(0, f"已从本地缓存加载 {cache_hits} 只股票的 PE/PB/EPS 数据")
        return errors

    # Fetch missing stocks from Xueqiu
    newly_fetched: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_single_xq, sid): sid for sid in actually_fetch}
        for future in as_completed(futures):
            sid, result, err = future.result()
            if err:
                errors.append(err)
            if result:
                stock_data[sid].update(result)
                newly_fetched[sid] = result
                enriched += 1

    # Save newly fetched data to cache
    if newly_fetched:
        try:
            cache = get_cache()
            cache.save_fundamentals(newly_fetched)
        except Exception as e:
            print(f"[缓存] 保存基本面缓存失败: {e}", file=sys.stderr)

    if enriched > 0 or cache_hits > 0:
        parts = []
        if cache_hits > 0:
            parts.append(f"缓存命中 {cache_hits} ֻ")
        if enriched > 0:
            parts.append(f"雪球新获取 {enriched} ֻ")
        errors.insert(0, f"PE/PB/EPS 数据: {'，'.join(parts)}")

    return errors


def _needs_fundamental_data(criteria: list[dict], factors: list[str], sort_by: Optional[str]) -> bool:
    """Check if any criteria/factors need fundamental data (PE/PB/EPS etc.)."""
    fundamental_fields = {"pe", "pb", "eps", "roe", "bvps", "pe_ttm", "pe_static",
                          "dividend_yield", "total_market_val", "total_market_val_yi",
                          "circulating_market_val", "circulating_market_val_yi",
                          "roa", "roic", "revenue_growth_yoy", "net_profit_growth_yoy",
                          "eps_growth_yoy", "gross_margin", "net_margin",
                          "operating_margin", "debt_ratio", "current_ratio",
                          "debt_to_equity", "free_cash_flow", "operating_cf",
                          "f_score", "ev_to_ebit", "peg"}
    # Check criteria
    for c in criteria:
        if c.get("field", "") in fundamental_fields:
            return True
    # Check factors
    for f in factors:
        if f in fundamental_fields:
            return True
    # Check sort
    if sort_by and sort_by in fundamental_fields:
        return True
    return True  # Default: always enrich (user expects PE/PB)


def _apply_simple_criteria(
    stock_data: dict[str, dict[str, Any]],
    criteria: list[dict],
) -> list[str]:
    """Apply criteria filters directly for simple operator-based filtering."""
    matched = list(stock_data.keys())

    for c in criteria:
        field = c.get("field", "")
        op = c.get("operator", "gt")
        value = c.get("value")
        if value is None:
            continue
        op_fn = _OPERATOR_MAP.get(op)
        if not op_fn:
            continue
        threshold = value if op == "between" else float(value)
        matched = [sid for sid in matched if op_fn(stock_data[sid].get(field), threshold)]

    return matched


# ── CLI Entry Point (called via Python bridge) ───────────────────

def run_screening(config: dict) -> dict:
    """Run stock screening from a JSON config (called by TypeScript bridge).

    Accepts two formats:

    Format A (from stock-screen.ts):
    {
      "action": "screen" | "rank" | "rps",
      "market": "cn_stock",
      "criteria": [{"field": "pe", "operator": "lt", "value": 20}],
      "factors": ["pe", "roe"],
      "industry": "白酒",
      "minListingDays": 365,
      "rpsPeriod": 120,
      "sortBy": "roe",
      "limit": 20
    }

    Format B (legacy / direct):
    {
      "conditions": [...],
      "stock_data": { stock_id: { factor: value } },
      "trade_date": 20250101
    }

    Returns:
    {
      "results": [{"stock_id": "600519", "name": "贵州茅台", "pe": 30, ...}],
      "count": int,
      "total_screened": int,
      "errors": [...]  // optional
    }
    """
    import sys
    errors: list[str] = []

    # Detect format
    action = config.get("action", "screen")

    # Format B (legacy) — has stock_data pre-populated
    if "stock_data" in config and config["stock_data"]:
        conditions = config.get("conditions", [])
        stock_data = config["stock_data"]
        trade_date = config.get("trade_date", 20250101)
        listing_dates = config.get("listing_dates", {})
        industries = config.get("industries", {})

        ff = FactorFilter(conditions)
        matched_ids = ff.filter(stock_data, trade_date, listing_dates, industries)

        results = []
        for sid in matched_ids:
            row = {"stock_id": sid}
            row.update(stock_data.get(sid, {}))
            results.append(row)

        return {
            "results": results,
            "count": len(results),
            "total_screened": len(stock_data),
        }

    # Format A (from stock-screen.ts) — fetch data from akshare
    market = config.get("market", "cn_stock")
    criteria = config.get("criteria", [])
    factors = config.get("factors", [])
    industry = config.get("industry")
    min_listing_days = config.get("minListingDays")
    rps_period = config.get("rpsPeriod", 120)
    sort_by = config.get("sortBy")
    limit = config.get("limit", 20)

    # Step 1: Fetch batch data from Sina (all A-shares, price/volume)
    stock_data: dict[str, dict[str, Any]] = {}
    if market == "cn_stock":
        print("正在获取 A 股行情数据 (Sina)...", file=sys.stderr)
        stock_data, fetch_errors = _fetch_cn_stock_data()
        errors.extend(fetch_errors)
    else:
        errors.append(f"暂不支持市场: {market}，目前仅支持 cn_stock")

    if not stock_data:
        return {
            "results": [],
            "count": 0,
            "total_screened": 0,
            "errors": errors or ["无法获取股票数据"],
        }

    total_screened = len(stock_data)

    # Step 2: Pre-filter by price-based criteria (before expensive enrichment)
    price_criteria = [c for c in criteria if c.get("field", "") in
                      ("price", "change_pct", "volume", "turnover", "turnover_rate")]
    fundamental_criteria = [c for c in criteria if c.get("field", "") not in
                            ("price", "change_pct", "volume", "turnover", "turnover_rate")]

    matched_ids = list(stock_data.keys())

    # Filter out ST stocks and suspended stocks (price=0 when price data available)
    matched_ids = [
        sid for sid in matched_ids
        if "ST" not in stock_data[sid].get("name", "")
        and "*ST" not in stock_data[sid].get("name", "")
        and (stock_data[sid].get("price") is None or stock_data[sid]["price"] > 0)
    ]

    # Apply price-based criteria first
    if price_criteria:
        matched_ids = _apply_simple_criteria(
            {sid: stock_data[sid] for sid in matched_ids}, price_criteria
        )

    # Apply industry filter by name match
    if industry:
        matched_ids = [
            sid for sid in matched_ids
            if industry in stock_data.get(sid, {}).get("name", "")
        ]

    # Step 3: Enrich candidate stocks with PE/PB from Xueqiu
    # For ranking mode, we need more data to rank properly
    if action == "rank" and factors:
        enrich_count = min(len(matched_ids), 500)
    elif fundamental_criteria:
        enrich_count = min(len(matched_ids), 300)
    else:
        enrich_count = min(len(matched_ids), limit * 5)
    # Sort by turnover (most actively traded first) before enrichment
    matched_ids.sort(
        key=lambda sid: stock_data.get(sid, {}).get("turnover", 0) or 0,
        reverse=True,
    )
    candidates = matched_ids[:enrich_count]

    print(f"正在从雪球获取 {len(candidates)} 只股票的基本面数据...", file=sys.stderr)
    enrich_errors = _enrich_with_xueqiu(candidates, stock_data, max_enrich=enrich_count, max_workers=16)
    # Don't add these as user-facing errors (they're info/debug)
    for e in enrich_errors:
        print(f"  {e}", file=sys.stderr)

    # Step 4: Apply fundamental criteria (PE, PB, etc.)
    if fundamental_criteria:
        matched_ids = _apply_simple_criteria(
            {sid: stock_data[sid] for sid in candidates}, fundamental_criteria
        )
    else:
        matched_ids = candidates

    # Step 5: Compute derived fields and resolve factor aliases
    # Factor aliases: user-friendly names → actual data field names
    _FACTOR_ALIASES: dict[str, str] = {
        "market_cap": "total_market_val_yi",
        "marketcap": "total_market_val_yi",
        "总市值": "total_market_val_yi",
        "total_market_val": "total_market_val_yi",
        "roe": "roe",  # computed below
        "ROE": "roe",
        "revenue_growth": "revenue_growth_yoy",
        "营收增速": "revenue_growth_yoy",
        "profit_growth": "net_profit_growth_yoy",
        "利润增速": "net_profit_growth_yoy",
        "负债率": "debt_ratio",
        "毛利率": "gross_margin",
        "净利率": "net_margin",
    }
    # Factor direction: True = higher is better, False = lower is better
    _FACTOR_HIGHER_BETTER: dict[str, bool] = {
        "pe": False, "pe_ttm": False, "pe_static": False, "pb": False,
        "roe": True, "eps": True, "bvps": True,
        "total_market_val_yi": True, "total_market_val": True,
        "circulating_market_val_yi": True,
        "change_pct": True, "turnover_rate": True,
        "dividend_yield": True, "price": True,
        "volume": True, "turnover": True,
        "roa": True, "roic": True,
        "revenue_growth_yoy": True, "net_profit_growth_yoy": True,
        "eps_growth_yoy": True,
        "gross_margin": True, "net_margin": True, "operating_margin": True,
        "debt_ratio": False, "current_ratio": True, "debt_to_equity": False,
        "free_cash_flow": True, "operating_cf": True,
        "f_score": True, "ev_to_ebit": False,
    }

    # Resolve factor aliases
    resolved_factors = [_FACTOR_ALIASES.get(f, f) for f in factors]

    # Compute ROE = EPS / BVPS for stocks that have both
    for sid in matched_ids:
        d = stock_data.get(sid, {})
        eps_val = d.get("eps")
        bvps_val = d.get("bvps")
        if eps_val is not None and bvps_val is not None and bvps_val != 0:
            d["roe"] = round((eps_val / bvps_val) * 100, 2)  # in %

    # Step 6: Sort
    # Action: rank — percentile-based multi-factor ranking
    if action == "rank" and resolved_factors:
        # Build per-factor rankings using percentiles
        # Only include stocks that have valid data for ALL factors
        # Also filter out stocks with negative PE/PB (loss-making) when those are factors
        _POSITIVE_ONLY = {"pe", "pe_ttm", "pe_static", "pb", "roe"}
        rankable = [
            sid for sid in matched_ids
            if all(
                stock_data.get(sid, {}).get(f) is not None
                and isinstance(stock_data.get(sid, {}).get(f), (int, float))
                and math.isfinite(stock_data.get(sid, {}).get(f))
                and (f not in _POSITIVE_ONLY or stock_data.get(sid, {}).get(f, 0) > 0)
                for f in resolved_factors
            )
        ]

        if rankable:
            n = len(rankable)
            composite_scores: dict[str, float] = {sid: 0.0 for sid in rankable}

            for f in resolved_factors:
                higher_better = _FACTOR_HIGHER_BETTER.get(f, True)
                # Sort stocks by this factor
                sorted_by_f = sorted(
                    rankable,
                    key=lambda sid: stock_data[sid].get(f, 0) or 0,
                    reverse=higher_better,
                )
                # Assign percentile rank (0=worst, 1=best)
                for rank_idx, sid in enumerate(sorted_by_f):
                    percentile = 1.0 - (rank_idx / max(n - 1, 1))
                    composite_scores[sid] += percentile  # equal weight

            # Sort by composite score (higher = better)
            matched_ids = sorted(composite_scores, key=lambda sid: composite_scores[sid], reverse=True)
        else:
            errors.append("没有足够的因子数据进行排名（请确认已获取基本面数据）")

    # Action: rps — relative price strength ranking
    elif action == "rps":
        matched_ids.sort(
            key=lambda sid: stock_data.get(sid, {}).get("change_pct", 0) or 0,
            reverse=True,
        )

    # Default sort
    elif sort_by:
        reverse = True
        # For PE/PB, lower is typically better
        if sort_by in ("pe", "pb", "pe_ttm"):
            reverse = False
        matched_ids.sort(
            key=lambda sid: stock_data.get(sid, {}).get(sort_by, 0) or 0,
            reverse=reverse,
        )

    # Step 6: Limit results
    matched_ids = matched_ids[:limit]

    # Step 7: Build result objects
    results = []
    # Use resolved factors for data lookup, original factors for display keys
    display_map = dict(zip(factors, resolved_factors)) if factors else {}
    for sid in matched_ids:
        data = stock_data.get(sid, {})
        row: dict[str, Any] = {
            "stock_id": sid,
            "name": data.get("name", ""),
        }
        # Include requested factors (using original names for display, resolved for lookup)
        if factors:
            for orig_f, resolved_f in display_map.items():
                val = data.get(resolved_f)
                if val is not None:
                    row[orig_f] = round(val, 4) if isinstance(val, float) else val
        else:
            # Default fields
            for f in ["price", "pe", "pb", "total_market_val_yi",
                       "change_pct", "turnover_rate", "eps"]:
                val = data.get(f)
                if val is not None:
                    row[f] = round(val, 4) if isinstance(val, float) else val
        # Always include exchange
        row["exchange"] = data.get("exchange", "")

        results.append(row)

    response: dict[str, Any] = {
        "results": results,
        "count": len(results),
        "total_screened": total_screened,
    }
    if errors:
        response["errors"] = errors

    return response


if __name__ == "__main__":
    import json
    import sys

    config = json.loads(sys.stdin.read())
    result = run_screening(config)
    print(json.dumps(result, ensure_ascii=False))
