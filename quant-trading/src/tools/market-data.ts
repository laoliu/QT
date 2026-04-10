/**
 * market_data tool — AI-callable tool for fetching market quotes and klines.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { QuantTradingConfig, TickAggregationInterval, TickData, TickFeatures } from "../types.js";

// ── Technical indicator types ────────────────────────────────────
interface IndicatorResult {
  ma5?: number | null;
  ma10?: number | null;
  ma20?: number | null;
  ma60?: number | null;
  ma120?: number | null;
  ma250?: number | null;
  ema12?: number | null;
  ema26?: number | null;
  rsi14?: number | null;
  atr14?: number | null;
  volatility?: number | null;
  high52w?: number | null;
  low52w?: number | null;
}

// ── Tick service interfaces (lazy-loaded from index.ts) ──────────

export type TickServiceAccessor = {
  getRecentTicks(symbol: string, limit?: number): TickData[];
  getLatestTick(symbol: string): TickData | null;
  getRecentBars(symbol: string, interval: TickAggregationInterval, limit?: number): import("../types.js").KlineBar[];
};

export type TickFeatureAccessor = {
  getFeatures(symbol: string): TickFeatures | null;
  getAllFeatures(): TickFeatures[];
  getRecentFeatures(symbol: string, limit?: number): TickFeatures[];
};

export type TickHistoryAccessor = {
  getRecentTicks(symbol: string, limit?: number): TickData[];
  getRecentBars(symbol: string, interval: TickAggregationInterval, limit?: number): import("../types.js").KlineBar[];
  getRecentFeatures(symbol: string, limit?: number): TickFeatures[];
};

export function createMarketDataTool(
  api: OpenClawPluginApi,
  config: QuantTradingConfig,
  tickServices?: {
    getTickService: () => TickServiceAccessor | null;
    getFeatureStore: () => TickFeatureAccessor | null;
    getHistoryStore: () => TickHistoryAccessor | null;
  },
) {
  const provider = createDataProvider(config);

  return {
    name: "market_data",
    description:
      "获取股票/期货/基金的实时行情、历史K线和基本面数据。" +
      "支持A股、美股、港股、期货、基金。\n" +
      "action=quote 实时报价,\n" +
      "action=klines 历史K线（可附带技术指标: MA/EMA/RSI/ATR/波动率/52周高低点）,\n" +
      "action=search 搜索标的,\n" +
      "action=fundamentals 基本面/估值/财务数据(PE/PB/ROE/毛利率/现金流等),\n" +
      "action=tick ʵʱTick级数据（最新逐笔成交/盘口/买卖方向/微观特征）。",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: ["quote", "klines", "search", "fundamentals", "tick"],
          description:
            "操作类型: quote=实时报价, klines=历史K线, search=搜索标的, " +
            "fundamentals=基本面/估值/财务数据, tick=Tick级实时数据(逐笔/盘口/微观特征)",
        },
        symbol: {
          type: "string",
          description: "标的代码，如 600519 (ę́), AAPL, BTC-USDT",
        },
        keyword: {
          type: "string",
          description: "搜索关键词（仅 action=search 时使用）",
        },
        interval: {
          type: "string",
          enum: ["1m", "5m", "15m", "30m", "60m", "1d", "1w", "1M"],
          description: "K线周期（仅 action=klines 时使用），默认 1d",
        },
        startDate: {
          type: "string",
          description: "开始日期 YYYY-MM-DD（仅 action=klines）",
        },
        endDate: {
          type: "string",
          description: "结束日期 YYYY-MM-DD（仅 action=klines）",
        },
        limit: {
          type: "number",
          description:
            "返回条数。action=klines 默认120，action=tick 默认50。设为0返回全部。" +
            "技术分析建议至少120条（含MA120），长期分析建议250条。",
        },
        indicators: {
          type: "boolean",
          description:
            "是否附加技术指标（仅 action=klines），默认 false。" +
            "开启后返回 MA5/10/20/60/120/250, EMA12/26, RSI14, ATR14, 波动率, 52周高低。",
        },
        market: {
          type: "string",
          enum: ["cn_stock", "us_stock", "hk_stock", "futures", "fund", "crypto"],
          description: "市场类型，默认使用配置的 defaultMarket",
        },
        tickMode: {
          type: "string",
          enum: ["latest", "recent", "features", "bars"],
          description:
            "Tick数据模式（仅 action=tick 时使用）。" +
            "latest=最新一笔Tick, recent=最近N笔Tick, " +
            "features=微观特征(VWAP/动量/盘口失衡/波动率), bars=Tick聚合K线。默认 recent",
        },
        tickInterval: {
          type: "string",
          enum: ["3s", "10s", "15s", "30s", "1m", "5m"],
          description: "Tick聚合周期（仅 action=tick tickMode=bars 时使用），默认 1m",
        },
      },
      required: ["action"],
    },
    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const symbol = args.symbol as string;
      // Auto-detect market from symbol when not explicitly given
      const market =
        (args.market as string) ?? (symbol ? detectMarketFromSymbol(symbol) : config.defaultMarket);

      switch (action) {
        case "quote": {
          if (!symbol) return { error: "请提供标的代码 (symbol)" };
          const quote = await provider.getQuote(symbol, market as never);
          if (!quote) return { error: `无法获取 ${symbol} 的行情数据` };
          return {
            symbol: quote.symbol,
            price: quote.price.toFixed(2),
            change: `${quote.change >= 0 ? "+" : ""}${quote.change.toFixed(2)}`,
            changePct: `${quote.changePct >= 0 ? "+" : ""}${quote.changePct.toFixed(2)}%`,
            open: quote.open.toFixed(2),
            high: quote.high.toFixed(2),
            low: quote.low.toFixed(2),
            volume: quote.volume,
            amount: quote.amount,
            marketCap: (quote as Record<string, unknown>).marketCap ?? undefined,
          };
        }

        case "klines": {
          if (!symbol) return { error: "请提供标的代码 (symbol)" };
          const interval = (args.interval as string) ?? "1d";
          const startDate = (args.startDate as string) ?? getDefaultStartDate();
          const endDate = (args.endDate as string) ?? new Date().toISOString().slice(0, 10);
          const limit = (args.limit as number) ?? 120;
          const wantIndicators = (args.indicators as boolean) ?? false;

          const bars = await provider.getKlines(
            symbol,
            interval as never,
            startDate,
            endDate,
            market as never,
          );

          // Slice to requested limit (0 = all)
          const sliced = limit > 0 ? bars.slice(-limit) : bars;

          const data = sliced.map((b) => ({
            date: new Date(b.timestamp).toISOString().slice(0, 10),
            open: b.open.toFixed(2),
            high: b.high.toFixed(2),
            low: b.low.toFixed(2),
            close: b.close.toFixed(2),
            volume: b.volume,
          }));

          const result: Record<string, unknown> = {
            symbol,
            interval,
            count: data.length,
            data,
          };

          // Compute technical indicators via Python if requested
          if (wantIndicators && bars.length > 0) {
            const indicators = await computeIndicators(bars, config);
            if (indicators) {
              result.indicators = indicators;
            }
          }

          return result;
        }

        case "search": {
          const keyword = (args.keyword as string) ?? symbol;
          if (!keyword) return { error: "请提供搜索关键词 (keyword)" };
          const results = await provider.searchSymbols(keyword, market as never);
          return { results };
        }

        case "fundamentals": {
          if (!symbol) return { error: "请提供标的代码 (symbol)" };
          const { runPythonScript: runPy } = await import("../bridge/python-bridge.js");
          // A-share → financial_provider (akshare/Sina, richer data + SQLite cache)
          // Other markets → yfinance_provider (Yahoo Finance global)
          if (market === "cn_stock") {
            const plainCode = symbol.replace(/\.(SH|SS|SZ)$/i, "");
            const result = await runPy(
              "data_providers.financial_provider",
              { action: "fetch", codes: [plainCode], market: "cn_stock" },
              config,
            );
            if (!result.ok) return { error: result.error ?? "获取基本面数据失败" };
            const data = result.data as { results?: Record<string, unknown> };
            const snap = data?.results?.[plainCode];
            if (!snap) return { error: `未找到 ${plainCode} 的基本面数据` };
            return snap;
          }
          const result = await runPy(
            "data_providers.yfinance_provider",
            { action: "fundamentals", symbol, market },
            config,
          );
          if (!result.ok) return { error: result.error ?? "获取基本面数据失败" };
          return result.data;
        }

        case "tick": {
          if (!symbol) return { error: "请提供标的代码 (symbol)" };
          return handleTickAction(symbol, args, tickServices);
        }

        default:
          return { error: `未知操作: ${action}` };
      }
    },
  };
}

// ── Tick action handler ──────────────────────────────────────────

function handleTickAction(
  symbol: string,
  args: Record<string, unknown>,
  tickServices?: {
    getTickService: () => TickServiceAccessor | null;
    getFeatureStore: () => TickFeatureAccessor | null;
    getHistoryStore: () => TickHistoryAccessor | null;
  },
): Record<string, unknown> {
  const tickMode = (args.tickMode as string) ?? "recent";
  const limit = (args.limit as number) ?? 50;

  // Try live tick service first, fall back to history store
  const tickService = tickServices?.getTickService();
  const featureStore = tickServices?.getFeatureStore();
  const historyStore = tickServices?.getHistoryStore();

  if (!tickService && !historyStore) {
    return {
      error:
        "Tick数据服务未启用。请在配置中设置 tickData.enabled=true 并配置数据源 " +
        "(tradingAccount.qmt.windowsBridgeUrl 或 receiver)。",
      hint: "需要连接QMT数据桥或RECEIVER代理才能获取Tick级数据。",
    };
  }

  switch (tickMode) {
    case "latest": {
      const tick = tickService?.getLatestTick(symbol);
      if (!tick) {
        return {
          error: `无 ${symbol} 的Tick数据。请确认已订阅该标的 (可能需要先通过 live_trading 启动并添加监控)。`,
          symbol,
        };
      }
      return formatTickResult(tick);
    }

    case "recent": {
      const source = tickService ?? historyStore;
      if (!source) return { error: "Tick数据服务不可用" };
      const ticks = source.getRecentTicks(symbol, limit > 0 ? limit : 200);
      if (ticks.length === 0) {
        return {
          error: `无 ${symbol} 的Tick历史。请确认已订阅该标的。`,
          symbol,
        };
      }
      return {
        symbol,
        mode: "recent",
        count: ticks.length,
        data: ticks.map(formatTickResult),
      };
    }

    case "features": {
      if (!featureStore) {
        return { error: "Tick特征计算服务未启用" };
      }
      const latest = featureStore.getFeatures(symbol);
      if (!latest) {
        // Try history store for recent features
        if (historyStore) {
          const recentFeatures = historyStore.getRecentFeatures(symbol, limit > 0 ? limit : 20);
          if (recentFeatures.length > 0) {
            return {
              symbol,
              mode: "features",
              count: recentFeatures.length,
              data: recentFeatures.map(formatFeatureResult),
            };
          }
        }
        return {
          error: `无 ${symbol} 的Tick特征数据。请确认已订阅该标的。`,
          symbol,
        };
      }
      // Return latest + recent history if available
      const recentFeatures = featureStore.getRecentFeatures(symbol, limit > 0 ? limit : 20);
      return {
        symbol,
        mode: "features",
        latest: formatFeatureResult(latest),
        count: recentFeatures.length,
        history: recentFeatures.length > 1 ? recentFeatures.map(formatFeatureResult) : undefined,
      };
    }

    case "bars": {
      const tickInterval = (args.tickInterval as TickAggregationInterval) ?? "1m";
      const source = tickService ?? historyStore;
      if (!source) return { error: "Tick数据服务不可用" };
      const bars = source.getRecentBars(symbol, tickInterval, limit > 0 ? limit : 100);
      if (bars.length === 0) {
        return {
          error: `无 ${symbol} 的 ${tickInterval} Tick聚合K线。请确认已订阅该标的。`,
          symbol,
          interval: tickInterval,
        };
      }
      return {
        symbol,
        mode: "bars",
        interval: tickInterval,
        count: bars.length,
        data: bars.map((b) => ({
          time: new Date(b.timestamp).toISOString(),
          open: b.open.toFixed(4),
          high: b.high.toFixed(4),
          low: b.low.toFixed(4),
          close: b.close.toFixed(4),
          volume: b.volume,
          amount: b.amount,
        })),
      };
    }

    default:
      return { error: `未知的Tick模式: ${tickMode}。支持: latest, recent, features, bars` };
  }
}

function formatTickResult(tick: TickData): Record<string, unknown> {
  const result: Record<string, unknown> = {
    symbol: tick.symbol.code,
    time: new Date(tick.timestamp).toISOString(),
    price: tick.price,
    volume: tick.volume,
    amount: tick.amount,
  };

  if (tick.direction) result.direction = tick.direction;
  if (tick.tradeCount != null) result.tradeCount = tick.tradeCount;
  if (tick.open) result.open = tick.open;
  if (tick.high) result.high = tick.high;
  if (tick.low) result.low = tick.low;
  if (tick.prevClose) result.prevClose = tick.prevClose;
  if (tick.openInterest != null) result.openInterest = tick.openInterest;

  // Bid/Ask depth
  if (tick.bidLevels && tick.bidLevels.length > 0) {
    result.bid = tick.bidLevels.map((l) => ({ price: l.price, volume: l.volume }));
  }
  if (tick.askLevels && tick.askLevels.length > 0) {
    result.ask = tick.askLevels.map((l) => ({ price: l.price, volume: l.volume }));
  }

  // Spread
  const bid1 = tick.bidLevels?.[0]?.price;
  const ask1 = tick.askLevels?.[0]?.price;
  if (bid1 && ask1 && bid1 > 0 && ask1 > 0) {
    const mid = (bid1 + ask1) / 2;
    result.spread = (ask1 - bid1).toFixed(4);
    result.spreadBps = mid > 0 ? (((ask1 - bid1) / mid) * 10_000).toFixed(1) : undefined;
  }

  result.source = tick.source;
  return result;
}

function formatFeatureResult(features: TickFeatures): Record<string, unknown> {
  return {
    symbol: features.symbol,
    time: new Date(features.timestamp).toISOString(),
    lastPrice: features.lastPrice,
    microVwap: features.microVwap.toFixed(4),
    shortMomentumPct: `${features.shortMomentumPct.toFixed(4)}%`,
    mediumMomentumPct: `${features.mediumMomentumPct.toFixed(4)}%`,
    spreadBps: features.spreadBps != null ? features.spreadBps.toFixed(1) : undefined,
    orderBookImbalance: features.orderBookImbalance != null ? features.orderBookImbalance.toFixed(4) : undefined,
    realizedVolBps: features.realizedVolBps != null ? features.realizedVolBps.toFixed(1) : undefined,
    volumeDelta: features.volumeDelta,
    amountDelta: features.amountDelta,
  };
}

function getDefaultStartDate(): string {
  const d = new Date();
  // Default to 18 months ago so MA250 + indicators have enough data
  d.setMonth(d.getMonth() - 18);
  return d.toISOString().slice(0, 10);
}

/**
 * Compute technical indicators from bar data using the Python indicators engine.
 * Falls back to inline TS computation if Python is unavailable.
 */
async function computeIndicators(
  bars: Array<{
    timestamp: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>,
  config: QuantTradingConfig,
): Promise<IndicatorResult | null> {
  // Convert to the dict format expected by Python indicators
  const pyBars = bars.map((b) => ({
    datetime: new Date(b.timestamp).toISOString().slice(0, 10).replace(/-/g, ""),
    open: b.open,
    high: b.high,
    low: b.low,
    close: b.close,
    volume: b.volume,
  }));

  const pyResult = await runPythonScript<IndicatorResult>(
    "engines/compute_indicators.py",
    { bars: pyBars },
    config,
  );

  if (pyResult.ok && pyResult.data) {
    return pyResult.data;
  }

  // Fallback: compute basic indicators inline (TypeScript)
  return computeIndicatorsInline(bars);
}

/** Inline TS fallback for basic technical indicators. */
function computeIndicatorsInline(
  bars: Array<{ timestamp: number; high: number; low: number; close: number }>,
): IndicatorResult {
  const closes = bars.map((b) => b.close);
  const n = closes.length;

  const sma = (period: number): number | null => {
    if (n < period) return null;
    const slice = closes.slice(-period);
    return slice.reduce((a, b) => a + b, 0) / period;
  };

  const ema = (period: number): number | null => {
    if (n < period) return null;
    const k = 2 / (period + 1);
    let e = closes.slice(0, period).reduce((a, b) => a + b, 0) / period;
    for (let i = period; i < n; i++) {
      e = closes[i] * k + e * (1 - k);
    }
    return e;
  };

  // RSI
  let rsi14: number | null = null;
  if (n >= 15) {
    const period = 14;
    const recent = closes.slice(-(period + 1));
    let gains = 0;
    let losses = 0;
    for (let i = 1; i < recent.length; i++) {
      const d = recent[i] - recent[i - 1];
      if (d > 0) gains += d;
      else losses -= d;
    }
    const avgGain = gains / period;
    const avgLoss = losses / period;
    rsi14 = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }

  // ATR14
  let atr14: number | null = null;
  if (n >= 15) {
    let trSum = 0;
    for (let i = n - 14; i < n; i++) {
      const h = bars[i].high;
      const l = bars[i].low;
      const pc = bars[i - 1].close;
      trSum += Math.max(h - l, Math.abs(h - pc), Math.abs(pc - l));
    }
    atr14 = trSum / 14;
  }

  // 52-week high/low (last 250 bars)
  const lookback = Math.min(n, 250);
  const recentBars = bars.slice(-lookback);
  const high52w = Math.max(...recentBars.map((b) => b.high));
  const low52w = Math.min(...recentBars.map((b) => b.low));

  // Historical volatility (annualized)
  let volatility: number | null = null;
  if (n >= 20) {
    const logReturns: number[] = [];
    for (let i = 1; i < n; i++) {
      if (closes[i] > 0 && closes[i - 1] > 0) {
        logReturns.push(Math.log(closes[i] / closes[i - 1]));
      }
    }
    if (logReturns.length >= 2) {
      const mean = logReturns.reduce((a, b) => a + b, 0) / logReturns.length;
      const variance = logReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) / logReturns.length;
      volatility = Math.sqrt(variance) * Math.sqrt(252); // annualized
    }
  }

  return {
    ma5: sma(5),
    ma10: sma(10),
    ma20: sma(20),
    ma60: sma(60),
    ma120: sma(120),
    ma250: sma(250),
    ema12: ema(12),
    ema26: ema(26),
    rsi14: rsi14 != null ? Math.round(rsi14 * 100) / 100 : null,
    atr14: atr14 != null ? Math.round(atr14 * 100) / 100 : null,
    volatility: volatility != null ? Math.round(volatility * 10000) / 100 : null, // as percentage
    high52w,
    low52w,
  };
}
