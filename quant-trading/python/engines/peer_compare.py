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
    "comparison": { ... }
  }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any, Optional


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

    # Step 2: Find industry peers
    peers = _find_industry_peers(code, industry, market, limit, errors)

    # Step 3: Compute comparison metrics
    comparison = _compute_comparison(target_data, peers)

    return {
        "target": target_data,
        "industry": industry,
        "peerCount": len(peers),
        "peers": peers[:limit],
        "comparison": comparison,
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
                    if len(peers) >= limit * 2:  # fetch more for sorting
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
                                if len(peers) >= limit * 2:
                                    break
            except Exception as e2:
                errors.append(f"行业板块查询 fallback 失败: {e2}")

    except ImportError:
        errors.append("akshare 未安装，无法获取行业成分股")

    # Sort by market cap descending (larger peers first)
    peers.sort(key=lambda p: p.get("marketCap") or 0, reverse=True)
    return peers[:limit]


def _compute_comparison(target: dict, peers: list[dict]) -> dict:
    """Compute how the target compares against its peers."""
    if not peers:
        return {"note": "无同行数据可比较"}

    metrics = ["pe", "pb", "roe", "grossMargin", "netMargin", "debtRatio", "revenueGrowth"]
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

        comparison[metric] = {
            "target": round(target_val, 2) if isinstance(target_val, float) else target_val,
            "peerAvg": round(avg, 2),
            "peerMedian": round(median, 2),
            "peerMin": round(min(peer_vals), 2),
            "peerMax": round(max(peer_vals), 2),
            "percentile": _percentile_rank(target_val, peer_vals),
        }

    return comparison


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
