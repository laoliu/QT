/**
 * stock_compare tool — side-by-side comparison of 2-5 stocks across
 * fundamentals, technicals, and capital flow dimensions.
 *
 * Use case: trader wants to decide which stock to buy among several candidates.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { QuantTradingConfig } from "../types.js";

export function createStockCompareTool(api: OpenClawPluginApi, config: QuantTradingConfig) {
  const provider = createDataProvider(config);

  return {
    name: "stock_compare",
    description:
      "个股对比工具 — 将2~5只股票按技术面、基本面、资金面多维度并排对比。\n" +
      "action=compare 多维度对比, action=valuation 估值对比, " +
      "action=technical 技术面对比, action=performance 区间涨跌幅对比。\n" +
      "结果以对比表格形式呈现，辅助选股决策。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["compare", "valuation", "technical", "performance"],
          description:
            "操作: compare=全维度对比, valuation=估值对比, technical=技术面对比, performance=涨跌幅对比",
        },
        symbols: {
          type: "string",
          description: "股票代码列表，逗号分隔，2~5只。如 '600519,000858,000333'",
        },
        period: {
          type: "string",
          enum: ["1w", "1m", "3m", "6m", "1y"],
          description: "对比区间（performance 时使用），默认 3m",
        },
      },
      required: ["action", "symbols"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbolsRaw = args.symbols as string;
      const period = (args.period as string) ?? "3m";

      if (!symbolsRaw) return { error: "请提供 symbols（逗号分隔的股票代码）" };

      const symbols = symbolsRaw
        .split(/[,，\s]+/)
        .map((s) => s.trim())
        .filter(Boolean);
      if (symbols.length < 2) return { error: "至少需要2只股票进行对比" };
      if (symbols.length > 5) return { error: "最多支持5只股票同时对比" };

      try {
        switch (action) {
          case "compare":
            return await fullCompare(symbols);
          case "valuation":
            return await valuationCompare(symbols);
          case "technical":
            return await technicalCompare(symbols);
          case "performance":
            return await performanceCompare(symbols, period);
          default:
            return { error: `未知操作: ${action}` };
        }
      } catch (err) {
        return { error: `对比失败: ${err instanceof Error ? err.message : String(err)}` };
      }
    },
  };

  /** Full multi-dimension compare */
  async function fullCompare(symbols: string[]) {
    const results = await Promise.all(
      symbols.map(async (symbol) => {
        const market = detectMarketFromSymbol(symbol);
        const [quote, fundamentals] = await Promise.all([
          provider.getQuote(symbol, market as never).catch(() => null),
          fetchFundamentals(symbol),
        ]);
        return {
          symbol,
          name: quote?.symbol?.name ?? symbol,
          // 行情
          price: quote?.price ?? null,
          changePct: quote?.changePct ?? null,
          volume: quote?.volume ?? null,
          amount: quote?.amount ?? null,
          // 基本面
          pe: fundamentals?.pe ?? null,
          pb: fundamentals?.pb ?? null,
          roe: fundamentals?.roe ?? null,
          totalMarketCap: fundamentals?.total_market_cap ?? null,
          revenueGrowth: fundamentals?.revenue_growth ?? null,
          profitGrowth: fundamentals?.profit_growth ?? null,
          debtRatio: fundamentals?.debt_ratio ?? null,
          dividendYield: fundamentals?.dividend_yield ?? null,
          grossMargin: fundamentals?.gross_margin ?? null,
          netMargin: fundamentals?.net_margin ?? null,
        };
      }),
    );

    return {
      action: "compare",
      count: results.length,
      dimensions: ["行情", "估值", "盈利能力", "成长性", "财务健康", "分红"],
      stocks: results,
      tip: "对比维度: PE/PB看估值, ROE/毛利率看盈利能力, 增速看成长性, 资产负债率看财务健康",
    };
  }

  /** Valuation-focused compare */
  async function valuationCompare(symbols: string[]) {
    const results = await Promise.all(
      symbols.map(async (symbol) => {
        const fundamentals = await fetchFundamentals(symbol);
        return {
          symbol,
          name: fundamentals?.name ?? symbol,
          pe: fundamentals?.pe ?? null,
          peTtm: fundamentals?.pe_ttm ?? null,
          pb: fundamentals?.pb ?? null,
          ps: fundamentals?.ps ?? null,
          peg: fundamentals?.peg ?? null,
          roe: fundamentals?.roe ?? null,
          totalMarketCap: fundamentals?.total_market_cap ?? null,
          circulatingMarketCap: fundamentals?.circulating_market_cap ?? null,
          dividendYield: fundamentals?.dividend_yield ?? null,
          evToEbitda: fundamentals?.ev_to_ebitda ?? null,
        };
      }),
    );

    return {
      action: "valuation",
      count: results.length,
      stocks: results,
      tip: "PE<行业均值0.7x为低估; PEG<1为成长合理; 股息率>3%为高分红",
    };
  }

  /** Technical compare */
  async function technicalCompare(symbols: string[]) {
    const results = await Promise.all(
      symbols.map(async (symbol) => {
        const market = detectMarketFromSymbol(symbol);
        const [quote, klineResult] = await Promise.all([
          provider.getQuote(symbol, market as never).catch(() => null),
          fetchKlinesWithIndicators(symbol),
        ]);
        return {
          symbol,
          name: quote?.symbol?.name ?? symbol,
          price: quote?.price ?? null,
          changePct: quote?.changePct ?? null,
          // 技术指标
          ma5: klineResult?.ma5 ?? null,
          ma10: klineResult?.ma10 ?? null,
          ma20: klineResult?.ma20 ?? null,
          ma60: klineResult?.ma60 ?? null,
          rsi14: klineResult?.rsi14 ?? null,
          macdSignal: klineResult?.macd_signal ?? null,
          bollingerPosition: klineResult?.bollinger_position ?? null,
          trendDirection: klineResult?.trend ?? null,
          volumeRatio: klineResult?.volume_ratio ?? null,
          high52w: klineResult?.high_52w ?? null,
          low52w: klineResult?.low_52w ?? null,
          distFromHigh52w: klineResult?.dist_from_high_52w ?? null,
        };
      }),
    );

    return {
      action: "technical",
      count: results.length,
      stocks: results,
      tip: "均线多头排列=强势; RSI>70超买,RSI<30超卖; 距52周高点越近越强势",
    };
  }

  /** Performance over a period */
  async function performanceCompare(symbols: string[], period: string) {
    const days = periodToDays(period);
    const endDate = new Date().toISOString().slice(0, 10);
    const startDate = new Date(Date.now() - days * 24 * 60 * 60 * 1000)
      .toISOString()
      .slice(0, 10);

    const results = await Promise.all(
      symbols.map(async (symbol) => {
        const market = detectMarketFromSymbol(symbol);
        try {
          const klines = await provider.getKlines(symbol, "1d", startDate, endDate, market as never);
          if (!klines || klines.length < 2) {
            return { symbol, returnPct: null, maxDrawdown: null, volatility: null };
          }

          const closes = klines.map((k) => k.close);
          const firstClose = closes[0];
          const lastClose = closes[closes.length - 1];
          const returnPct = ((lastClose - firstClose) / firstClose) * 100;

          // Max drawdown
          let peak = closes[0];
          let maxDd = 0;
          for (const c of closes) {
            if (c > peak) peak = c;
            const dd = ((peak - c) / peak) * 100;
            if (dd > maxDd) maxDd = dd;
          }

          // Annualized volatility
          const dailyReturns: number[] = [];
          for (let i = 1; i < closes.length; i++) {
            dailyReturns.push((closes[i] - closes[i - 1]) / closes[i - 1]);
          }
          const mean = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
          const variance =
            dailyReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) / dailyReturns.length;
          const annualVol = Math.sqrt(variance) * Math.sqrt(252) * 100;

          const quote = await provider.getQuote(symbol, market as never).catch(() => null);

          return {
            symbol,
            name: quote?.symbol?.name ?? symbol,
            currentPrice: lastClose,
            returnPct: Math.round(returnPct * 100) / 100,
            maxDrawdown: Math.round(maxDd * 100) / 100,
            annualizedVolatility: Math.round(annualVol * 100) / 100,
            tradingDays: closes.length,
          };
        } catch {
          return { symbol, returnPct: null, maxDrawdown: null, volatility: null };
        }
      }),
    );

    // Rank by return
    const ranked = [...results]
      .filter((r) => r.returnPct !== null)
      .sort((a, b) => (b.returnPct ?? 0) - (a.returnPct ?? 0));

    return {
      action: "performance",
      period,
      startDate,
      endDate,
      stocks: results,
      ranking: ranked.map((r, i) => ({
        rank: i + 1,
        symbol: r.symbol,
        name: r.name,
        returnPct: r.returnPct,
      })),
      tip: "收益率排名配合最大回撤一起看，高收益+低回撤=最优标的",
    };
  }

  /** Fetch fundamentals via Python */
  async function fetchFundamentals(symbol: string): Promise<Record<string, unknown> | null> {
    try {
      const result = await runPythonScript(
        "engines/screener_engine.py",
        { action: "fundamentals", symbol, market: "cn" },
        config,
      );
      return (result as { ok: boolean; data?: Record<string, unknown> }).data ?? null;
    } catch {
      return null;
    }
  }

  /** Fetch K-lines with computed indicators */
  async function fetchKlinesWithIndicators(
    symbol: string,
  ): Promise<Record<string, unknown> | null> {
    try {
      const result = await runPythonScript(
        "engines/compute_indicators.py",
        { action: "indicators", symbol, market: "cn", period: 120 },
        config,
      );
      return (result as { ok: boolean; data?: Record<string, unknown> }).data ?? null;
    } catch {
      return null;
    }
  }

  function periodToDays(period: string): number {
    switch (period) {
      case "1w":
        return 7;
      case "1m":
        return 30;
      case "3m":
        return 90;
      case "6m":
        return 180;
      case "1y":
        return 365;
      default:
        return 90;
    }
  }
}
