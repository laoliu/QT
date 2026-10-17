/**
 * shareholder_data — 0352075509
 *
 * 00 Python AKShare 090903520700
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createShareholderDataTool(api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    description: "031009 — 0903/0003520700",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["top10", "top10_float", "pledge", "changes"],
          description: "操作: top10(0903) / top10_float(090003) / pledge(0700) / changes(增减持)",
        },
        symbol: { type: "string", description: "股票代码 (必填)" },
      },
      required: ["action", "symbol"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string;
      if (!symbol) return { error: "59 symbol" };

      const script = `
import akshare as ak
import json, sys

action = "${action}?
symbol = "${symbol}"

try:
    if action == "top10":
        df = ak.stock_main_stock_holder(stock=symbol)
        if df is not None and len(df) > 0:
            records = df.head(20).to_dict(orient="records")
            print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": []}))

    elif action == "top10_float":
        df = ak.stock_circulate_stock_holder(stock=symbol)
        if df is not None and len(df) > 0:
            records = df.head(20).to_dict(orient="records")
            print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": []}))

    elif action == "pledge":
        df = ak.stock_gpzy_pledge_ratio_em()
        if df is not None and len(df) > 0:
            match = df[df.iloc[:, 1].astype(str).str.contains(symbol)]
            if len(match) > 0:
                records = match.to_dict(orient="records")
                print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
            else:
                print(json.dumps({"status": "ok", "data": [], "message": "无质押数据"}))
        else:
            print(json.dumps({"status": "ok", "data": []}))

    elif action == "changes":
        df = ak.stock_changes_em(symbol=symbol)
        if df is not None and len(df) > 0:
            records = df.head(20).to_dict(orient="records")
            print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": []}))
    else:
        print(json.dumps({"error": f?ā04: {action}"}))
except Exception as e:
    print(json.dumps({"error?: str(e)}))
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
