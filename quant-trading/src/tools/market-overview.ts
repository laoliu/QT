/**
 * market_overview tool — AI-callable morning briefing / market overview.
 *
 * Provides a comprehensive daily snapshot for traders:
 *   briefing        — full morning briefing (indices + holdings + alerts + events)
 *   indices         — major market index snapshots
 *   holdings_update — quick P&L update on current portfolio holdings
 *   today_events    — today's scheduled events (earnings, alerts, tasks)
 *   sector_heatmap  — sector performance overview
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { QuantTradingConfig, Portfolio, QuoteSnapshot } from "../types.js";

// Major A-share indices
const CN_INDICES = [
  { code: "000001", name: "上证指数" },
  { code: "399001", name: "深证成指" },
  { code: "399006", name: "创业板指" },
  { code: "000300", name: "沪深300" },
  { code: "000905", name: "中证500" },
  { code: "000852", name: "中证1000" },
];

export function createMarketOverviewTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getEngine: () => LiveEngine | null,
  deps: {
    loadPortfolio: () => Promise<Portfolio>;
    getAlertCount?: () => Promise<number>;
    getCondOrderCount?: () => Promise<number>;
    getTaskCount?: () => Promise<number>;
  },
) {
  const dp = createDataProvider(config);

  async function fetchIndices() {
    const results: Array<{
      name: string;
      code: string;
      price?: number;
      changePct?: string;
      status: string;
    }> = [];

    for (const idx of CN_INDICES) {
      try {
        const quote = await dp.getQuote(idx.code, "cn_stock" as never);
        if (quote) {
          results.push({
            name: idx.name,
            code: idx.code,
            price: quote.price,
            changePct: `${quote.changePct >= 0 ? "+" : ""}${quote.changePct.toFixed(2)}%`,
            status: "ok",
          });
        } else {
          results.push({ name: idx.name, code: idx.code, status: "no_data" });
        }
      } catch {
        results.push({ name: idx.name, code: idx.code, status: "error" });
      }
    }
    return results;
  }

  async function fetchHoldingsUpdate(portfolio: Portfolio) {
    const updates: Array<{
      symbol: string;
      name: string;
      quantity: number;
      avgCost: number;
      currentPrice?: number;
      changePct?: string;
      unrealizedPnl?: string;
      unrealizedPnlPct?: string;
    }> = [];

    for (const pos of portfolio.positions) {
      try {
        const market = detectMarketFromSymbol(pos.symbol);
        const quote = await dp.getQuote(pos.symbol, market as never);
        const curPrice = quote?.price;
        const pnl = curPrice != null ? (curPrice - pos.avgCost) * pos.quantity : undefined;
        const pnlPct = curPrice != null ? ((curPrice - pos.avgCost) / pos.avgCost) * 100 : undefined;

        updates.push({
          symbol: pos.symbol,
          name: pos.name ?? pos.symbol,
          quantity: pos.quantity,
          avgCost: pos.avgCost,
          currentPrice: curPrice,
          changePct: quote ? `${quote.changePct >= 0 ? "+" : ""}${quote.changePct.toFixed(2)}%` : undefined,
          unrealizedPnl: pnl != null ? `¥${pnl.toFixed(2)}` : undefined,
          unrealizedPnlPct: pnlPct != null ? `${pnlPct >= 0 ? "+" : ""}${pnlPct.toFixed(2)}%` : undefined,
        });
      } catch {
        updates.push({
          symbol: pos.symbol,
          name: pos.name ?? pos.symbol,
          quantity: pos.quantity,
          avgCost: pos.avgCost,
        });
      }
    }

    const totalPnl = updates
      .filter((u) => u.unrealizedPnl != null)
      .reduce((s, u) => s + parseFloat(u.unrealizedPnl!.replace("¥", "")), 0);

    return { positions: updates, totalUnrealizedPnl: `¥${totalPnl.toFixed(2)}` };
  }

  return {
    name: "market_overview",
    description:
      "市场总览/每日早报工具。提供大盘指数、持仓概况、预警事件、板块热度的一站式摘要。\n" +
      "action=briefing 完整早报, action=indices 指数快照, " +
      "action=holdings_update 持仓快速更新, action=today_events 今日事件, " +
      "action=sector_heatmap 板块热度。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["briefing", "indices", "holdings_update", "today_events", "sector_heatmap"],
          description: "操作类型",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;

      switch (action) {
        case "indices": {
          return { 大盘指数: await fetchIndices() };
        }

        case "holdings_update": {
          const portfolio = await deps.loadPortfolio();
          if (portfolio.positions.length === 0) {
            return { message: "当前无持仓" };
          }
          return await fetchHoldingsUpdate(portfolio);
        }

        case "today_events": {
          const alertCount = deps.getAlertCount ? await deps.getAlertCount() : 0;
          const condOrderCount = deps.getCondOrderCount ? await deps.getCondOrderCount() : 0;
          const taskCount = deps.getTaskCount ? await deps.getTaskCount() : 0;

          const engine = getEngine();
          const engineStatus = engine?.getStatus?.() ?? "未启动";
          const isRunning = engineStatus === "running";

          return {
            引擎状态: engineStatus,
            价格预警数: alertCount,
            条件单数: condOrderCount,
            定时任务数: taskCount,
            日期: new Date().toISOString().slice(0, 10),
          };
        }

        case "sector_heatmap": {
          try {
            const { runPythonScript } = await import("../bridge/python-bridge.js");
            const result = await runPythonScript<{
              sectors?: Array<{ name: string; changePct: number; leadingStocks?: string[] }>;
            }>("engines/sector_analysis.py", { action: "sector_overview" }, config);
            return result ?? { message: "板块数据暂不可用" };
          } catch {
            return { message: "板块数据获取失败，请检查数据源" };
          }
        }

        case "briefing": {
          const today = new Date().toISOString().slice(0, 10);
          const weekday = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][new Date().getDay()];

          // Fetch data in parallel
          const [indices, portfolio] = await Promise.all([
            fetchIndices(),
            deps.loadPortfolio(),
          ]);

          const holdingsUpdate = portfolio.positions.length > 0
            ? await fetchHoldingsUpdate(portfolio)
            : null;

          const alertCount = deps.getAlertCount ? await deps.getAlertCount() : 0;
          const condOrderCount = deps.getCondOrderCount ? await deps.getCondOrderCount() : 0;

          const engine = getEngine();
          const engineStatus = engine?.getStatus?.() ?? "stopped";

          return {
            日期: `${today} ${weekday}`,
            大盘指数: indices,
            持仓概况: holdingsUpdate
              ? {
                  持仓数: portfolio.positions.length,
                  可用资金: `¥${portfolio.cash.toFixed(2)}`,
                  总市值: `¥${portfolio.totalValue.toFixed(2)}`,
                  浮动盈亏: holdingsUpdate.totalUnrealizedPnl,
                  明细: holdingsUpdate.positions,
                }
              : { message: "暂无持仓" },
            系统状态: {
              引擎: engineStatus === "running" ? "运行中" : engineStatus,
              价格预警: alertCount,
              条件单: condOrderCount,
            },
          };
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}
