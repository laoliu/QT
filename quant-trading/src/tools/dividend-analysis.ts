/**
 * dividend_analysis tool — AI-callable for A-share dividend data and analysis.
 *
 * Actions:
 *   history    — Historical dividend payments for a stock
 *   calendar   — Upcoming ex-dividend dates
 *   high_yield — Screen for high dividend yield stocks
 *   detail     — Detailed dividend analysis (yield trend, payout consistency)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createDividendAnalysisTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "dividend_analysis",
    description:
      "分红分析工具。查看个股分红历史、分红日历、高股息筛选、分红详细分析。\n" +
      "• action=history — 个股历史分红记录（需指定symbol）\n" +
      "• action=calendar — 近期除权除息日历\n" +
      "• action=high_yield — 高股息股票筛选（可设最低股息率）\n" +
      "• action=detail — 个股分红详细分析（分红趋势、持续性）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["history", "calendar", "high_yield", "detail"],
          description:
            "操作类型: history=分红历史, calendar=分红日历, high_yield=高股息筛选, detail=详细分析",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=history/detail时必填），如 600519",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
        min_yield: {
          type: "number",
          description: "最低股息率（仅action=high_yield），默认3.0（%）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "history";
      const symbol = args.symbol as string | undefined;
      const limit = (args.limit as number) ?? 20;
      const minYield = (args.min_yield as number) ?? 3.0;

      if ((action === "history" || action === "detail") && !symbol) {
        return { error: "请提供 symbol 参数" };
      }

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/dividend_analysis.py",
        { action, symbol, limit, min_yield: minYield },
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
        error: pyResult.error ?? "分红数据获取失败",
      };
    },
  };
}
