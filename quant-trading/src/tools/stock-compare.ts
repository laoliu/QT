/**
 * stock_compare tool — AI-callable for side-by-side comparison of multiple stocks.
 *
 * A stock trader frequently needs to compare 2-5 candidates before making a
 * buy/sell decision. This tool fetches quotes, fundamentals, and technical
 * indicators for each symbol, then builds a unified comparison table.
 *
 * Actions:
 *   compare       — full multi-dimension comparison (quote + fundamental + technical)
 *   quick_compare — lightweight comparison (quote + key ratios only)
 *   rank          — rank symbols by a single metric (pe, roe, market_cap, etc.)
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { QuantTradingConfig } from "../types.js";

interface CompareRow {
  symbol: string;
  name: string;
  price: number | null;
  changePct: number | null;
  pe: number | null;
  pb: number | null;
  roe: number | null;
  marketCap: number | null;
  revenueGrowth: number | null;
  netProfitGrowth: number | null;
  debtRatio: number | null;
  dividendYield: number | null;
  industry: string | null;
  technical: {
    rsi14: number | null;
    macdHist: number | null;
    aboveMa20: boolean | null;
    aboveMa60: boolean | null;
    volumeRatio: number | null;
  } | null;
}

export function createStockCompareTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  const dp = createDataProvider(config);

  async function fetchStockData(symbol: string): Promise<CompareRow> {
    const market = detectMarketFromSymbol(symbol);
    const row: CompareRow = {
      symbol,
      name: "",
      price: null,
      changePct: null,
      pe: null,
      pb: null,
      roe: null,
      marketCap: null,
      revenueGrowth: null,
      netProfitGrowth: null,
      debtRatio: null,
      dividendYield: null,
      industry: null,
      technical: null,
    };

    // Fetch quote
    try {
      const quote = await dp.getQuote(symbol, market as never);
      if (quote) {
        row.name = quote.symbol?.name || symbol;
        row.price = quote.price;
        row.changePct = quote.changePct;
      }
    } catch {
      // quote unavailable
    }

    // Fetch fundamentals via Python
    try {
      const result = (await runPythonScript(
        "financial_provider.py",
        { action: "fundamental", symbol, market },
        config,
      )) as Record<string, unknown>;
      if (result) {
        row.pe = result.pe != null ? Number(result.pe) : null;
        row.pb = result.pb != null ? Number(result.pb) : null;
        row.roe = result.roe != null ? Number(result.roe) : null;
        row.marketCap = result.total_market_val != null ? Number(result.total_market_val) : null;
        row.revenueGrowth =
          result.revenue_growth_yoy != null ? Number(result.revenue_growth_yoy) : null;
        row.netProfitGrowth =
          result.net_profit_growth_yoy != null ? Number(result.net_profit_growth_yoy) : null;
        row.debtRatio = result.debt_ratio != null ? Number(result.debt_ratio) : null;
        row.dividendYield =
          result.dividend_yield != null ? Number(result.dividend_yield) : null;
        row.industry = result.industry ? String(result.industry) : null;
      }
    } catch {
      // fundamentals unavailable
    }

    // Fetch technical indicators via Python
    try {
      const result = (await runPythonScript(
        "indicator_calculator.py",
        { action: "multi_indicator", symbol, indicators: ["rsi", "macd", "ma"] },
        config,
      )) as Record<string, unknown>;
      if (result) {
        row.technical = {
          rsi14: result.rsi14 != null ? Number(result.rsi14) : null,
          macdHist: result.macd_hist != null ? Number(result.macd_hist) : null,
          aboveMa20: result.above_ma20 != null ? Boolean(result.above_ma20) : null,
          aboveMa60: result.above_ma60 != null ? Boolean(result.above_ma60) : null,
          volumeRatio: result.volume_ratio != null ? Number(result.volume_ratio) : null,
        };
      }
    } catch {
      // technical indicators unavailable
    }

    return row;
  }

  function rankByMetric(rows: CompareRow[], metric: string): CompareRow[] {
    const getValue = (row: CompareRow): number | null => {
      switch (metric) {
        case "pe":
          return row.pe;
        case "pb":
          return row.pb;
        case "roe":
          return row.roe;
        case "market_cap":
          return row.marketCap;
        case "revenue_growth":
          return row.revenueGrowth;
        case "net_profit_growth":
          return row.netProfitGrowth;
        case "dividend_yield":
          return row.dividendYield;
        case "change_pct":
          return row.changePct;
        default:
          return null;
      }
    };

    // Higher is better for most metrics, except PE/PB (lower is better)
    const lowerIsBetter = metric === "pe" || metric === "pb" || metric === "debt_ratio";

    return [...rows].sort((a, b) => {
      const va = getValue(a);
      const vb = getValue(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      return lowerIsBetter ? va - vb : vb - va;
    });
  }

  return {
    name: "stock_compare",
    description:
      "个股对比工具。将多只股票（2-10只）从行情、估值、基本面、技术面等多维度并排对比。" +
      "action=compare 全面对比, action=quick_compare 快速对比（仅行情+估值）, " +
      "action=rank 按单一指标排序。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "quick_compare", "rank"],
          description: "操作类型",
        },
        symbols: {
          type: "array",
          items: { type: "string" },
          description: '要对比的股票代码列表，如 ["600519", "000858", "002304"]',
        },
        metric: {
          type: "string",
          enum: [
            "pe",
            "pb",
            "roe",
            "market_cap",
            "revenue_growth",
            "net_profit_growth",
            "dividend_yield",
            "change_pct",
          ],
          description: "排名指标（仅 action=rank 时使用）",
        },
      },
      required: ["action", "symbols"],
    },

    async execute(args: Record<string, unknown>) {
      const action = (args.action as string) ?? "compare";
      const symbols = (args.symbols as string[]) ?? [];
      const metric = (args.metric as string) ?? "roe";

      if (symbols.length < 2) {
        return { error: "至少需要 2 个股票代码进行对比" };
      }
      if (symbols.length > 10) {
        return { error: "最多支持 10 只股票对比" };
      }

      // Fetch all stock data in parallel
      const rows = await Promise.all(symbols.map((s) => fetchStockData(s.trim())));

      if (action === "quick_compare") {
        return {
          action: "quick_compare",
          count: rows.length,
          comparison: rows.map((r) => ({
            symbol: r.symbol,
            name: r.name,
            price: r.price,
            changePct: r.changePct,
            pe: r.pe,
            pb: r.pb,
            roe: r.roe,
            marketCap: r.marketCap,
            industry: r.industry,
          })),
        };
      }

      if (action === "rank") {
        const ranked = rankByMetric(rows, metric);
        return {
          action: "rank",
          metric,
          count: ranked.length,
          ranking: ranked.map((r, i) => ({
            rank: i + 1,
            symbol: r.symbol,
            name: r.name,
            value:
              r[metric as keyof CompareRow] ??
              (r as unknown as Record<string, unknown>)[metric] ??
              null,
          })),
        };
      }

      // Full compare
      return {
        action: "compare",
        count: rows.length,
        comparison: rows.map((r) => ({
          symbol: r.symbol,
          name: r.name,
          quote: {
            price: r.price,
            changePct: r.changePct,
          },
          valuation: {
            pe: r.pe,
            pb: r.pb,
            dividendYield: r.dividendYield,
          },
          fundamental: {
            roe: r.roe,
            marketCap: r.marketCap,
            revenueGrowth: r.revenueGrowth,
            netProfitGrowth: r.netProfitGrowth,
            debtRatio: r.debtRatio,
            industry: r.industry,
          },
          technical: r.technical,
        })),
        summary: buildComparisonSummary(rows),
      };
    },
  };
}

function buildComparisonSummary(rows: CompareRow[]): Record<string, unknown> {
  const withPe = rows.filter((r) => r.pe != null && r.pe > 0);
  const withRoe = rows.filter((r) => r.roe != null);

  return {
    lowestPE: withPe.length > 0 ? withPe.sort((a, b) => a.pe! - b.pe!)[0].symbol : null,
    highestROE: withRoe.length > 0 ? withRoe.sort((a, b) => b.roe! - a.roe!)[0].symbol : null,
    bestPerformer: rows.filter((r) => r.changePct != null).length > 0
      ? rows
          .filter((r) => r.changePct != null)
          .sort((a, b) => b.changePct! - a.changePct!)[0].symbol
      : null,
    avgPE:
      withPe.length > 0
        ? Number((withPe.reduce((s, r) => s + r.pe!, 0) / withPe.length).toFixed(1))
        : null,
    avgROE:
      withRoe.length > 0
        ? Number((withRoe.reduce((s, r) => s + r.roe!, 0) / withRoe.length).toFixed(1))
        : null,
  };
}
