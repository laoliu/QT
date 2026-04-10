/**
 * lockup_calendar — 限售解禁日历工具
 *
 * 00 Python AKShare 0919001100
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createLockupCalendarTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    description: "限售解禁日历 — 091900110005",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["upcoming", "by_stock", "summary"],
          description: "操作: upcoming(近期解禁日历) / by_stock(个股解禁) / summary(解禁统计)",
        },
        symbol: { type: "string", description: "股票代码 (by_stock0004)" },
        start_date: { type: "string", description: "开始日期 YYYYMMDD" },
        end_date: { type: "string", description: "结束日期 YYYYMMDD" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const today = new Date();
      const future = new Date(today.getTime() + 30 * 86400000);
      const fmt = (d: Date) => d.toISOString().slice(0, 10).replace(/-/g, "");
      const startDate = (args.start_date as string) || fmt(today);
      const endDate = (args.end_date as string) || fmt(future);

      const script = `
import akshare as ak
import json, sys
from datetime import datetime, timedelta

action = "${action}"
symbol = "${args.symbol ?? ""}"
start_date = "${startDate}"
end_date = "${endDate}"

try:
    if action == "upcoming":
        df = ak.stock_restricted_release_queue_sina(symbol="0007")
        if df is not None and len(df) > 0:
            records = df.head(30).to_dict(orient="records")
            print(json.dumps({"status": "ok", "count": len(records), "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": [], "message": "暂无数据"}))

    elif action == "by_stock":
        if not symbol:
            print(json.dumps({"error": "59 symbol"}))
            sys.exit(0)
        df = ak.stock_restricted_release_detail_sina(symbol=symbol)
        if df is not None and len(df) > 0:
            records = df.to_dict(orient="records")
            print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": [], "message": "071504"}))

    elif action == "summary":
        df = ak.stock_restricted_release_summary_sina()
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
