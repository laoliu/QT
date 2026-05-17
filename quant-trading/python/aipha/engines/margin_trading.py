"""
margin_trading — Market breadth and margin trading (两融) data.

Called from TypeScript sentiment-dashboard via python-bridge.
Input:
  { "action": "breadth" | "summary" }

Output:
  breadth → { advancers, decliners, unchanged, advanceRatio, limitUp, limitDown, ... }
  summary → { date, balance, buyAmount, repayAmount, ... }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any

# Bypass proxy for all requests — required in proxy-enabled environments
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)


def run_margin_trading(config: dict) -> dict:
    """Dispatch margin trading / breadth analysis based on action."""
    action = config.get("action", "breadth")

    if action == "breadth":
        return _market_breadth()
    elif action == "summary":
        return _margin_summary()
    else:
        return {"error": f"未知 action: {action}"}


def _market_breadth() -> dict:
    """Get market breadth data (涨跌家数) via akshare stock_market_activity_legu."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装，请执行: pip install akshare"}

    result: dict[str, Any] = {"action": "breadth"}

    try:
        df = ak.stock_market_activity_legu()
        if df is None or df.empty:
            result["error"] = "无法获取市场活跃度数据"
            return result

        # Parse the item/value pairs
        data = {}
        for _, row in df.iterrows():
            data[str(row["item"]).strip()] = row["value"]

        advancers = int(data.get("上涨", 0))
        decliners = int(data.get("下跌", 0))
        unchanged = int(data.get("平盘", 0))
        limit_up = int(data.get("涨停", 0))
        real_limit_up = int(data.get("真实涨停", 0))
        limit_down = int(data.get("跌停", 0))
        real_limit_down = int(data.get("真实跌停", 0))
        suspended = int(data.get("停牌", 0))
        total = advancers + decliners + unchanged

        result["date"] = str(data.get("统计日期", datetime.now().strftime("%Y-%m-%d")))
        result["total"] = total
        result["advancers"] = advancers
        result["decliners"] = decliners
        result["unchanged"] = unchanged
        result["advanceRatio"] = round(advancers / total, 4) if total > 0 else 0
        result["limitUp"] = limit_up
        result["realLimitUp"] = real_limit_up
        result["limitDown"] = limit_down
        result["realLimitDown"] = real_limit_down
        result["suspended"] = suspended
        result["activity"] = str(data.get("活跃度", ""))

    except Exception as e:
        result["error"] = f"市场宽度查询失败: {e}"

    return result


def _margin_summary() -> dict:
    """Get margin trading (两融) summary via akshare stock_margin_account_info.

    This API provides market-wide daily data (融资余额, 融券余额, 融资买入额,
    担保物总价值, 平均维持担保比例 etc.) with full history.
    """
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装，请执行: pip install akshare"}

    result: dict[str, Any] = {"action": "summary"}

    try:
        df = ak.stock_margin_account_info()
        if df is None or df.empty:
            result["error"] = "暂无两融数据"
            return result

        latest = df.iloc[-1]
        result["date"] = str(latest["日期"])[:10]
        result["balance"] = _safe_float(latest.get("融资余额"))          # 亿
        result["shortBalance"] = _safe_float(latest.get("融券余额"))     # 亿
        result["buyAmount"] = _safe_float(latest.get("融资买入额"))      # 亿
        result["shortSellAmount"] = _safe_float(latest.get("融券卖出额"))# 亿
        result["collateralValue"] = _safe_float(latest.get("担保物总价值"))
        result["avgGuaranteeRatio"] = _safe_float(latest.get("平均维持担保比例"))
        result["investors"] = _safe_float(latest.get("有融资融券负债的投资者数量"))
        result["tradingInvestors"] = _safe_float(latest.get("参与交易的投资者数量"))
        result["brokerCount"] = int(latest.get("证券公司数量", 0))

        # Recent 10-day history for trend
        history = []
        for _, row in df.tail(10).iterrows():
            history.append({
                "date": str(row["日期"])[:10],
                "balance": _safe_float(row.get("融资余额")),
                "shortBalance": _safe_float(row.get("融券余额")),
                "buyAmount": _safe_float(row.get("融资买入额")),
                "avgGuaranteeRatio": _safe_float(row.get("平均维持担保比例")),
            })
        result["history"] = history

    except Exception as e:
        result["error"] = f"两融数据查询失败: {e}"

    return result


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

        result = run_margin_trading(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
