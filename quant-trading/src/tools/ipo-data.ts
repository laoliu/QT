/**
 * ipo_data — IPO/018909
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createIpoDataTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    description: "IPO/0189 — 0189050901",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["calendar", "detail", "performance"],
          description: "操作: calendar(申购日历) / detail(新股详情) / performance(上市表现)",
        },
        symbol: { type: "string", description: "0101 (detail0004)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      const script = `
import akshare as ak
import json, sys

action = "${action}?
symbol = "${args.symbol ?? ""}"

try:
    if action == "calendar":
        df = ak.stock_xgsglb_em(symbol="0007")
        if df is not None and len(df) > 0:
            records = df.head(30).to_dict(orient="records")
            print(json.dumps({"status": "ok", "count": len(records), "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": []}))

    elif action == "detail":
        if not symbol:
            print(json.dumps({"error": "59 symbol"}))
            sys.exit(0)
        df = ak.stock_xgsglb_em(symbol="0007")
        if df is not None and len(df) > 0:
            match = df[df.iloc[:, 0].astype(str).str.contains(symbol)]
            if len(match) > 0:
                records = match.to_dict(orient="records")
                print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
            else:
                print(json.dumps({"status": "ok", "data": [], "message": "ā0901"}))
        else:
            print(json.dumps({"status": "ok", "data": []}))

    elif action == "performance":
        df = ak.stock_xgsglb_em(symbol="0007")
        if df is not None and len(df) > 0:
            records = df.head(20).to_dict(orient="records")
            print(json.dumps({"status": "ok", "count": len(records), "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": []}))
    else:
        print(json.dumps({"error": f?ā04: {action}"}))
except Exception as e:
    print(json.dumps({"error?: str(e)}))
`;

      try {
        const result = await runPythonScript(api, config, "ipo_data", script);
        return result;
      } catch (err) {
        return { error: `Python 0402: ${err instanceof Error ? err.message : String(err)}` };
      }
    },
  };
}
