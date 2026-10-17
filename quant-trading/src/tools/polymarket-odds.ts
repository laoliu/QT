/**
 * polymarket_odds tool — prediction market odds lookup.
 *
 * Fetches odds/probabilities from prediction markets for:
 *   - Economic events (rate cuts, GDP, inflation)
 *   - Geopolitical events
 *   - Market predictions (S&P500 targets, crypto milestones)
 *   - General prediction market trending topics
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createPolymarketOddsTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "polymarket_odds",
    description:
      "预测市场赔率查询工具。\n" +
      "• action=search — 搜索预测市场事件（关键词搜索）\n" +
      "• action=trending — 获取热门预测事件及赔率\n" +
      "• action=category — 按分类浏览预测市场（经济、政治、加密、体育）\n" +
      "• action=detail — 查看特定事件的详细赔率和历史变化",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["search", "trending", "category", "detail"],
          description: "操作类型",
        },
        keyword: {
          type: "string",
          description: "搜索关键词（action=search时必填）",
        },
        category: {
          type: "string",
          enum: ["economics", "politics", "crypto", "sports", "tech", "all"],
          description: "分类筛选，默认all",
        },
        eventId: {
          type: "string",
          description: "事件ID（action=detail时必填）",
        },
        limit: {
          type: "number",
          description: "返回数量，默认10",
        },
        sortBy: {
          type: "string",
          enum: ["volume", "liquidity", "newest", "ending_soon"],
          description: "排序方式，默认volume（交易量）",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const limit = (args.limit as number) ?? 10;
      const category = (args.category as string) ?? "all";
      const sortBy = (args.sortBy as string) ?? "volume";

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/polymarket_engine.py",
        {
          action,
          keyword: args.keyword,
          category,
          eventId: args.eventId,
          limit,
          sortBy,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "预测市场数据获取失败" };
      }

      return pyResult.data;
    },
  };
}
