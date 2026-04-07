/**
 * stop_advisor tool — AI-callable tool for intelligent stop-loss / take-profit recommendations.
 *
 * Analyzes a stock's historical price data using multiple methods (ATR, support/resistance,
 * Bollinger Bands, moving averages) and recommends optimal stop-loss and take-profit levels
 * based on the user's risk preference.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { MarketDataProvider } from "../data/market-data-provider.js";
import { detectMarketFromSymbol } from "../data/market-data-provider.js";
import { computeStopAdvice, computeBatchStopAdvice } from "../services/stop-advisor.js";
import type { KlineBar, QuantTradingConfig } from "../types.js";

export function createStopAdvisorTool(
  _api: OpenClawPluginApi,
  config: QuantTradingConfig,
  getDataProvider: () => MarketDataProvider,
  getPortfolioPositions?: () => Promise<
    Array<{ symbol: string; avgCost: number; currentPrice?: number; quantity: number }>
  >,
) {
  return {
    name: "stop_advisor",
    description:
      "【智能止盈止损建议】根据个股波动率(ATR)、支撑阻力位、布林带、均线等多种方法，自动计算最优止盈止损位。\n" +
      "action=advise 单只股票止盈止损建议（需提供 symbol 和 entry_price）,\n" +
      "action=diagnose 对当前持仓组合批量生成止盈止损建议。\n" +
      "每次推荐包含: 止损价、止盈价、盈亏比、方法说明、置信度。\n" +
      "风格: short=短线(3%止损,盈亏比≥2), mid=中线(5%止损,盈亏比≥2), long=长线(8%止损,盈亏比≥2.5)。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["advise", "diagnose"],
          description: "操作: advise=单股建议, diagnose=持仓组合批量诊断",
        },
        symbol: {
          type: "string",
          description: "股票代码（action=advise 时必填）",
        },
        entry_price: {
          type: "number",
          description: "买入价/入场价（action=advise 时必填）",
        },
        style: {
          type: "string",
          enum: ["short", "mid", "long"],
          description: "交易风格: short=短线, mid=中线(默认), long=长线",
        },
        max_stop_loss_pct: {
          type: "number",
          description: "最大止损比例(0-1)，如0.05表示5%。不填则按风格默认",
        },
        min_reward_risk_ratio: {
          type: "number",
          description: "最小盈亏比，默认2.0",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const style = (args.style as "short" | "mid" | "long") ?? "mid";
      const riskOverride = {
        style,
        ...(args.max_stop_loss_pct != null
          ? { maxStopLossPct: args.max_stop_loss_pct as number }
          : {}),
        ...(args.min_reward_risk_ratio != null
          ? { minRewardRiskRatio: args.min_reward_risk_ratio as number }
          : {}),
      };

      if (action === "advise") {
        return handleAdvise(args, riskOverride, getDataProvider);
      }

      if (action === "diagnose") {
        return handleDiagnose(riskOverride, getDataProvider, getPortfolioPositions);
      }

      return { error: `未知操作: ${action}` };
    },
  };
}

async function handleAdvise(
  args: Record<string, unknown>,
  riskOverride: { style: "short" | "mid" | "long"; maxStopLossPct?: number; minRewardRiskRatio?: number },
  getDataProvider: () => MarketDataProvider,
): Promise<Record<string, unknown>> {
  const symbol = args.symbol as string;
  const entryPrice = args.entry_price as number;
  if (!symbol || !entryPrice) {
    return { error: "请提供 symbol 和 entry_price" };
  }

  const provider = getDataProvider();
  const market = detectMarketFromSymbol(symbol);

  // Fetch quote for current price
  const quote = await provider.getQuote(symbol, market);
  const currentPrice = quote?.price ?? entryPrice;

  // Fetch 120 days of daily klines for indicator calculation
  const endDate = new Date().toISOString().slice(0, 10);
  const startDate = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
  let bars: KlineBar[];
  try {
    bars = await provider.getKlines(symbol, "1d", startDate, endDate, market);
  } catch {
    return { error: `获取 ${symbol} K线数据失败，请检查数据源` };
  }

  if (bars.length < 5) {
    return { error: `${symbol} K线数据不足(${bars.length}根)，至少需要5根日K` };
  }

  try {
    const result = computeStopAdvice(symbol, entryPrice, currentPrice, bars, riskOverride);
    return formatAdviceResult(result);
  } catch (err) {
    return { error: `计算止盈止损建议失败: ${(err as Error).message}` };
  }
}

async function handleDiagnose(
  riskOverride: { style: "short" | "mid" | "long"; maxStopLossPct?: number; minRewardRiskRatio?: number },
  getDataProvider: () => MarketDataProvider,
  getPortfolioPositions?: () => Promise<
    Array<{ symbol: string; avgCost: number; currentPrice?: number; quantity: number }>
  >,
): Promise<Record<string, unknown>> {
  if (!getPortfolioPositions) {
    return { error: "组合数据不可用" };
  }

  const positions = await getPortfolioPositions();
  if (positions.length === 0) {
    return { ok: true, message: "当前无持仓" };
  }

  const provider = getDataProvider();
  const endDate = new Date().toISOString().slice(0, 10);
  const startDate = new Date(Date.now() - 180 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);

  // Fetch klines for all positions in parallel
  const positionsWithBars = await Promise.all(
    positions.map(async (pos) => {
      const market = detectMarketFromSymbol(pos.symbol);
      let bars: KlineBar[] = [];
      let currentPrice = pos.currentPrice ?? pos.avgCost;
      try {
        bars = await provider.getKlines(pos.symbol, "1d", startDate, endDate, market);
        // Update current price from latest bar if available
        if (bars.length > 0 && !pos.currentPrice) {
          currentPrice = bars[bars.length - 1].close;
        }
      } catch {
        // Non-fatal — will return error for this position
      }
      // Also try to get real-time quote
      try {
        const quote = await provider.getQuote(pos.symbol, market);
        if (quote) currentPrice = quote.price;
      } catch {
        // Use latest bar close as fallback
      }
      return {
        symbol: pos.symbol,
        entryPrice: pos.avgCost,
        currentPrice,
        bars,
      };
    }),
  );

  const results = computeBatchStopAdvice(positionsWithBars, riskOverride);

  const formatted = results.map((r) => {
    if ("error" in r) {
      return { symbol: r.symbol, error: r.error };
    }
    return formatAdviceResult(r);
  });

  // Summary statistics
  const valid = results.filter((r) => !("error" in r)) as Array<Exclude<typeof results[number], { error: string }>>;
  const needStopLoss = valid.filter((r) => {
    const pnlPct = (r.currentPrice - r.entryPrice) / r.entryPrice;
    return pnlPct < -r.recommended.stopLossPct;
  });

  return {
    ok: true,
    totalPositions: positions.length,
    analyzedPositions: valid.length,
    alertSummary: {
      needStopLoss: needStopLoss.length,
      needStopLossSymbols: needStopLoss.map((r) => r.symbol),
    },
    positions: formatted,
    style: riskOverride.style,
    disclaimer: "以上为量化模型计算的参考建议，不构成投资建议，请结合自身判断决策。",
  };
}

function formatAdviceResult(result: {
  symbol: string;
  entryPrice: number;
  currentPrice: number;
  methods: Array<{
    method: string;
    stopLoss: number;
    takeProfit: number;
    stopLossPct: number;
    takeProfitPct: number;
    rewardRiskRatio: number;
    description: string;
    confidence: number;
  }>;
  recommended: {
    method: string;
    stopLoss: number;
    takeProfit: number;
    stopLossPct: number;
    takeProfitPct: number;
    rewardRiskRatio: number;
    description: string;
    confidence: number;
  };
  riskPreference: { maxStopLossPct: number; minRewardRiskRatio: number; style: string };
  marketStats: Record<string, number>;
}): Record<string, unknown> {
  const pnlPct = ((result.currentPrice - result.entryPrice) / result.entryPrice * 100).toFixed(2);
  const rec = result.recommended;

  return {
    symbol: result.symbol,
    entryPrice: `¥${result.entryPrice}`,
    currentPrice: `¥${result.currentPrice}`,
    unrealizedPnlPct: `${pnlPct}%`,
    recommended: {
      stopLoss: `¥${rec.stopLoss}`,
      takeProfit: `¥${rec.takeProfit}`,
      stopLossPct: `${(rec.stopLossPct * 100).toFixed(1)}%`,
      takeProfitPct: `${(rec.takeProfitPct * 100).toFixed(1)}%`,
      rewardRiskRatio: `${rec.rewardRiskRatio}:1`,
      method: rec.method,
      description: rec.description,
      confidence: `${rec.confidence}%`,
    },
    methods: result.methods.map((m) => ({
      method: m.method,
      stopLoss: `¥${m.stopLoss}`,
      takeProfit: `¥${m.takeProfit}`,
      stopLossPct: `${(m.stopLossPct * 100).toFixed(1)}%`,
      takeProfitPct: `${(m.takeProfitPct * 100).toFixed(1)}%`,
      rewardRiskRatio: `${m.rewardRiskRatio}:1`,
      description: m.description,
      confidence: `${m.confidence}%`,
    })),
    marketStats: {
      ATR14: `¥${result.marketStats.atr14}`,
      ATR占比: `${(result.marketStats.atrPct * 100).toFixed(2)}%`,
      年化波动率: `${(result.marketStats.volatility20 * 100).toFixed(1)}%`,
      近期高点: `¥${result.marketStats.recentHigh}`,
      近期低点: `¥${result.marketStats.recentLow}`,
      MA5: `¥${result.marketStats.ma5}`,
      MA20: `¥${result.marketStats.ma20}`,
      MA60: `¥${result.marketStats.ma60}`,
      布林上轨: `¥${result.marketStats.bollingerUpper}`,
      布林下轨: `¥${result.marketStats.bollingerLower}`,
    },
    riskPreference: {
      style: result.riskPreference.style === "short" ? "短线" : result.riskPreference.style === "mid" ? "中线" : "长线",
      maxStopLoss: `${(result.riskPreference.maxStopLossPct * 100).toFixed(0)}%`,
      minRewardRisk: `${result.riskPreference.minRewardRiskRatio}:1`,
    },
  };
}
