/**
 * market_overview tool — AI-callable for market breadth and overview (大盘概览).
 *
 * Actions:
 *   overview — Market advance/decline, limit-up/down, volume stats
 *   breadth  — Market breadth indicators (涨跌比, 分布, 宽度评分)
 *   index    — Major index quotes (上证, 深证, 创业板, 科创50, etc.)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarketOverviewTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "market_overview",
    description:
      "大盘概览工具。查看市场涨跌统计、涨停跌停数、市场宽度、主要指数行情。\n" +
      "• action=overview — 市场涨跌家数、涨停跌停统计、成交额、健康评分\n" +
      "• action=breadth — 市场宽度指标（涨跌分布、强弱比例、宽度评分）\n" +
      "• action=index — 主要指数行情（上证、深证、创业板、沪深300等）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["overview", "breadth", "index"],
          description: "操作类型: overview=大盘概览, breadth=市场宽度, index=指数行情",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "overview";

      const pyResult = await runPythonScript<{
        action?: string;
        advance?: number;
        decline?: number;
        limitUp?: number;
        limitDown?: number;
        healthScore?: number;
        healthLevel?: string;
        breadthScore?: number;
        indexes?: Array<Record<string, unknown>>;
        error?: string;
        errors?: string[];
      }>("engines/market_overview.py", { action }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }
        return data;
      }

      return {
        action,
        note: "大盘概览引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
