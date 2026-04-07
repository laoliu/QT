/**
 * margin_trading tool — AI-callable for 两融数据 (Margin Trading Data).
 *
 * Actions:
 *   summary   — 两融余额汇总 (latest margin balance summary)
 *   detail    — 个股融资融券明细 (per-stock margin detail)
 *   history   — 个股融资融券历史 (margin history for a stock)
 *   breadth   — 市场宽度 (advance/decline ratio, limit-up/down counts)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarginTradingTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "margin_trading",
    description:
      "两融数据与市场宽度工具。\n" +
      "• action=summary — 沪深两市融资融券余额汇总（融资余额、融券余额、变化）\n" +
      "• action=detail — 个股融资融券明细（融资余额排名、净买入排名）\n" +
      "• action=history — 查看某只股票的两融历史（需指定symbol）\n" +
      "• action=breadth — 市场宽度指标（涨跌比、涨停跌停数、上涨下跌家数）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["summary", "detail", "history", "breadth"],
          description:
            "操作类型: summary=两融余额汇总, detail=个股两融明细, history=个股两融历史, breadth=市场涨跌宽度",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=history时必填），如 600519",
        },
        date: {
          type: "string",
          description: "日期（action=breadth时可选），格式 YYYYMMDD",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "summary";
      const symbol = args.symbol as string | undefined;
      const date = args.date as string | undefined;
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/margin_trading.py",
        { action, symbol, date, limit },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }
        return data;
      }

      return {
        action,
        note: "两融数据引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
