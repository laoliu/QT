/**
 * market_overview tool — AI-callable one-shot market dashboard.
 *
 * A stock trader starts their day with a market overview: major indices,
 * market breadth (advance/decline), key sector movements, top gainers/losers,
 * and northbound fund flow. This tool aggregates it all in one call.
 *
 * Actions:
 *   dashboard       — full market dashboard (indices + breadth + sectors + movers)
 *   indices         — major market indices only
 *   breadth         — advance/decline/unchanged stats
 *   top_movers      — top gainers and losers
 *   sector_rotation — sector heatmap with fund flows
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider } from "../data/market-data-provider.js";
import type { QuantTradingConfig } from "../types.js";

interface IndexData {
  name: string;
  code: string;
  price: number | null;
  changePct: number | null;
  volume: number | null;
}

interface MoverItem {
  symbol: string;
  name: string;
  price: number | null;
  changePct: number;
}

export function createMarketOverviewTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  const dp = createDataProvider(config);

  const MAJOR_INDICES = [
    { code: "000001", name: "上证指数" },
    { code: "399001", name: "深证成指" },
    { code: "399006", name: "创业板指" },
    { code: "000688", name: "科创50" },
    { code: "000300", name: "沪深300" },
    { code: "000905", name: "中证500" },
    { code: "000852", name: "中证1000" },
  ];

  async function fetchIndex(idx: { code: string; name: string }): Promise<IndexData> {
    try {
      const quote = await dp.getQuote(idx.code, "cn_stock" as never);
      return {
        name: idx.name,
        code: idx.code,
        price: quote?.price ?? null,
        changePct: quote?.changePct ?? null,
        volume: quote?.volume ?? null,
      };
    } catch {
      return { name: idx.name, code: idx.code, price: null, changePct: null, volume: null };
    }
  }

  async function fetchBreadth(): Promise<Record<string, unknown>> {
    try {
      const result = (await runPythonScript(
        "market_breadth.py",
        { action: "breadth" },
        config,
      )) as Record<string, unknown>;
      return result ?? {};
    } catch {
      return {};
    }
  }

  async function fetchTopMovers(
    direction: "gainers" | "losers",
    limit: number,
  ): Promise<MoverItem[]> {
    try {
      const result = (await runPythonScript(
        "screener_engine.py",
        { action: "top_movers", direction, limit },
        config,
      )) as { movers?: MoverItem[] };
      return result?.movers ?? [];
    } catch {
      return [];
    }
  }

  async function fetchSectorOverview(): Promise<Record<string, unknown>[]> {
    try {
      const result = (await runPythonScript(
        "sector_analysis.py",
        { action: "ranking", market: "cn_stock", sortBy: "change_pct", limit: 15 },
        config,
      )) as { sectors?: Record<string, unknown>[] };
      return result?.sectors ?? [];
    } catch {
      return [];
    }
  }

  async function fetchNorthboundFlow(): Promise<Record<string, unknown>> {
    try {
      const result = (await runPythonScript(
        "fund_flow.py",
        { action: "northbound" },
        config,
      )) as Record<string, unknown>;
      return result ?? {};
    } catch {
      return {};
    }
  }

  return {
    name: "market_overview",
    description:
      "市场总览工具。一键获取大盘指数、涨跌家数、板块热力、涨跌幅榜、北向资金等市场全景。" +
      "action=dashboard 完整市场看板, action=indices 主要指数, " +
      "action=breadth 涨跌家数统计, action=top_movers 涨跌幅榜, " +
      "action=sector_rotation 板块轮动热力。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["dashboard", "indices", "breadth", "top_movers", "sector_rotation"],
          description: "操作类型",
        },
        limit: {
          type: "number",
          description: "返回数量（top_movers 默认10）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "dashboard";
      const limit = (args.limit as number) ?? 10;

      if (action === "indices") {
        const indices = await Promise.all(MAJOR_INDICES.map(fetchIndex));
        return { action: "indices", indices };
      }

      if (action === "breadth") {
        const breadth = await fetchBreadth();
        return { action: "breadth", ...breadth };
      }

      if (action === "top_movers") {
        const [gainers, losers] = await Promise.all([
          fetchTopMovers("gainers", limit),
          fetchTopMovers("losers", limit),
        ]);
        return { action: "top_movers", gainers, losers };
      }

      if (action === "sector_rotation") {
        const [sectors, northbound] = await Promise.all([
          fetchSectorOverview(),
          fetchNorthboundFlow(),
        ]);
        return { action: "sector_rotation", sectors, northbound };
      }

      // Full dashboard
      const [indices, breadth, gainers, losers, sectors, northbound] = await Promise.all([
        Promise.all(MAJOR_INDICES.map(fetchIndex)),
        fetchBreadth(),
        fetchTopMovers("gainers", 5),
        fetchTopMovers("losers", 5),
        fetchSectorOverview(),
        fetchNorthboundFlow(),
      ]);

      const now = new Date();
      const hour = now.getHours();
      let marketPhase: string;
      const day = now.getDay();
      if (day === 0 || day === 6) {
        marketPhase = "非交易日";
      } else if (hour < 9 || (hour === 9 && now.getMinutes() < 30)) {
        marketPhase = "盘前";
      } else if (hour < 11 || (hour === 11 && now.getMinutes() <= 30)) {
        marketPhase = "盘中（上午）";
      } else if (hour < 13) {
        marketPhase = "午间休市";
      } else if (hour < 15) {
        marketPhase = "盘中（下午）";
      } else {
        marketPhase = "盘后";
      }

      return {
        action: "dashboard",
        timestamp: now.toISOString(),
        marketPhase,
        indices,
        breadth,
        topGainers: gainers,
        topLosers: losers,
        sectorHighlights: sectors.slice(0, 8),
        northboundFlow: northbound,
      };
    },
  };
}
