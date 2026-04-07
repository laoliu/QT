/**
 * margin_data tool — AI-callable for A-share margin trading data (融资融券/两融).
 *
 * Actions:
 *   market_summary — Market-wide margin balance summary
 *   stock_detail   — Margin data for a specific stock
 *   top_stocks     — Top stocks by margin balance
 *   trend          — Historical margin balance trend
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarginDataTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "margin_data",
    description:
      "融资融券（两融）数据工具。查看全市场两融余额、个股融资融券明细、两融排名、趋势。\n" +
      "• action=market_summary — 全市场融资融券余额概览\n" +
      "• action=stock_detail — 个股融资融券明细（需指定symbol）\n" +
      "• action=top_stocks — 两融余额排名（融资买入/融券卖出前N）\n" +
      "• action=trend — 融资余额历史趋势",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["market_summary", "stock_detail", "top_stocks", "trend"],
          description:
            "操作类型: market_summary=市场概览, stock_detail=个股明细, top_stocks=排名, trend=趋势",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=stock_detail时必填），如 600519",
        },
        limit: {
          type: "number",
          description: "返回数量限制（action=top_stocks），默认20",
        },
        days: {
          type: "number",
          description: "趋势天数（action=trend），默认30",
        },
        sort_by: {
          type: "string",
          enum: ["balance", "change"],
          description: "排序方式（action=top_stocks），默认按余额",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "market_summary";
      const symbol = args.symbol as string | undefined;
      const limit = (args.limit as number) ?? 20;
      const days = (args.days as number) ?? 30;
      const sortBy = (args.sort_by as string) ?? "balance";

      if (action === "stock_detail" && !symbol) {
        return { error: "请提供 symbol 参数" };
      }

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/margin_data.py",
        { action, symbol, limit, days, sort_by: sortBy },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error };
        }
        return data;
      }

      return {
        error: pyResult.error ?? "两融数据获取失败",
      };
    },
  };
}
