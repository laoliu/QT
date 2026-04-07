/**
 * market_breadth tool — AI-callable for A-share market breadth indicators.
 *
 * Actions:
 *   overview    — Market breadth overview (advance/decline, limit-up/down, profit effect)
 *   ma_breadth  — Percentage of stocks above MA20/MA60 (market width)
 *   history     — Historical breadth trend over N days
 *   sentiment   — Composite market sentiment score
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarketBreadthTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "market_breadth",
    description:
      "市场广度/宽度指标工具。查看涨跌家数比、涨停跌停数、赚钱效应、市场情绪。\n" +
      "• action=overview — 市场广度概览（涨跌家数、涨停跌停、赚钱效应）\n" +
      "• action=ma_breadth — 市场宽度（站上均线比例）\n" +
      "• action=history — 历史广度趋势\n" +
      "• action=sentiment — 综合市场情绪评分（0-100）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["overview", "ma_breadth", "history", "sentiment"],
          description:
            "操作类型: overview=广度概览, ma_breadth=均线宽度, history=历史趋势, sentiment=情绪评分",
        },
        period: {
          type: "number",
          description: "均线周期（仅action=ma_breadth），默认20",
        },
        days: {
          type: "number",
          description: "历史天数（仅action=history），默认20",
        },
        date: {
          type: "string",
          description: "日期（可选），格式 YYYY-MM-DD",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "overview";
      const period = (args.period as number) ?? 20;
      const days = (args.days as number) ?? 20;
      const date = args.date as string | undefined;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/market_breadth.py",
        { action, period, days, date },
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
        error: pyResult.error ?? "市场广度数据获取失败",
      };
    },
  };
}
