"""
market_overview — A-share market breadth and overview engine (大盘概览).

Provides:
  - advance/decline statistics (涨跌家数)
  - limit-up / limit-down counts (涨停/跌停统计)
  - market breadth indicators (市场宽度)
  - major index quotes (主要指数行情)
  - composite market health score

Called from TypeScript market_overview tool via python-bridge.
Input:
  { "action": "overview" | "breadth" | "index" }

Output varies by action:
  overview → { advance, decline, limitUp, limitDown, ... }
  breadth  → { advanceDeclineRatio, breadthScore, newHighLow, ... }
  index    → { indexes: [{code, name, price, changePct}] }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

# Proxy bypass
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_market_overview(config: dict) -> dict:
    """Dispatch market overview analysis."""
    action = config.get("action", "overview")
    errors: list[str] = []

    if action == "overview":
        return _market_overview(errors)
    elif action == "breadth":
        return _market_breadth(errors)
    elif action == "index":
        return _index_quotes(errors)
    else:
        return {"error": f"未知action: {action}"}


def _market_overview(errors: list[str]) -> dict:
    """Get comprehensive market overview: advance/decline, limit-up/down, volume."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    result: dict[str, Any] = {"action": "overview"}

    # 1. Get advance/decline from stock board changes
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            changes = df["涨跌幅"].dropna().astype(float)
            total = len(changes)
            advance = int((changes > 0).sum())
            decline = int((changes < 0).sum())
            flat = total - advance - decline

            # Limit-up / limit-down detection
            limit_up = 0
            limit_down = 0
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                chg = _safe_float(row.get("涨跌幅"))
                if chg is None:
                    continue
                threshold = _limit_threshold(code)
                if chg >= threshold - 0.5:
                    limit_up += 1
                elif chg <= -(threshold - 0.5):
                    limit_down += 1

            # Volume statistics
            total_amount = _safe_float(df["成交额"].sum()) or 0

            result.update({
                "totalStocks": total,
                "advance": advance,
                "decline": decline,
                "flat": flat,
                "advanceDeclineRatio": round(advance / decline, 2) if decline > 0 else advance,
                "limitUp": limit_up,
                "limitDown": limit_down,
                "totalAmount": round(total_amount / 1e8, 2),  # 亿元
                "totalAmountUnit": "亿元",
                "averageChangePct": round(float(changes.mean()), 2),
                "medianChangePct": round(float(changes.median()), 2),
            })

            # Market health score (0-100)
            ad_ratio = advance / total if total > 0 else 0.5
            lu_ld_ratio = limit_up / (limit_up + limit_down) if (limit_up + limit_down) > 0 else 0.5
            health = round(ad_ratio * 50 + lu_ld_ratio * 30 + min(total_amount / 1e12, 1) * 20, 1)
            result["healthScore"] = health
            if health >= 70:
                result["healthLevel"] = "强势"
            elif health >= 55:
                result["healthLevel"] = "偏多"
            elif health >= 45:
                result["healthLevel"] = "中性"
            elif health >= 30:
                result["healthLevel"] = "偏空"
            else:
                result["healthLevel"] = "弱势"

    except Exception as e:
        errors.append(f"市场概览获取失败: {e}")
        traceback.print_exc(file=sys.stderr)

    if errors:
        result["errors"] = errors
    return result


def _market_breadth(errors: list[str]) -> dict:
    """Compute market breadth indicators."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    result: dict[str, Any] = {"action": "breadth"}

    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            changes = df["涨跌幅"].dropna().astype(float)
            total = len(changes)
            advance = int((changes > 0).sum())
            decline = int((changes < 0).sum())

            # Advance-Decline Line equivalent
            ad_diff = advance - decline
            result["advanceDeclineDiff"] = ad_diff
            result["advanceDeclineRatio"] = round(advance / decline, 2) if decline > 0 else advance

            # Distribution of changes
            above_5 = int((changes > 5).sum())
            above_3 = int((changes > 3).sum())
            above_0 = advance
            below_0 = decline
            below_3 = int((changes < -3).sum())
            below_5 = int((changes < -5).sum())

            result["distribution"] = {
                "above5pct": above_5,
                "above3pct": above_3,
                "above0pct": above_0,
                "below0pct": below_0,
                "below3pct": below_3,
                "below5pct": below_5,
            }

            # New high / new low (52-week proxied by > 250-day price if available)
            # Use simple percentage change as proxy
            high_pct = round(above_5 / total * 100, 1) if total > 0 else 0
            low_pct = round(below_5 / total * 100, 1) if total > 0 else 0
            result["strongBullPct"] = high_pct
            result["strongBearPct"] = low_pct

            # Breadth score (0-100)
            breadth = round(advance / total * 100, 1) if total > 0 else 50
            result["breadthScore"] = breadth
            if breadth >= 70:
                result["breadthLevel"] = "极度乐观"
            elif breadth >= 55:
                result["breadthLevel"] = "偏多"
            elif breadth >= 45:
                result["breadthLevel"] = "中性"
            elif breadth >= 30:
                result["breadthLevel"] = "偏空"
            else:
                result["breadthLevel"] = "极度悲观"

    except Exception as e:
        errors.append(f"市场宽度获取失败: {e}")

    if errors:
        result["errors"] = errors
    return result


def _index_quotes(errors: list[str]) -> dict:
    """Get major index quotes."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    indexes = []
    try:
        df = ak.stock_zh_index_spot_em()
        if df is not None and not df.empty:
            # Major indices
            target_codes = {
                "000001": "上证指数",
                "399001": "深证成指",
                "399006": "创业板指",
                "000688": "科创50",
                "000300": "沪深300",
                "000905": "中证500",
                "000852": "中证1000",
                "899050": "北证50",
            }
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                if code in target_codes:
                    indexes.append({
                        "code": code,
                        "name": target_codes.get(code, str(row.get("名称", ""))),
                        "price": _safe_float(row.get("最新价")),
                        "changePct": _safe_float(row.get("涨跌幅")),
                        "changeAmount": _safe_float(row.get("涨跌额")),
                        "volume": _safe_float(row.get("成交量")),
                        "amount": _safe_float(row.get("成交额")),
                    })
    except Exception as e:
        errors.append(f"指数行情获取失败: {e}")

    # Sort by predefined order
    order = ["000001", "399001", "399006", "000688", "000300", "000905", "000852", "899050"]
    indexes.sort(key=lambda x: order.index(x["code"]) if x["code"] in order else 99)

    return {
        "action": "index",
        "count": len(indexes),
        "indexes": indexes,
        "errors": errors if errors else None,
    }


def _limit_threshold(code: str) -> float:
    """Return limit-up/down percentage threshold by stock code."""
    if code.startswith("688"):
        return 20.0  # 科创板
    if code.startswith("30"):
        return 20.0  # 创业板
    if code.startswith("4") or (code.startswith("8") and not code.startswith("688")):
        return 30.0  # 北交所
    return 10.0  # 主板


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:
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

        result = run_market_overview(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
