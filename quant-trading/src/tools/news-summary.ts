/**
 * news_summary tool — financial news aggregation & sentiment analysis.
 *
 * Actions:
 *   - market_news: Get latest market/macro news headlines
 *   - stock_news: Get news for a specific stock
 *   - sentiment: Analyze market sentiment from recent news
 *   - events: Upcoming economic calendar events
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { QuantTradingConfig } from "../types.js";

export function createNewsSummaryTool(_api: OpenClawPluginApi, config: QuantTradingConfig) {
  return {
    name: "news_summary",
    description:
      "财经新闻聚合与情绪分析工具（BEC计新闻 + 国际新闻抽要）。\n" +
      "• action=market_news — 最新市场/宏观新闻摘要（A股、美股、港股、加密货币）\n" +
      "• action=stock_news — 某只标的的相关新闻\n" +
      "• action=sentiment — 分析近期新闻的市场情绪（看多/看空/中性）\n" +
      "• action=events — 财经日历（央行会议、GDP、CPI等宏观事件）",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["market_news", "stock_news", "sentiment", "events"],
          description: "操作类型",
        },
        symbol: {
          type: "string",
          description: "标的代码（action=stock_news/sentiment时使用）",
        },
        market: {
          type: "string",
          enum: ["cn", "us", "hk", "crypto", "global"],
          description: "市场范围，默认cn",
        },
        limit: {
          type: "number",
          description: "返回新闻条数，默认10",
        },
        days: {
          type: "number",
          description: "查询最近N天的新闻，默认3",
        },
        keyword: {
          type: "string",
          description: "关键词过滤",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const market = (args.market as string) ?? "cn";
      const limit = (args.limit as number) ?? 10;
      const days = (args.days as number) ?? 3;

      const pyResult = await runPythonScript<Record<string, unknown>>(
        "engines/news_engine.py",
        {
          action,
          symbol: args.symbol,
          market,
          limit,
          days,
          keyword: args.keyword,
        },
        config,
      );

      if (!pyResult.ok) {
        return { error: pyResult.error ?? "新闻获取失败" };
      }

      return pyResult.data;
    },
  };
}
