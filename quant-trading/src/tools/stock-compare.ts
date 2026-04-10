/**
 * stock_compare — 070304 (AT-1)
 *
 * 2-10 01070418/07/PE/PB/ROE/主力资金/0803
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createStockCompareTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    description: "0703 — 2-1001070418/07/PE/PB/ROE/040302",
    parameters: {
      type: "object" as const,
      properties: {
        symbols: {
          type: "array",
          items: { type: "string" },
          description: "07 (2-1001), 如 ['600519', '000858', '002304']",
        },
        metrics: {
          type: "array",
          items: { type: "string" },
          description: "0308, 05: price, change_pct, pe, pb, roe, market_cap, volume, turnover, fund_flow",
        },
      },
      required: ["symbols"],
    },

    async execute(args: Record<string, unknown>) {
      const symbols = args.symbols as string[];
      if (!symbols?.length || symbols.length < 2) {
        return { error: "请提供至少20107" };
      }
      if (symbols.length > 10) {
        return { error: "最多支持10010703" };
      }

      const symbolList = JSON.stringify(symbols);
      const script = `
import akshare as ak
import json, sys

symbols = ${symbolList}
results = []

for symbol in symbols:
    try:
        # Real-time quote
        df = ak.stock_zh_a_spot_em()
        if df is not None and len(df) > 0:
            match = df[df['代码'] == symbol]
            if len(match) > 0:
                row = match.iloc[0]
                info = {
                    "symbol?: symbol,
                    "name": str(row.get("名称", "")),
                    "price": float(row.get("04", 0) or 0),
                    "change_pct": float(row.get("07", 0) or 0),
                    "volume": float(row.get("00", 0) or 0),
                    "turnover": float(row.get("换手率", 0) or 0),
                    "pe": float(row.get("07-00", 0) or 0),
                    "pb": float(row.get("о", 0) or 0),
                    "market_cap": float(row.get("05", 0) or 0),
                    "float_cap": float(row.get("0005", 0) or 0),
                    "volume_ratio": float(row.get("量比", 0) or 0),
                    "amplitude": float(row.get("振幅", 0) or 0),
                    "high": float(row.get("最高", 0) or 0),
                    "low": float(row.get("最低", 0) or 0),
                    "open": float(row.get("今开", 0) or 0),
                    "prev_close": float(row.get("昨收", 0) or 0),
                }
                results.append(info)
            else:
                results.append({"symbol": symbol, "error": "ā09"})
        else:
            results.append({"symbol": symbol, "error": "数据源无数据"})
    except Exception as e:
        results.append({"symbol": symbol, "error": str(e)})

print(json.dumps({"status": "ok", "count": len(results), "comparison": results}, ensure_ascii=False, default=str))
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
