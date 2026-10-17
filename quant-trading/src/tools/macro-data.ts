/**
 * macro_data tool — AI-callable for macro/economic indicator analysis.
 *
 * Actions:
 *   indicators — List available macro indicators
 *   history    — Time series for a specific indicator (GDP, CPI, PMI, etc.)
 *   calendar   — Upcoming economic event releases
 *   regime     — Current macro regime classification (Merrill clock)
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createMacroDataTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "macro_data",
    description:
      "宏观经济数据工具。获取GDP、CPI、PPI、PMI、利率(SHIBOR/LPR)、货币供应(M2)等宏观指标，" +
      "以及经济日历和宏观周期判断。\n" +
      "• action=indicators — 列出可用宏观指标\n" +
      "• action=history — 指定指标的时间序列数据（需指定indicator参数）\n" +
      "• action=calendar — 即将发布的经济数据/事件\n" +
      "• action=regime — 当前宏观周期分析（美林时钟简化版）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["indicators", "history", "calendar", "regime"],
          description:
            "操作类型: indicators=指标列表, history=历史数据, calendar=经济日历, regime=宏观周期",
        },
        indicator: {
          type: "string",
          enum: [
            "gdp",
            "cpi",
            "ppi",
            "pmi",
            "shibor",
            "lpr",
            "m2",
            "social_finance",
            "fed_rate",
            "us_cpi",
            "us_pmi",
            "us_unemployment",
          ],
          description:
            "宏观指标ID（action=history时必填）: gdp/cpi/ppi/pmi/shibor/lpr/m2/social_finance (中国), " +
            "fed_rate/us_cpi/us_pmi/us_unemployment (美国)",
        },
        market: {
          type: "string",
          enum: ["cn", "us"],
          description: "市场: cn=中国, us=美国。默认cn",
        },
        limit: {
          type: "number",
          description: "返回数据点数量，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "indicators";
      const market = (args.market as string) ?? "cn";
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/macro_economic.py",
        {
          action,
          indicator: args.indicator,
          market,
          limit,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "宏观数据获取失败" };
      }

      return pyResult.data;
    },
  };
}
