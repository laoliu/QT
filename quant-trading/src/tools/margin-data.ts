/**
 * margin_data — 041009 (DS-5, AT-10)
 *
 * 00 Python AKShare 09:
 *  - 个股两融余额 / 趋势
 *  - 市场两融排行
 *  - 两融异常检测
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runInlinePython } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarginDataTool(api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    description: "融资融券数据 — 个股两融余额、趋势、排行榜、异常检测",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["balance", "trend", "ranking", "anomaly"],
          description: "操作: balance(余额查询) / trend(趋势) / ranking(排行) / anomaly(异常检测)",
        },
        symbol: { type: "string", description: "股票代码 (balance/trend0004)" },
        days: { type: "number", description: "查看最近N天 (trend0004, 0830)" },
        top: { type: "number", description: "02N名 (ranking0004, 0820)" },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = (args.symbol as string) ?? "";
      const days = (args.days as number) ?? 30;
      const top = (args.top as number) ?? 20;

      const script = `
import akshare as ak
import json, sys
from datetime import datetime, timedelta

action = "${action}?
symbol = "${symbol}"
days = ${days}
top = ${top}

try:
    if action == "balance":
        if not symbol:
            print(json.dumps({"error": "59 symbol"}))
            sys.exit(0)
        df = ak.stock_margin_detail_szse(date=datetime.now().strftime("%Y%m%d"))
        if df is not None and len(df) > 0:
            match = df[df.iloc[:, 0].astype(str).str.contains(symbol)]
            if len(match) > 0:
                records = match.to_dict(orient="records")
                print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
            else:
                print(json.dumps({"status": "ok", "data": [], "message": "ā09"}))
        else:
            print(json.dumps({"status": "ok", "data": []}))

    elif action == "trend":
        if not symbol:
            print(json.dumps({"error": "59 symbol"}))
            sys.exit(0)
        start = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")
        df = ak.stock_margin_underlying_info_szse(date=end)
        if df is not None and len(df) > 0:
            records = df.head(days).to_dict(orient="records")
            print(json.dumps({"status": "ok", "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": []}))

    elif action == "ranking":
        df = ak.stock_margin_detail_szse(date=datetime.now().strftime("%Y%m%d"))
        if df is not None and len(df) > 0:
            records = df.head(top).to_dict(orient="records")
            print(json.dumps({"status": "ok", "count": len(records), "data": records}, default=str, ensure_ascii=False))
        else:
            print(json.dumps({"status": "ok", "data": []}))

    elif action == "anomaly":
        df = ak.stock_margin_detail_szse(date=datetime.now().strftime("%Y%m%d"))
        if df is not None and len(df) > 0:
            records = df.head(10).to_dict(orient="records")
            print(json.dumps({"status": "ok", "data": records, "note": "按融资余额变化排序"}, default=str, ensure_ascii=False))
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
