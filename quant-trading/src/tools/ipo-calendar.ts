/**
 * ipo_calendar tool — AI-callable for A-share IPO tracking.
 *
 * Actions:
 *   upcoming — Upcoming IPO subscription calendar (新股申购日历)
 *   recent   — Recently listed new stocks (近期上市新股)
 *   detail   — IPO detail for a specific stock
 *   lottery  — IPO lottery results (中签率)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createIpoCalendarTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "ipo_calendar",
    description:
      "新股/IPO日历工具。查看新股申购日历、近期上市新股、中签率。\n" +
      "• action=upcoming — 新股申购日历（即将申购的新股）\n" +
      "• action=recent — 近期上市新股列表\n" +
      "• action=detail — 查看某只新股的IPO详情（需指定symbol）\n" +
      "• action=lottery — 新股中签率查询",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["upcoming", "recent", "detail", "lottery"],
          description:
            "操作类型: upcoming=申购日历, recent=近期上市, detail=IPO详情, lottery=中签率",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=detail时必填），如 688xxx",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认30",
        },
        days: {
          type: "number",
          description: "近期天数（仅action=recent），默认30",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "upcoming";
      const symbol = args.symbol as string | undefined;
      const limit = (args.limit as number) ?? 30;
      const days = (args.days as number) ?? 30;

      if (action === "detail" && !symbol) {
        return { error: "请提供 symbol 参数" };
      }

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/ipo_calendar.py",
        { action, symbol, limit, days },
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
        error: pyResult.error ?? "新股数据获取失败",
      };
    },
  };
}
