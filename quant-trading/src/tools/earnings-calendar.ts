/**
 * earnings_calendar tool — AI-callable for earnings dates & analyst consensus.
 *
 * Actions:
 *   upcoming         — Earnings announcements in the next N days
 *   stock_forecast   — Analyst consensus estimates for a stock
 *   surprise_history — Historical beat/miss track record
 *   analyst_ratings  — Analyst ratings & price targets
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createEarningsCalendarTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "earnings_calendar",
    description:
      "财报日历与分析师预测工具。查看即将发布的财报、盈利预测一致性预期、历史超预期记录、分析师评级。\n" +
      "• action=upcoming — 近期（默认30天内）即将披露财报的公司\n" +
      "• action=stock_forecast — 分析师盈利预测一致性预期（需指定symbol）\n" +
      "• action=surprise_history — 历史业绩超预期/不及预期记录（需指定symbol）\n" +
      "• action=analyst_ratings — 分析师评级分布与目标价（需指定symbol）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["upcoming", "stock_forecast", "surprise_history", "analyst_ratings"],
          description:
            "操作类型: upcoming=近期财报, stock_forecast=盈利预测, " +
            "surprise_history=超预期记录, analyst_ratings=分析师评级",
        },
        symbol: {
          type: "string",
          description: "股票代码（forecast/surprise/ratings时必填），如 600519, AAPL",
        },
        market: {
          type: "string",
          enum: ["cn", "us"],
          description: "市场: cn=A股, us=美股。默认cn",
        },
        days: {
          type: "number",
          description: "查询未来多少天的财报日程（仅action=upcoming），默认30",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "upcoming";
      const market = (args.market as string) ?? "cn";
      const days = (args.days as number) ?? 30;
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/earnings_calendar.py",
        {
          action,
          symbol: args.symbol,
          market,
          days,
          limit,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "财报数据获取失败" };
      }

      return pyResult.data;
    },
  };
}
