"""
peer_compare — Compare a stock against its industry peers.

Called from TypeScript stock_screen tool via python-bridge.
Input:
  { "code": "601231", "market": "cn_stock", "limit": 10 }

Output:
  {
    "target": { "code": ..., "name": ..., "industry": ..., "pe": ..., "pb": ..., ... },
    "industry": "...",
    "peers": [ ... ],
    "comparison": { ... },
    "rankings": { ... },
    "comparisonTable": [ ... ]
  }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import time
from typing import Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

os.environ.setdefault("no_proxy", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")
os.environ.setdefault("NO_PROXY", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")


def run_peer_compare(config: dict) -> dict:
    """Find and compare industry peers for a given stock."""
    code = config.get("code", "")
    market = config.get("market", "cn_stock")
    limit = config.get("limit", 10)

    if not code:
        return {"error": "请提供股票代码 (code)"}

    errors: list[str] = []

    # Step 1: Get target stock's fundamental data
    target_data = _fetch_fundamentals(code, market, errors)
    if not target_data:
        return {"error": f"无法获取 {code} 的基本面数据", "errors": errors}

    industry = target_data.get("industry", "")
    if not industry:
        return {
            "error": f"无法获取 {code} 的行业信息",
            "target": target_data,
            "errors": errors,
        }

    # Step 2: Find industry peers (raw list with basic data)
    raw_peers = _find_industry_peers(code, industry, market, limit, errors)

    # Step 3: Enrich peers with full fundamentals + fund flow (parallel)
    peers = _enrich_peers(raw_peers, market, errors)

    # Step 4: Fetch fund flow for target
    target_data["fundFlow"] = _fetch_fund_flow(code, errors)

    # Step 5: Compute comparison metrics with percentiles
    comparison = _compute_comparison(target_data, peers)

    # Step 6: Compute industry rankings for the target
    rankings = _compute_rankings(target_data, peers)

    # Step 7: Build comparison table
    comparison_table = _build_comparison_table(target_data, peers)

    return {
        "target": target_data,
        "industry": industry,
        "peerCount": len(peers),
        "peers": peers[:limit],
        "comparison": comparison,
        "rankings": rankings,
        "comparisonTable": comparison_table,
        "displayHint": (
            f"已找到{industry}行业{len(peers)}只同行标的。"
            "请以表格形式展示对比数据，包含：PE/PB/ROE/营收增速/主力资金净流入。"
            "在表格后给出目标股票的行业排名和估值分位数分析。"
        ),
        "errors": errors if errors else None,
    }


def _fetch_fundamentals(code: str, market: str, errors: list[str]) -> Optional[dict]:
    """Fetch fundamental data for a single stock."""
    try:
        from data_providers.financial_provider import FinancialProvider

        provider = FinancialProvider()
        snapshot = provider.fetch(code, market)
        if snapshot is None:
            return None

        return {
            "code": code,
            "name": snapshot.get("name", ""),
            "industry": snapshot.get("industry", ""),
            "pe": snapshot.get("pe"),
            "pb": snapshot.get("pb"),
            "ps": snapshot.get("ps"),
            "roe": snapshot.get("roe"),
            "roa": snapshot.get("roa"),
            "grossMargin": snapshot.get("gross_margin"),
            "netMargin": snapshot.get("net_margin"),
            "debtRatio": snapshot.get("debt_ratio"),
            "revenueGrowth": snapshot.get("revenue_growth_yoy"),
            "netProfitGrowth": snapshot.get("net_profit_growth_yoy"),
            "marketCap": snapshot.get("total_market_val"),
            "dividendYield": snapshot.get("dividend_yield"),
            "currentRatio": snapshot.get("current_ratio"),
        }
    except Exception as e:
        errors.append(f"获取 {code} 基本面失败: {e}")
        # Fallback: try yfinance
        try:
            from data_providers.yfinance_provider import get_fundamentals

            data = get_fundamentals(code, market)
            if data and "error" not in data:
                return {
                    "code": code,
                    "name": data.get("name", ""),
                    "industry": data.get("industry", data.get("sector", "")),
                    "pe": data.get("pe"),
                    "pb": data.get("pb"),
                    "roe": data.get("profitability", {}).get("roe"),
                    "marketCap": data.get("marketCap"),
                }
        except Exception as e2:
            errors.append(f"yfinance fallback 也失败: {e2}")
        return None


def _find_industry_peers(
    target_code: str, industry: str, market: str, limit: int, errors: list[str]
) -> list[dict]:
    """Find stocks in the same industry."""
    peers: list[dict] = []

    try:
        import akshare as ak

        # Get industry constituents
        try:
            df = ak.stock_board_industry_cons_em(symbol=industry)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = str(row.get("代码", ""))
                    if code == target_code:
                        continue
                    name = str(row.get("名称", ""))
                    peers.append({
                        "code": code,
                        "name": name,
                        "pe": _safe_float(row.get("市盈率-动态")),
                        "pb": _safe_float(row.get("市净率")),
                        "marketCap": _safe_float(row.get("总市值")),
                        "changePct": _safe_float(row.get("涨跌幅")),
                    })
                    if len(peers) >= limit * 3:  # fetch more for sorting
                        break
        except Exception as e:
            errors.append(f"获取行业成分股失败: {e}")

        if not peers:
            # Fallback: try stock_board_industry_name_em to find the board
            try:
                boards = ak.stock_board_industry_name_em()
                if boards is not None:
                    matching = boards[boards["板块名称"].str.contains(industry[:2], na=False)]
                    if not matching.empty:
                        board_name = matching.iloc[0]["板块名称"]
                        df = ak.stock_board_industry_cons_em(symbol=board_name)
                        if df is not None and not df.empty:
                            for _, row in df.iterrows():
                                code = str(row.get("代码", ""))
                                if code == target_code:
                                    continue
                                peers.append({
                                    "code": code,
                                    "name": str(row.get("名称", "")),
                                    "pe": _safe_float(row.get("市盈率-动态")),
                                    "pb": _safe_float(row.get("市净率")),
                                    "marketCap": _safe_float(row.get("总市值")),
                                })
                                if len(peers) >= limit * 3:
                                    break
            except Exception as e2:
                errors.append(f"行业板块查询 fallback 失败: {e2}")

    except ImportError:
        errors.append("akshare 未安装，无法获取行业成分股")

    # Sort by market cap descending (larger peers first), take top N
    peers.sort(key=lambda p: p.get("marketCap") or 0, reverse=True)
    return peers[:limit]


def _enrich_peers(peers: list[dict], market: str, errors: list[str]) -> list[dict]:
    """Enrich peer data with full fundamentals and fund flow in parallel."""
    if not peers:
        return peers

    def _enrich_single(peer: dict) -> dict:
        code = peer["code"]
        # Enrich with fundamentals
        try:
            from data_providers.financial_provider import FinancialProvider
            provider = FinancialProvider()
            snapshot = provider.fetch(code, market)
            if snapshot:
                peer["roe"] = peer.get("roe") or snapshot.get("roe")
                peer["roa"] = snapshot.get("roa")
                peer["grossMargin"] = snapshot.get("gross_margin")
                peer["netMargin"] = snapshot.get("net_margin")
                peer["debtRatio"] = snapshot.get("debt_ratio")
                peer["revenueGrowth"] = snapshot.get("revenue_growth_yoy")
                peer["netProfitGrowth"] = snapshot.get("net_profit_growth_yoy")
                peer["dividendYield"] = snapshot.get("dividend_yield")
                if not peer.get("name"):
                    peer["name"] = snapshot.get("name", "")
                if not peer.get("industry"):
                    peer["industry"] = snapshot.get("industry", "")
        except Exception as e:
            errors.append(f"获取 {code} 基本面失败: {e}")

        # Enrich with fund flow
        peer["fundFlow"] = _fetch_fund_flow(code, errors)
        return peer

    # Use thread pool to fetch in parallel (max 5 concurrent)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_enrich_single, p): p for p in peers}
        enriched = []
        for future in as_completed(futures):
            try:
                enriched.append(future.result())
            except Exception:
                enriched.append(futures[future])

    # Re-sort by market cap to maintain order
    enriched.sort(key=lambda p: p.get("marketCap") or 0, reverse=True)
    return enriched


def _fetch_fund_flow(code: str, errors: list[str]) -> Optional[dict]:
    """Fetch recent main fund flow for a stock (net inflow of 主力/超大单/大单)."""
    try:
        import akshare as ak

        market_code = "sh" if code.startswith("6") else "sz"
        df = ak.stock_individual_fund_flow(stock=code, market=market_code)
        if df is None or df.empty:
            return None

        # Get latest day's flow data
        latest = df.iloc[-1]
        # Sum recent 5 days
        recent = df.tail(5)

        def _sum_col(df_slice, col_name):
            if col_name in df_slice.columns:
                vals = df_slice[col_name].dropna()
                return round(float(vals.sum()), 2) if len(vals) > 0 else None
            return None

        def _get_val(row, col_name):
            if col_name in row.index:
                v = row[col_name]
                return round(float(v), 2) if v is not None and v == v else None
            return None

        return {
            "todayMainNet": _get_val(latest, "主力净流入-净额"),
            "todayMainPct": _get_val(latest, "主力净流入-净占比"),
            "todaySuperNet": _get_val(latest, "超大单净流入-净额"),
            "todayBigNet": _get_val(latest, "大单净流入-净额"),
            "fiveDayMainNet": _sum_col(recent, "主力净流入-净额"),
            "fiveDayMainPct": _sum_col(recent, "主力净流入-净占比"),
        }
    except Exception as e:
        errors.append(f"获取 {code} 资金流向失败: {e}")
        return None


def _compute_comparison(target: dict, peers: list[dict]) -> dict:
    """Compute how the target compares against its peers with percentiles."""
    if not peers:
        return {"note": "无同行数据可比较"}

    metrics = [
        "pe", "pb", "roe", "grossMargin", "netMargin",
        "debtRatio", "revenueGrowth", "netProfitGrowth",
        "dividendYield", "marketCap",
    ]
    comparison: dict[str, Any] = {}

    for metric in metrics:
        target_val = target.get(metric)
        if target_val is None:
            continue

        peer_vals = [
            p.get(metric) for p in peers
            if p.get(metric) is not None
        ]
        if not peer_vals:
            continue

        avg = sum(peer_vals) / len(peer_vals)
        sorted_vals = sorted(peer_vals)
        median = sorted_vals[len(sorted_vals) // 2]

        # Percentile among all (including target)
        all_vals = peer_vals + [target_val]
        pct = _percentile_rank(target_val, all_vals)

        comparison[metric] = {
            "target": round(target_val, 2) if isinstance(target_val, float) else target_val,
            "peerAvg": round(avg, 2),
            "peerMedian": round(median, 2),
            "peerMin": round(min(peer_vals), 2),
            "peerMax": round(max(peer_vals), 2),
            "percentile": pct,
            "sampleSize": len(peer_vals),
        }

    # Fund flow comparison
    target_flow = target.get("fundFlow")
    if target_flow and target_flow.get("todayMainNet") is not None:
        peer_flows = [
            p["fundFlow"]["todayMainNet"]
            for p in peers
            if p.get("fundFlow") and p["fundFlow"].get("todayMainNet") is not None
        ]
        if peer_flows:
            all_flows = peer_flows + [target_flow["todayMainNet"]]
            comparison["todayMainNet"] = {
                "target": target_flow["todayMainNet"],
                "peerAvg": round(sum(peer_flows) / len(peer_flows), 2),
                "peerMedian": round(sorted(peer_flows)[len(peer_flows) // 2], 2),
                "peerMin": round(min(peer_flows), 2),
                "peerMax": round(max(peer_flows), 2),
                "percentile": _percentile_rank(target_flow["todayMainNet"], all_flows),
                "sampleSize": len(peer_flows),
            }

    return comparison


def _compute_rankings(target: dict, peers: list[dict]) -> dict:
    """Compute where the target ranks among all industry peers."""
    if not peers:
        return {"note": "无同行数据可排名"}

    all_stocks = [target] + peers
    total = len(all_stocks)
    rankings: dict[str, Any] = {"total": total}

    # Metrics where lower is better
    lower_is_better = {"pe", "pb", "debtRatio"}
    # Metrics where higher is better
    higher_is_better = {"roe", "grossMargin", "netMargin", "revenueGrowth",
                        "netProfitGrowth", "dividendYield", "marketCap"}

    for metric in list(lower_is_better) + list(higher_is_better):
        target_val = target.get(metric)
        if target_val is None:
            continue

        vals_with_codes = [
            (s.get(metric), s.get("code", ""))
            for s in all_stocks
            if s.get(metric) is not None
        ]
        if len(vals_with_codes) < 2:
            continue

        reverse = metric in higher_is_better
        vals_with_codes.sort(key=lambda x: x[0] if x[0] is not None else float('-inf'), reverse=reverse)

        rank = next(
            (i + 1 for i, (v, c) in enumerate(vals_with_codes) if c == target.get("code")),
            None,
        )
        if rank is not None:
            rankings[metric] = {
                "rank": rank,
                "total": len(vals_with_codes),
                "label": f"{rank}/{len(vals_with_codes)}",
                "percentile": round((1 - (rank - 1) / len(vals_with_codes)) * 100),
            }

    return rankings


def _build_comparison_table(target: dict, peers: list[dict]) -> list[dict]:
    """Build a structured comparison table for display."""
    rows = []
    all_stocks = [target] + peers

    for stock in all_stocks:
        flow = stock.get("fundFlow") or {}
        row = {
            "code": stock.get("code", ""),
            "name": stock.get("name", ""),
            "isTarget": stock.get("code") == target.get("code"),
            "pe": _round_val(stock.get("pe")),
            "pb": _round_val(stock.get("pb")),
            "roe": _round_val(stock.get("roe")),
            "revenueGrowth": _round_val(stock.get("revenueGrowth")),
            "netProfitGrowth": _round_val(stock.get("netProfitGrowth")),
            "grossMargin": _round_val(stock.get("grossMargin")),
            "netMargin": _round_val(stock.get("netMargin")),
            "debtRatio": _round_val(stock.get("debtRatio")),
            "marketCap": _round_val(stock.get("marketCap")),
            "dividendYield": _round_val(stock.get("dividendYield")),
            "todayMainNet": flow.get("todayMainNet"),
            "fiveDayMainNet": flow.get("fiveDayMainNet"),
        }
        rows.append(row)

    return rows


def _round_val(val: Any) -> Any:
    """Round float values for display."""
    if val is None:
        return None
    if isinstance(val, float):
        return round(val, 2)
    return val


def _percentile_rank(value: float, values: list[float]) -> int:
    """What percentile is the value among the reference values? (0-100)"""
    below = sum(1 for v in values if v < value)
    return round(below / len(values) * 100) if values else 0


def _safe_float(val: Any) -> Optional[float]:
    """Safely convert to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def main():
    config_str = os.environ.get("QUANT_CONFIG", "")
    if not config_str:
        config_str = sys.stdin.read()
    try:
        config = json.loads(config_str) if config_str else {}
        result = run_peer_compare(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        print(json.dumps({
            "error": f"同行对比失败: {traceback.format_exc()[:500]}"
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
