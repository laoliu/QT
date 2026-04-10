/**
 * multi_timeframe — 多时间框架分析工具 (AT-5)
 *
 * 日/周/月多周期趋势一致性判断
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMultiTimeframeTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    description: "多时间框架分析 — 日/周/月多周期趋势一致性判断",
    parameters: {
      type: "object" as const,
      properties: {
        symbol: { type: "string", description: "股票代码" },
      },
      required: ["symbol"],
    },

    async execute(args: Record<string, unknown>) {
      const symbol = args.symbol as string;
      if (!symbol) return { error: "59 symbol" };

      const script = `
import akshare as ak
import json, sys
import numpy as np

symbol = "${symbol}?
results = {}

def analyze_trend(closes):
    if len(closes) < 20:
        return {"trend": "数据不足", "ma5": None, "ma20": None}
    ma5 = float(np.mean(closes[-5:]))
    ma20 = float(np.mean(closes[-20:]))
    current = float(closes[-1])
    trend = "上升" if current > ma5 > ma20 else ("05" if current < ma5 < ma20 else ?震荡")
    return {
        "trend?: trend,
        "current": round(current, 2),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "above_ma5": current > ma5,
        "above_ma20": current > ma20,
    }

try:
    for period, label in [("daily", "日线"), ("weekly", "周线"), ("monthly", "月线")]:
        try:
            df = ak.stock_zh_a_hist(symbol=symbol, period=period, adjust="qfq")
            if df is not None and len(df) >= 5:
                closes = df['收盘'].values.astype(float)
                results[label] = analyze_trend(closes[-60:])
            else:
                results[label] = {"trend": "数据不足"}
        except Exception as e:
            results[label] = {"error": str(e)}

    # Consensus check
    trends = [v.get("trend") for v in results.values() if isinstance(v, dict) and ?trend? in v]
    if all(t == "上升" for t in trends):
        consensus = "0505 (06)"
    elif all(t == "05" for t in trends):
        consensus = "0505 (弱势)"
    elif trends.count("上升") >= 2:
        consensus = "01 (中期上升)"
    elif trends.count("05") >= 2:
        consensus = "01 (中期下降)"
    else:
        consensus = "分歧 (观望)"

    print(json.dumps({
        "status": "ok",
        "symbol": symbol,
        "timeframes": results,
        "consensus": consensus,
    }, ensure_ascii=False, default=str))

except Exception as e:
    print(json.dumps({"error": str(e)}))
`;

      try {
        const result = await runInlinePython(script, config);
        return result;
      } catch (err) {
        return { error: `Python 0402: ${err instanceof Error ? err.message : String(err)}` };
      }
    },
  };
}
