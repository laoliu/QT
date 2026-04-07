/**
 * market_breadth tool — AI-callable for A-share market breadth analysis.
 *
 * Actions:
 *   overview        — Comprehensive breadth snapshot (涨跌家数, 涨停跌停, 成交额, 情绪评分)
 *   advance_decline — Historical advance/decline data
 *   new_highs_lows  — New 60-day highs vs lows count
 *   breadth_trend   — Major index breadth trend over N days
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarketBreadthTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "market_breadth",
    description:
      "市场宽度分析工具。追踪大盘整体健康度：涨跌家数、涨停跌停统计、新高新低、情绪评分。\n" +
      "• action=overview — 今日市场宽度全景（涨跌比、涨停/跌停数、成交分布、情绪评分0-100）\n" +
      "• action=advance_decline — 近N日涨跌数据（可指定days）\n" +
      "• action=new_highs_lows — 60日新高 vs 新低统计\n" +
      "• action=breadth_trend — 三大指数宽度趋势",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["overview", "advance_decline", "new_highs_lows", "breadth_trend"],
          description:
            "操作类型: overview=今日全景, advance_decline=涨跌历史, new_highs_lows=新高新低, breadth_trend=宽度趋势",
        },
        days: {
          type: "number",
          description: "查询天数（advance_decline/breadth_trend使用），默认5/20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "overview";
      const days = args.days as number | undefined;

      const pyResult = await runPythonScript<{
        action?: string;
        breadth?: Record<string, unknown>;
        sentiment_score?: number;
        sentiment_label?: string;
        market_turnover?: Record<string, unknown>;
        history?: Array<Record<string, unknown>>;
        indices?: Array<Record<string, unknown>>;
        error?: string;
        errors?: string[];
      }>("engines/market_breadth.py", { action, days }, config);

      if (pyResult.ok && pyResult.data) {
        const data = pyResult.data;
        if (data.error) {
          return { error: data.error, errors: data.errors };
        }
        return data;
      }

      return {
        action,
        note: "市场宽度引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
