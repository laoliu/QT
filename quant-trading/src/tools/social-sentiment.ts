/**
 * social_sentiment tool — AI-callable for social/community sentiment analysis.
 *
 * Actions:
 *   hot_stocks        — Trending stocks by community popularity
 *   stock_sentiment   — Sentiment analysis for a specific stock
 *   sentiment_history — Historical sentiment trend
 *   retail_flow       — Retail investor activity / sudden attention spikes
 */
import type { QuantTradingRuntime } from "../runtime.js";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createSocialSentimentTool(_api: QuantTradingRuntime, config: QuantTradingConfig) {
  return {
    name: "social_sentiment",
    description:
      "社会舆情与市场情绪分析工具。基于东方财富人气榜、百度热搜等数据源，分析个股和市场的社区讨论热度与情绪。\n" +
      "• action=hot_stocks — 今日社区讨论热度排名（全市场热门股）\n" +
      "• action=stock_sentiment — 单只股票的综合情绪评分（需指定symbol）\n" +
      "• action=sentiment_history — 个股情绪热度历史走势（需指定symbol）\n" +
      "• action=retail_flow — 散户关注度突变排名（热度突增的股票）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["hot_stocks", "stock_sentiment", "sentiment_history", "retail_flow"],
          description:
            "操作类型: hot_stocks=热门股排名, stock_sentiment=个股情绪, " +
            "sentiment_history=情绪趋势, retail_flow=散户关注突变",
        },
        symbol: {
          type: "string",
          description: "股票代码（sentiment/history时必填），如 600519",
        },
        market: {
          type: "string",
          enum: ["cn"],
          description: "市场，目前仅支持cn（A股）",
        },
        limit: {
          type: "number",
          description: "返回数量限制，默认20",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "hot_stocks";
      const market = (args.market as string) ?? "cn";
      const limit = (args.limit as number) ?? 20;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/social_sentiment.py",
        {
          action,
          symbol: args.symbol,
          market,
          limit,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "舆情数据获取失败" };
      }

      return pyResult.data;
    },
  };
}
