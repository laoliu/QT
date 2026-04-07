/**
 * market_breadth tool — AI-callable for market breadth indicators (市场宽度).
 *
 * Provides classic breadth indicators:
 *   overview         — advance/decline, limit up/down, volume ratio, sentiment
 *   above_ma         — percentage of stocks above MA(N)
 *   advance_decline  — A/D line history
 *   new_high_low     — 52-week new highs vs lows
 *   mcclellan        — McClellan Oscillator approximation
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMarketBreadthTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
) {
  return {
    name: "market_breadth",
    description:
      "市场宽度指标。分析全市场涨跌家数、新高新低、均线之上占比等广度指标，判断市场整体强弱。\n" +
      "action=overview 全市场涨跌概览（涨跌家数/涨停跌停/上下量比/情绪判断），\n" +
      "action=above_ma 站上均线的股票占比（period=20/50/200），\n" +
      "action=advance_decline 涨跌家数历史（A/D线），\n" +
      "action=new_high_low 创52周新高新低的股票，\n" +
      "action=mcclellan McClellan摆动指标（市场动能）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["overview", "above_ma", "advance_decline", "new_high_low", "mcclellan"],
          description:
            "操作: overview=涨跌概览, above_ma=均线之上占比, advance_decline=A/D线, " +
            "new_high_low=新高新低, mcclellan=McClellan指标",
        },
        period: {
          type: "number",
          description: "均线周期 (action=above_ma, 默认20, 可选: 5/10/20/50/60/200)",
        },
        days: {
          type: "number",
          description: "历史天数 (action=advance_decline/mcclellan, 默认30)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/market_breadth.py",
        {
          action,
          period: args.period ?? 20,
          days: args.days ?? 30,
        },
        config,
      );

      if (pyResult.ok && pyResult.data) {
        return pyResult.data;
      }

      return {
        action,
        note: "市场宽度引擎未就绪。" + (pyResult.error ? ` 错误: ${pyResult.error}` : ""),
      };
    },
  };
}
