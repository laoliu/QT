/**
 * portfolio_news tool — AI-callable interface for fetching news and sentiment
 * specifically for the user's held positions (portfolio-scoped).
 *
 * Unlike news_summary (which fetches general news or single-stock news),
 * this tool automatically aggregates news across all positions and highlights
 * material events.
 *
 * Actions:
 *   scan       — scan news for all held positions, flag material events
 *   sentiment  — aggregate sentiment score for the portfolio
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { LivePosition, QuantTradingConfig } from "../types.js";

export function createPortfolioNewsTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getPositions: () => LivePosition[],
) {
  return {
    name: "portfolio_news",
    description:
      "持仓新闻聚合工具 — 自动扫描当前持仓的所有股票新闻并高亮重要事件。\n" +
      "• action=scan — 扫描所有持仓股票的最新新闻，标记重大事件（财报、政策、人事变动等）\n" +
      "• action=sentiment — 汇总持仓的新闻情绪评分",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["scan", "sentiment"],
          description: "操作类型",
        },
        days: {
          type: "number",
          description: "查询最近N天的新闻 (默认3)",
        },
        limit_per_stock: {
          type: "number",
          description: "每只股票最多返回几条新闻 (默认5)",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const days = (args.days as number) ?? 3;
      const limitPerStock = (args.limit_per_stock as number) ?? 5;

      const positions = getPositions().filter((p) => p.quantity > 0);
      if (positions.length === 0) {
        return { message: "当前无持仓" };
      }

      const symbols = positions.map((p) => p.symbol);
      const posMap = new Map(positions.map((p) => [p.symbol, p]));

      switch (action) {
        case "scan": {
          const results: Array<{
            symbol: string;
            name?: string;
            quantity: number;
            marketValue?: number;
            news: Array<Record<string, unknown>>;
            hasMaterialEvent: boolean;
            materialEvents: string[];
          }> = [];

          let totalMaterialEvents = 0;

          // Fetch news for each position (batch by calling Python once per stock)
          for (const symbol of symbols) {
            const pos = posMap.get(symbol);
            try {
              const pyResult = await runPythonScript<{
                news?: Array<{
                  title: string;
                  time?: string;
                  source?: string;
                  url?: string;
                  isMaterial?: boolean;
                  materialType?: string;
                }>;
                materialEvents?: string[];
              }>(
                "engines/news_engine.py",
                {
                  action: "stock_news",
                  symbol,
                  market: pos?.market === "us_stock" ? "us" : "cn",
                  limit: limitPerStock,
                  days,
                },
                config,
              );

              const news = pyResult.ok && pyResult.data?.news ? pyResult.data.news : [];

              // Detect material events from keywords
              const materialKeywords = [
                "财报", "业绩", "盈利", "亏损", "预增", "预减", "快报",
                "重组", "并购", "收购", "增持", "减持", "回购",
                "停牌", "复牌", "退市", "ST",
                "高管", "董事长", "总经理", "辞职", "变更",
                "监管", "处罚", "立案", "违规",
                "分红", "除权", "配股", "增发",
              ];

              const materialEvents: string[] = [];
              const enrichedNews = news.map((n: Record<string, unknown>) => {
                const title = String(n.title ?? "");
                const isMaterial = materialKeywords.some((kw) => title.includes(kw));
                if (isMaterial) {
                  const matched = materialKeywords.filter((kw) => title.includes(kw));
                  materialEvents.push(`${title} [${matched.join(",")}]`);
                }
                return { ...n, isMaterial };
              });

              totalMaterialEvents += materialEvents.length;

              results.push({
                symbol,
                name: pos?.name,
                quantity: pos?.quantity ?? 0,
                marketValue: pos?.marketValue,
                news: enrichedNews,
                hasMaterialEvent: materialEvents.length > 0,
                materialEvents,
              });
            } catch {
              results.push({
                symbol,
                name: pos?.name,
                quantity: pos?.quantity ?? 0,
                news: [],
                hasMaterialEvent: false,
                materialEvents: [],
              });
            }
          }

          // Sort: material events first
          results.sort((a, b) => {
            if (a.hasMaterialEvent && !b.hasMaterialEvent) return -1;
            if (!a.hasMaterialEvent && b.hasMaterialEvent) return 1;
            return 0;
          });

          return {
            message: totalMaterialEvents > 0
              ? `持仓新闻扫描完成，发现 ${totalMaterialEvents} 条重大事件`
              : `持仓新闻扫描完成，未发现重大事件`,
            positionsScanned: positions.length,
            totalMaterialEvents,
            results,
          };
        }

        case "sentiment": {
          const sentimentResults: Array<{
            symbol: string;
            name?: string;
            sentimentScore?: number;
            sentimentLabel?: string;
            newsCount?: number;
          }> = [];

          let weightedSentiment = 0;
          let totalValue = 0;

          for (const symbol of symbols) {
            const pos = posMap.get(symbol);
            try {
              const pyResult = await runPythonScript<{
                sentiment_score?: number;
                sentiment_label?: string;
                news_count?: number;
              }>(
                "engines/news_engine.py",
                {
                  action: "sentiment",
                  symbol,
                  market: pos?.market === "us_stock" ? "us" : "cn",
                  days,
                },
                config,
              );

              const score = pyResult.ok ? (pyResult.data?.sentiment_score ?? 0) : 0;
              const label = pyResult.ok ? (pyResult.data?.sentiment_label ?? "neutral") : "unknown";
              const newsCount = pyResult.ok ? (pyResult.data?.news_count ?? 0) : 0;

              const mv = pos?.marketValue ?? 0;
              weightedSentiment += score * mv;
              totalValue += mv;

              sentimentResults.push({
                symbol,
                name: pos?.name,
                sentimentScore: score,
                sentimentLabel: String(label),
                newsCount: Number(newsCount),
              });
            } catch {
              sentimentResults.push({
                symbol,
                name: pos?.name,
                sentimentScore: 0,
                sentimentLabel: "unknown",
                newsCount: 0,
              });
            }
          }

          const portfolioSentiment = totalValue > 0
            ? Math.round((weightedSentiment / totalValue) * 100) / 100
            : 0;

          const portfolioLabel =
            portfolioSentiment > 0.3 ? "偏多" :
            portfolioSentiment < -0.3 ? "偏空" : "中性";

          return {
            message: `持仓情绪汇总: ${portfolioLabel} (综合评分 ${portfolioSentiment})`,
            portfolioSentiment,
            portfolioLabel,
            positions: sentimentResults,
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
