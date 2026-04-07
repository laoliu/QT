"""
margin_data — 融资融券数据查询引擎。

Actions:
  query   — 查询个股融资融券数据（融资余额、融券余量、融资买入额、融券卖出量）
  trend   — 两融余额变动趋势（近N日）
  ranking — 全市场两融数据排行（融资净买入TOP / 融券净卖出TOP）
  alert   — 两融变动异常检测（融资余额大幅增加/减少的标的）

Input:
  { "action": "query" | "trend" | "ranking" | "alert",
    "symbol": "600519", "days": 30, "limit": 20, "threshold": 10 }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

os.environ.setdefault("no_proxy", "eastmoney.com,datacenter-web.eastmoney.com")
os.environ.setdefault("NO_PROXY", "eastmoney.com,datacenter-web.eastmoney.com")


def run_margin_data(config: dict) -> dict:
    """Dispatch margin data analysis based on action."""
    action = config.get("action", "query")
    errors: list[str] = []

    if action == "query":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        days = config.get("days", 30)
        return _query_margin(symbol, days, errors)
    elif action == "trend":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        days = config.get("days", 30)
        return _margin_trend(symbol, days, errors)
    elif action == "ranking":
        limit = config.get("limit", 20)
        return _margin_ranking(limit, errors)
    elif action == "alert":
        days = config.get("days", 5)
        threshold = config.get("threshold", 10)
        limit = config.get("limit", 20)
        return _margin_alert(days, threshold, limit, errors)
    else:
        return {"error": f"未知action: {action}"}


def _query_margin(symbol: str, days: int, errors: list[str]) -> dict:
    """查询个股融资融券明细数据。"""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    records: list[dict] = []
    exchange = _detect_exchange(symbol)

    try:
        if exchange == "sz":
            df = ak.stock_margin_detail_szse(date="")
            if df is not None and not df.empty:
                code_col = _find_col(df, ["证券代码", "标的证券代码"])
                if code_col:
                    df_stock = df[df[code_col].astype(str).str.strip() == symbol.strip()]
                    for _, row in df_stock.tail(days).iterrows():
                        records.append(_parse_margin_row(row))
        else:
            df = ak.stock_margin_detail_sse(date="")
            if df is not None and not df.empty:
                code_col = _find_col(df, ["标的证券代码", "证券代码"])
                if code_col:
                    df_stock = df[df[code_col].astype(str).str.strip() == symbol.strip()]
                    for _, row in df_stock.tail(days).iterrows():
                        records.append(_parse_margin_row(row))
    except Exception as e:
        errors.append(f"个股融资融券数据获取失败: {e}")

    # 如果交易所接口未返回数据，尝试通用接口
    if not records:
        try:
            df = ak.stock_margin_underlying_info_szse()
            if df is not None and not df.empty:
                pass  # 仅作为检查用
        except Exception:
            pass

        try:
            df = ak.stock_margin_detail_sse(date="") if exchange == "sh" else ak.stock_margin_detail_szse(date="")
            if df is not None and not df.empty:
                code_col = _find_col(df, ["标的证券代码", "证券代码", "股票代码"])
                if code_col:
                    df_stock = df[df[code_col].astype(str).str.strip() == symbol.strip()]
                    for _, row in df_stock.tail(days).iterrows():
                        records.append(_parse_margin_row(row))
        except Exception as e:
            errors.append(f"备用接口获取失败: {e}")

    if not records:
        return {"error": f"无法获取 {symbol} 的融资融券数据", "errors": errors}

    return {
        "action": "query",
        "symbol": symbol,
        "count": len(records),
        "records": records,
        "errors": errors if errors else None,
    }


def _margin_trend(symbol: str, days: int, errors: list[str]) -> dict:
    """查询个股两融余额变动趋势。"""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    trends: list[dict] = []

    try:
        # 尝试使用融资融券汇总数据接口
        exchange = _detect_exchange(symbol)
        if exchange == "sh":
            df = ak.stock_margin_detail_sse(date="")
        else:
            df = ak.stock_margin_detail_szse(date="")

        if df is not None and not df.empty:
            code_col = _find_col(df, ["标的证券代码", "证券代码", "股票代码"])
            if code_col:
                df_stock = df[df[code_col].astype(str).str.strip() == symbol.strip()]
                df_stock = df_stock.tail(days)
                for _, row in df_stock.iterrows():
                    rec = _parse_margin_row(row)
                    trends.append(rec)
    except Exception as e:
        errors.append(f"两融趋势数据获取失败: {e}")

    if not trends:
        return {"error": f"无法获取 {symbol} 的两融趋势数据", "errors": errors}

    # 计算变动率
    if len(trends) >= 2:
        for i in range(1, len(trends)):
            prev_rzye = trends[i - 1].get("rzye")  # 融资余额
            curr_rzye = trends[i].get("rzye")
            if prev_rzye and curr_rzye and prev_rzye > 0:
                trends[i]["rzyeChangePct"] = round((curr_rzye - prev_rzye) / prev_rzye * 100, 4)

    return {
        "action": "trend",
        "symbol": symbol,
        "days": days,
        "count": len(trends),
        "trends": trends,
        "errors": errors if errors else None,
    }


def _margin_ranking(limit: int, errors: list[str]) -> dict:
    """全市场两融排行（融资净买入TOP + 融券净卖出TOP）。"""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    rz_top: list[dict] = []
    rq_top: list[dict] = []

    # 获取沪市数据
    try:
        df_sh = ak.stock_margin_detail_sse(date="")
        if df_sh is not None and not df_sh.empty:
            _append_ranking_data(df_sh, rz_top, rq_top, errors, "sh")
    except Exception as e:
        errors.append(f"沪市融资融券排名获取失败: {e}")

    # 获取深市数据
    try:
        df_sz = ak.stock_margin_detail_szse(date="")
        if df_sz is not None and not df_sz.empty:
            _append_ranking_data(df_sz, rz_top, rq_top, errors, "sz")
    except Exception as e:
        errors.append(f"深市融资融券排名获取失败: {e}")

    # 排序并截取
    rz_top.sort(key=lambda x: x.get("rzmre", 0) or 0, reverse=True)
    rq_top.sort(key=lambda x: x.get("rqmcl", 0) or 0, reverse=True)
    rz_top = rz_top[:limit]
    rq_top = rq_top[:limit]

    if not rz_top and not rq_top:
        return {"error": "无法获取两融排行数据", "errors": errors}

    return {
        "action": "ranking",
        "rzBuyTop": rz_top,
        "rzBuyTopCount": len(rz_top),
        "rqSellTop": rq_top,
        "rqSellTopCount": len(rq_top),
        "errors": errors if errors else None,
    }


def _margin_alert(days: int, threshold: float, limit: int, errors: list[str]) -> dict:
    """两融异动检测：融资余额大幅变动的标的。"""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    alerts_increase: list[dict] = []
    alerts_decrease: list[dict] = []

    # 获取沪深两市数据
    all_records: list[dict] = []

    for fetch_fn, exchange in [
        (lambda: ak.stock_margin_detail_sse(date=""), "sh"),
        (lambda: ak.stock_margin_detail_szse(date=""), "sz"),
    ]:
        try:
            df = fetch_fn()
            if df is not None and not df.empty:
                code_col = _find_col(df, ["标的证券代码", "证券代码", "股票代码"])
                name_col = _find_col(df, ["标的证券简称", "证券简称", "股票简称", "名称"])
                rzye_col = _find_col(df, ["融资余额", "融资余额(元)"])
                rzmre_col = _find_col(df, ["融资买入额", "融资买入额(元)", "融资买入金额"])
                rqyl_col = _find_col(df, ["融券余量", "融券余量(股)"])

                if code_col and rzye_col:
                    for _, row in df.iterrows():
                        code = str(row.get(code_col, "")).strip()
                        name = str(row.get(name_col, "")).strip() if name_col else ""
                        rzye = _safe_float(row.get(rzye_col))
                        rzmre = _safe_float(row.get(rzmre_col)) if rzmre_col else None
                        rqyl = _safe_float(row.get(rqyl_col)) if rqyl_col else None

                        if code and rzye is not None:
                            all_records.append({
                                "code": code,
                                "name": name,
                                "exchange": exchange,
                                "rzye": rzye,
                                "rzmre": rzmre,
                                "rqyl": rqyl,
                            })
        except Exception as e:
            errors.append(f"{exchange}市融资融券数据获取失败: {e}")

    # 按融资买入额排序，检测异动
    # 对于当日数据，融资买入额/融资余额比例超过阈值视为异动
    for rec in all_records:
        rzye = rec.get("rzye", 0) or 0
        rzmre = rec.get("rzmre", 0) or 0
        if rzye > 0 and rzmre != 0:
            change_pct = abs(rzmre) / rzye * 100
            rec["changePct"] = round(change_pct, 4)
            if change_pct >= threshold:
                if rzmre > 0:
                    alerts_increase.append(rec)
                else:
                    alerts_decrease.append(rec)

    alerts_increase.sort(key=lambda x: x.get("changePct", 0), reverse=True)
    alerts_decrease.sort(key=lambda x: x.get("changePct", 0), reverse=True)
    alerts_increase = alerts_increase[:limit]
    alerts_decrease = alerts_decrease[:limit]

    return {
        "action": "alert",
        "threshold": threshold,
        "increase": alerts_increase,
        "increaseCount": len(alerts_increase),
        "decrease": alerts_decrease,
        "decreaseCount": len(alerts_decrease),
        "totalScanned": len(all_records),
        "errors": errors if errors else None,
    }


# ── Helpers ──────────────────────────────────────────────────────────


def _detect_exchange(symbol: str) -> str:
    """根据股票代码判断交易所。"""
    code = symbol.strip()
    if code.startswith(("sh", "sz", "SH", "SZ")):
        return code[:2].lower()
    if code.startswith(("6", "5", "9", "7")):
        return "sh"
    return "sz"


def _find_col(df, candidates: list[str]) -> str | None:
    """在 DataFrame 中查找第一个匹配的列名。"""
    for c in candidates:
        if c in df.columns:
            return c
    return None


def _parse_margin_row(row) -> dict:
    """从一行数据中解析融资融券字段。"""
    date_col_candidates = ["信用交易日期", "交易日期", "日期", "date"]
    date_val = ""
    for c in date_col_candidates:
        v = row.get(c)
        if v is not None:
            date_val = str(v)[:10]
            break

    code_col_candidates = ["标的证券代码", "证券代码", "股票代码"]
    code_val = ""
    for c in code_col_candidates:
        v = row.get(c)
        if v is not None:
            code_val = str(v).strip()
            break

    name_col_candidates = ["标的证券简称", "证券简称", "股票简称", "名称"]
    name_val = ""
    for c in name_col_candidates:
        v = row.get(c)
        if v is not None:
            name_val = str(v).strip()
            break

    return {
        "date": date_val,
        "code": code_val,
        "name": name_val,
        "rzye": _safe_float(row.get("融资余额") or row.get("融资余额(元)")),
        "rzmre": _safe_float(row.get("融资买入额") or row.get("融资买入额(元)") or row.get("融资买入金额")),
        "rzche": _safe_float(row.get("融资偿还额") or row.get("融资偿还额(元)") or row.get("融资偿还金额")),
        "rzjme": _safe_float(row.get("融资净买入") or row.get("融资净买入额")),
        "rqyl": _safe_float(row.get("融券余量") or row.get("融券余量(股)")),
        "rqmcl": _safe_float(row.get("融券卖出量") or row.get("融券卖出量(股)")),
        "rqchl": _safe_float(row.get("融券偿还量") or row.get("融券偿还量(股)")),
        "rqye": _safe_float(row.get("融券余额") or row.get("融券余额(元)")),
    }


def _append_ranking_data(df, rz_top: list, rq_top: list, errors: list, exchange: str):
    """从 DataFrame 中提取排名数据追加到列表。"""
    code_col = _find_col(df, ["标的证券代码", "证券代码", "股票代码"])
    name_col = _find_col(df, ["标的证券简称", "证券简称", "股票简称", "名称"])
    rzye_col = _find_col(df, ["融资余额", "融资余额(元)"])
    rzmre_col = _find_col(df, ["融资买入额", "融资买入额(元)", "融资买入金额"])
    rqyl_col = _find_col(df, ["融券余量", "融券余量(股)"])
    rqmcl_col = _find_col(df, ["融券卖出量", "融券卖出量(股)"])

    if not code_col:
        return

    for _, row in df.iterrows():
        code = str(row.get(code_col, "")).strip()
        name = str(row.get(name_col, "")).strip() if name_col else ""
        rzye = _safe_float(row.get(rzye_col)) if rzye_col else None
        rzmre = _safe_float(row.get(rzmre_col)) if rzmre_col else None
        rqyl = _safe_float(row.get(rqyl_col)) if rqyl_col else None
        rqmcl = _safe_float(row.get(rqmcl_col)) if rqmcl_col else None

        rec = {
            "code": code,
            "name": name,
            "exchange": exchange,
            "rzye": rzye,
            "rzmre": rzmre,
            "rqyl": rqyl,
            "rqmcl": rqmcl,
        }

        if rzmre is not None:
            rz_top.append(rec)
        if rqmcl is not None:
            rq_top.append(rec)


def _safe_float(val: Any) -> float | None:
    """Convert value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_margin_data(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
