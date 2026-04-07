/**
 * dividend_tracker tool — AI-callable for dividend/ex-dividend date tracking.
 *
 * Actions:
 *   upcoming   — Upcoming ex-dividend dates within N days
 *   history    — Dividend history for a specific stock
 *   calendar   — Dividend calendar grouped by date
 *   high_yield — Screen for high dividend yield stocks
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createDividendTrackerTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "dividend_tracker",
    description:
      "分红追踪工具。查看即将除权除息的股票、个股分红历史、高股息筛选。\n" +
      "• action=upcoming — 即将除权除息的股票（可指定days天数范围，默认30天）\n" +
      "• action=history — 个股分红历史记录（需指定symbol）\n" +
      "• action=calendar — 分红日历（按日期分组）\n" +
      "• action=high_yield — 高股息股票筛选（可指定min_yield最低股息率，默认3%）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["upcoming", "history", "calendar", "high_yield"],
          description:
            "操作类型: upcoming=即将除息, history=分红历史, calendar=分红日历, high_yield=高股息筛选",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=history时必填），如 600519",
        },
        days: {
          type: "number",
          description: "查询天数范围（upcoming/calendar使用），默认30",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
        min_yield: {
          type: "number",
          description: "最低股息率百分比（action=high_yield时使用），默认3.0",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "upcoming";
      const symbol = args.symbol as string | undefined;
      const days = (args.days as number) ?? 30;
      const limit = (args.limit as number) ?? 20;
      const min_yield = (args.min_yield as number) ?? 3.0;

      const pyResult = await runPythonScript<{
        action?: string;
        dividends?: Array<Record<string, unknown>>;
        calendar?: Record<string, unknown>;
        stocks?: Array<Record<string, unknown>>;
        count?: number;
        error?: string;
        errors?: string[];
      }>(
        "engines/dividend_tracker.py",
        { action, symbol, days, limit, min_yield },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }

        const count = data.count ?? 0;
        if (count > 10) {
          (data as Record<string, unknown>).displayHint =
            `共${String(count)}条分红记录，请分组展示，标注除息日距今天数。`;
        }

        return data;
      }

      return {
        action,
        note: "分红追踪引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
