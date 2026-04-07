/**
 * etf_analysis tool — AI-callable for A-share ETF analysis.
 *
 * Actions:
 *   list    — List popular ETFs with key metrics
 *   compare — Compare multiple ETFs side-by-side
 *   premium — ETF premium/discount rate
 *   flow    — ETF fund flow data
 *   detail  — Detailed ETF analysis
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createEtfAnalysisTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "etf_analysis",
    description:
      "ETF分析工具。查看ETF列表、多ETF对比、折溢价率、资金流向。\n" +
      "• action=list — ETF列表（按成交额/涨跌排序）\n" +
      "• action=compare — 多ETF区间收益对比（需指定symbols）\n" +
      "• action=premium — ETF折溢价率查询（需指定symbol）\n" +
      "• action=flow — ETF份额变化/资金流向（需指定symbol）\n" +
      "• action=detail — ETF详细信息（需指定symbol）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["list", "compare", "premium", "flow", "detail"],
          description:
            "操作类型: list=列表, compare=对比, premium=折溢价, flow=资金流向, detail=详情",
        },
        symbol: {
          type: "string",
          description: "ETF代码（部分action需要），如 510050",
        },
        symbols: {
          type: "string",
          description: '多ETF代码（action=compare时使用，逗号分隔），如 "510050,510300,159919"',
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认30",
        },
        days: {
          type: "number",
          description: "区间天数（action=compare/flow），默认20",
        },
        category: {
          type: "string",
          description: "ETF类别筛选（action=list），默认all",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "list";
      const symbol = args.symbol as string | undefined;
      const symbolsRaw = args.symbols as string | undefined;
      const limit = (args.limit as number) ?? 30;
      const days = (args.days as number) ?? 20;
      const category = (args.category as string) ?? "all";

      if ((action === "premium" || action === "flow" || action === "detail") && !symbol) {
        return { error: "请提供 symbol 参数（ETF代码）" };
      }
      if (action === "compare" && !symbolsRaw) {
        return { error: "请提供 symbols 参数（逗号分隔的ETF代码）" };
      }

      const symbols = symbolsRaw
        ? symbolsRaw
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean)
        : [];

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/etf_analysis.py",
        { action, symbol, symbols, limit, days, category },
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
        error: pyResult.error ?? "ETF数据获取失败",
      };
    },
  };
}
