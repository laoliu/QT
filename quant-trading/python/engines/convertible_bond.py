"""
convertible_bond — A-share convertible bond (可转债) analysis.

Actions:
  list        — List all tradable convertible bonds with key metrics
  detail      — Detailed analysis for a specific CB
  dual_low    — Dual-low strategy screening (双低策略: low price + low premium)
  forced_redeem — Forced redemption alert (强赎预警)
  arbitrage   — Conversion arbitrage opportunities (转股套利)

Data sourced from akshare public APIs.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from typing import Any


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _df_to_records(df, limit: int = 50) -> list[dict[str, Any]]:
    """Convert DataFrame rows to serializable dicts."""
    records = []
    for _, row in df.head(limit).iterrows():
        record: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            if hasattr(val, "isoformat"):
                record[col] = str(val)[:10]
            elif isinstance(val, float) and val != val:
                record[col] = None
            else:
                record[col] = val
        records.append(record)
    return records


def _get_cb_data():
    """Fetch convertible bond market data."""
    import akshare as ak

    df = None
    source = ""

    try:
        df = ak.bond_zh_hs_cov_spot()
        source = "bond_zh_hs_cov_spot"
    except Exception:
        pass

    if df is None or df.empty:
        try:
            df = ak.bond_cov_comparison()
            source = "bond_cov_comparison"
        except Exception:
            pass

    if df is None or df.empty:
        try:
            df = ak.bond_zh_cov()
            source = "bond_zh_cov"
        except Exception:
            pass

    return df, source


def list_bonds(limit: int = 50, sort_by: str = "price") -> dict[str, Any]:
    """List all tradable convertible bonds."""
    try:
        df, source = _get_cb_data()

        if df is None or df.empty:
            return {"error": "无法获取可转债数据", "bonds": []}

        # Sort
        sort_col = None
        if sort_by == "price":
            for col in df.columns:
                if "价" in col and ("最新" in col or "现" in col):
                    sort_col = col
                    break
        elif sort_by == "premium":
            for col in df.columns:
                if "溢价" in col:
                    sort_col = col
                    break

        if sort_col:
            df[sort_col] = df[sort_col].apply(lambda x: _safe_float(x, float("inf")))
            df = df.sort_values(sort_col, ascending=True)

        records = _df_to_records(df, limit)
        return {
            "source": source,
            "total": len(df),
            "count": len(records),
            "bonds": records,
        }

    except Exception as e:
        return {"error": f"获取可转债列表失败: {str(e)}"}


def detail(symbol: str) -> dict[str, Any]:
    """Detailed analysis for a specific convertible bond."""
    try:
        df, source = _get_cb_data()

        if df is None or df.empty:
            return {"error": "无法获取可转债数据"}

        # Find the bond by code
        code_col = None
        for col in df.columns:
            if "代码" in col or "code" in col.lower():
                code_col = col
                break

        if not code_col:
            return {"error": "数据格式异常，未找到代码列"}

        match = df[df[code_col].astype(str).str.contains(symbol)]
        if match.empty:
            return {"error": f"未找到可转债 {symbol}"}

        row = match.iloc[0]
        info: dict[str, Any] = {"symbol": symbol}
        for col in df.columns:
            val = row[col]
            if hasattr(val, "isoformat"):
                info[col] = str(val)[:10]
            elif isinstance(val, float) and val != val:
                info[col] = None
            else:
                info[col] = val

        return info

    except Exception as e:
        return {"error": f"获取可转债详情失败: {str(e)}"}


def dual_low(limit: int = 20, max_price: float = 130.0, max_premium: float = 30.0) -> dict[str, Any]:
    """Dual-low strategy: screen for low price + low premium convertible bonds."""
    try:
        df, source = _get_cb_data()

        if df is None or df.empty:
            return {"error": "无法获取可转债数据", "bonds": []}

        # Find price and premium columns
        price_col = None
        premium_col = None

        for col in df.columns:
            if price_col is None and "价" in col and ("最新" in col or "现" in col or "收盘" in col):
                price_col = col
            if premium_col is None and "溢价" in col and "率" in col:
                premium_col = col

        if not price_col:
            # Fallback
            for col in df.columns:
                if "价" in col:
                    price_col = col
                    break

        if not premium_col:
            for col in df.columns:
                if "溢价" in col:
                    premium_col = col
                    break

        if not price_col or not premium_col:
            return {
                "error": f"数据列不匹配 (price_col={price_col}, premium_col={premium_col})",
                "available_columns": list(df.columns),
            }

        df["_price"] = df[price_col].apply(lambda x: _safe_float(x, 999))
        df["_premium"] = df[premium_col].apply(lambda x: _safe_float(x, 999))

        # Filter
        filtered = df[(df["_price"] <= max_price) & (df["_premium"] <= max_premium)].copy()

        # Calculate dual-low score = price + premium_rate (lower is better)
        filtered["_dual_low_score"] = filtered["_price"] + filtered["_premium"]
        filtered = filtered.sort_values("_dual_low_score", ascending=True)

        records = _df_to_records(filtered, limit)

        # Remove internal columns from output
        for r in records:
            r.pop("_price", None)
            r.pop("_premium", None)
            r.pop("_dual_low_score", None)

        return {
            "strategy": "双低策略",
            "max_price": max_price,
            "max_premium_rate": max_premium,
            "total_market": len(df),
            "matched": len(filtered),
            "count": len(records),
            "bonds": records,
        }

    except Exception as e:
        return {"error": f"双低筛选失败: {str(e)}"}


def forced_redeem(limit: int = 20) -> dict[str, Any]:
    """Screen for convertible bonds at risk of forced redemption (强赎预警)."""
    try:
        df, source = _get_cb_data()

        if df is None or df.empty:
            return {"error": "无法获取可转债数据", "bonds": []}

        # Forced redemption typically triggers when stock price > 130% of conversion price
        # for 15 out of 30 consecutive trading days
        # Look for conversion value or related column
        conv_col = None
        price_col = None

        for col in df.columns:
            if conv_col is None and ("转股价值" in col or "转换" in col):
                conv_col = col
            if price_col is None and "价" in col and ("最新" in col or "现" in col):
                price_col = col

        if conv_col:
            df["_conv_value"] = df[conv_col].apply(lambda x: _safe_float(x))
            # Filter bonds where conversion value > 130 (high forced-redeem risk)
            at_risk = df[df["_conv_value"] >= 130].sort_values("_conv_value", ascending=False)
            records = _df_to_records(at_risk, limit)
            for r in records:
                r.pop("_conv_value", None)

            return {
                "description": "转股价值>=130元的可转债面临强赎风险",
                "count": len(records),
                "bonds": records,
            }
        else:
            return {
                "error": "当前数据源不包含转股价值字段",
                "hint": "建议查看可转债详情中的转股相关数据",
            }

    except Exception as e:
        return {"error": f"强赎预警查询失败: {str(e)}"}


def arbitrage(limit: int = 20) -> dict[str, Any]:
    """Conversion arbitrage: bonds where conversion value > bond price (negative premium)."""
    try:
        df, source = _get_cb_data()

        if df is None or df.empty:
            return {"error": "无法获取可转债数据", "bonds": []}

        premium_col = None
        for col in df.columns:
            if "溢价" in col and "率" in col:
                premium_col = col
                break

        if not premium_col:
            for col in df.columns:
                if "溢价" in col:
                    premium_col = col
                    break

        if premium_col:
            df["_premium"] = df[premium_col].apply(lambda x: _safe_float(x, 999))
            # Negative premium = arbitrage opportunity
            arb = df[df["_premium"] < 0].sort_values("_premium", ascending=True)
            records = _df_to_records(arb, limit)
            for r in records:
                r.pop("_premium", None)

            return {
                "description": "转股溢价率为负的可转债（存在折价转股套利空间）",
                "count": len(records),
                "bonds": records,
                "warning": "套利需考虑T+1转股限制和手续费，实际收益可能低于理论值",
            }
        else:
            return {"error": "当前数据源不包含溢价率字段"}

    except Exception as e:
        return {"error": f"套利分析失败: {str(e)}"}


def main():
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}

    action = args.get("action", "list")
    symbol = args.get("symbol", "")
    limit = args.get("limit", 30)

    if action == "list":
        result = list_bonds(limit, args.get("sort_by", "price"))
    elif action == "detail":
        result = detail(symbol)
    elif action == "dual_low":
        result = dual_low(limit, args.get("max_price", 130), args.get("max_premium", 30))
    elif action == "forced_redeem":
        result = forced_redeem(limit)
    elif action == "arbitrage":
        result = arbitrage(limit)
    else:
        result = {"error": f"未知action: {action}"}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
