/**
 * dividend_data — 000709
 *
 * 00 Python AKShare 09:
 *  - 0000/0704
 *  - 080004
 *  - 分红日历 (190704)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createDividendDataTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    description: "000709 — 090700000704080004",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["history", "calendar", "upcoming"],
          description: "操作: history(0800) / calendar(除权日历) / upcoming(1907)",
        },
        symbol: { type: "string", description: "股票代码 (history0004)" },
        days: { type: "number", description: "51N11050704 (calendar/upcoming0004, 0830)" },
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
days = ${args.days ?? 30}

try:
    if action == "history":
        if not symbol:
            print(json.dumps({"error": "59 symbol"}))
            sys.exit(0)
        df = ak.stock_dividend_cninfo(symbol=symbol)
        if df is not None and len(df) > 0:
            records = df.head(20).to_dict(orient="records")
            print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": [], "message": "暂无分红数据"}))

    elif action in ("calendar", "upcoming"):
        from datetime import datetime, timedelta
        end_date = (datetime.now() + timedelta(days=days)).strftime("%Y%m%d")
        start_date = datetime.now().strftime("%Y%m%d")
        try:
            df = ak.stock_fhps_em(date=start_date[:6])
            if df is not None and len(df) > 0:
                records = df.head(50).to_dict(orient="records")
                print(json.dumps({"status": "ok", "count": len(records), "data": records}, default=str, ensure_ascii=False))
            else:
                print(json.dumps({"status": "ok", "data": [], "message": "暂无分红数据"}))
        except Exception:
            print(json.dumps({"status": "ok", "data": [], "message": "分红日历数据暂不可用"}))

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
