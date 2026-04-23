import { writeFile, readFile } from "fs";
import { join } from "path";
import { promisify } from "util";
import type { OpenClawPluginService } from "openclaw/plugin-sdk/core";
import { TickAggregator } from "../engine/tick-aggregator.js";
import { TickFeatureStore } from "../engine/tick-feature-store.js";
import type {
  QuantTradingConfig,
  QuoteSnapshot,
  TickAggregationInterval,
  TickData,
  TickDepthLevel,
  TickDirection,
  TickFeatures,
} from "../types.js";
import type { KlineBar } from "../types.js";
import type { EventBus } from "./event-bus.js";
import { createDefaultTickSourceAdapter, type TickSourceAdapter } from "./tick-source-adapter.js";
import { nowCSTISO } from "../utils/cst-time.js";

type TickHandler = (tick: TickData) => void;
type QuoteHandler = (quote: QuoteSnapshot) => void;
type TickFeatureHandler = (features: TickFeatures) => void;
type BarHandler = (symbol: string, bar: KlineBar, interval: TickAggregationInterval) => void;

export interface TickIngressStatus {
  running: boolean;
  source: string;
  sourceEndpoint?: string;
  subscribedSymbols: string[];
  lastEventAt: string | null;
  pollIntervalMs: number;
  failedSubscribeSymbols?: string[];
  consecutiveErrors?: number;
  lastErrorAt?: string | null;
  lastError?: string | null;
}

function normalizeDepthLevels(prices: unknown, volumes: unknown): TickDepthLevel[] | undefined {
  if (!Array.isArray(prices)) return undefined;
  const vols = Array.isArray(volumes) ? volumes : [];
  const levels: TickDepthLevel[] = [];
  for (let index = 0; index < prices.length; index++) {
    const price = prices[index];
    if (typeof price !== "number" || !Number.isFinite(price) || price <= 0) continue;
    const volume =
      typeof vols[index] === "number" && Number.isFinite(vols[index]) ? vols[index] : 0;
    levels.push({ price, volume });
  }
  return levels.length > 0 ? levels : undefined;
}

function normalizeQuote(
  raw: Record<string, unknown>,
  toInternalSymbol: (symbol: string) => string,
): QuoteSnapshot | null {
  const sym = toInternalSymbol(String(raw.stock_code ?? ""));
  const price = (raw.last_price as number) ?? (raw.price as number) ?? 0;
  if (!sym || price <= 0) return null;

  const prevClose = (raw.last_close as number) ?? (raw.prev_close as number) ?? 0;
  const change = price - prevClose;
  return {
    symbol: { code: sym, name: sym, market: "cn_stock" },
    price,
    change,
    changePct: prevClose > 0 ? (change / prevClose) * 100 : 0,
    open: (raw.open as number) ?? 0,
    high: (raw.high as number) ?? price,
    low: (raw.low as number) ?? price,
    prevClose,
    volume: (raw.volume as number) ?? 0,
    amount: (raw.amount as number) ?? 0,
    timestamp: (raw.time as number) ?? (raw.timestamp as number) ?? Date.now(),
    bid1: Array.isArray(raw.bid_price) ? (raw.bid_price[0] as number | undefined) : undefined,
    ask1: Array.isArray(raw.ask_price) ? (raw.ask_price[0] as number | undefined) : undefined,
    openInterest: (raw.open_interest as number) ?? undefined,
    settlementPrice: (raw.settlement_price as number) ?? undefined,
  };
}

function inferTickDirection(raw: Record<string, unknown>, quote: QuoteSnapshot): TickDirection {
  // 1. Explicit direction from data source
  const explicit = raw.direction ?? raw.bs_flag ?? raw.side;
  if (explicit != null) {
    const d = String(explicit).toLowerCase().trim();
    if (d === "buy" || d === "b" || d === "1" || d === "bid") return "buy";
    if (d === "sell" || d === "s" || d === "-1" || d === "2" || d === "ask" || d === "offer") return "sell";
    return "neutral";
  }
  // 2. Infer from price vs bid/ask
  if (quote.bid1 && quote.ask1 && quote.bid1 > 0 && quote.ask1 > 0) {
    if (quote.price >= quote.ask1) return "buy";
    if (quote.price <= quote.bid1) return "sell";
  }
  return "neutral";
}

function normalizeTick(raw: Record<string, unknown>, quote: QuoteSnapshot): TickData {
  return {
    symbol: quote.symbol,
    timestamp: quote.timestamp,
    price: quote.price,
    volume: quote.volume,
    amount: quote.amount,
    open: quote.open,
    high: quote.high,
    low: quote.low,
    prevClose: quote.prevClose,
    openInterest: quote.openInterest,
    settlementPrice: quote.settlementPrice,
    bidLevels: normalizeDepthLevels(raw.bid_price, raw.bid_volume),
    askLevels: normalizeDepthLevels(raw.ask_price, raw.ask_volume),
    direction: inferTickDirection(raw, quote),
    tradeCount: typeof raw.trade_count === "number" ? raw.trade_count :
                typeof raw.num_trades === "number" ? raw.num_trades : undefined,
    source: "unknown",
    raw,
  };
}

export class TickIngressService implements OpenClawPluginService {
  readonly id = "qmt-tick-ingress";

  private readonly pollIntervalMs: number;
  private readonly maxRecentTicksPerSymbol: number;
  private readonly maxRecentBarsPerSeries: number;
  private readonly sourceAdapter: TickSourceAdapter | null;
  private readonly tickHandlers = new Set<TickHandler>();
  private readonly quoteHandlers = new Set<QuoteHandler>();
  private readonly tickFeatureHandlers = new Set<TickFeatureHandler>();
  private readonly barHandlers = new Set<BarHandler>();
  private readonly subscribedSymbols = new Set<string>();
  private readonly recentTicks = new Map<string, TickData[]>();
  private readonly recentBars = new Map<string, KlineBar[]>();
  private readonly aggregator: TickAggregator;
  private readonly featureStore: TickFeatureStore;
  private running = false;
  private lastEventAt: number | null = null;
  private seqSaveTimer: ReturnType<typeof setInterval> | null = null;
  private retryTimer: ReturnType<typeof setInterval> | null = null;
  private readonly failedSubscribeSymbols = new Set<string>();
  private consecutiveErrors = 0;
  private lastErrorAt: number | null = null;
  private lastError: string | null = null;

  constructor(
    private readonly config: QuantTradingConfig,
    private readonly eventBus?: EventBus,
    sourceAdapter?: TickSourceAdapter | null,
  ) {
    this.pollIntervalMs = config.tickData?.pollIntervalMs ?? 3_000;
    this.maxRecentTicksPerSymbol = config.tickData?.maxRecentTicksPerSymbol ?? 256;
    this.maxRecentBarsPerSeries = config.tickData?.maxRecentBarsPerSeries ?? 256;
    this.sourceAdapter =
      sourceAdapter ?? createDefaultTickSourceAdapter(config, this.pollIntervalMs);
    this.aggregator = new TickAggregator(
      config.tickData?.aggregateIntervals ??
        (["3s", "15s", "1m", "5m"] satisfies TickAggregationInterval[]),
    );
    this.featureStore = new TickFeatureStore({
      maxTicksPerSymbol: this.maxRecentTicksPerSymbol,
    });
  }

  async start(): Promise<void> {
    if (this.running || !this.sourceAdapter || this.config.tickData?.enabled === false) return;
    this.sourceAdapter.start((event) => this.handleEvent(event.event, event.data));
    this.running = true;

    // Start periodic seq persistence for receiver-proxy
    if (this.sourceAdapter.kind === "receiver-proxy" && this.config.dataDir) {
      const adapter = this.sourceAdapter;
      this.seqSaveTimer = setInterval(() => {
        const seq = (adapter as unknown as { getSeq(): number }).getSeq();
        const seqFile = join(this.config.dataDir!, "tick-seq.json");
        void promisify(writeFile)(
          seqFile,
          JSON.stringify({ afterSeq: seq, updatedAt: nowCSTISO() }, null, 2),
        ).catch(() => {
          // Fail silently; seq persistence is best-effort
        });
      }, 10_000);
    }

    // Start background retry for failed subscriptions
    this.retryTimer = setInterval(() => {
      const failed = Array.from(this.failedSubscribeSymbols);
      if (failed.length > 0) {
        void this.retryFailedSubscriptions(failed);
      }
    }, 30_000);
  }

  async stop(): Promise<void> {
    if (this.seqSaveTimer) {
      clearInterval(this.seqSaveTimer);
      this.seqSaveTimer = null;
    }
    if (this.retryTimer) {
      clearInterval(this.retryTimer);
      this.retryTimer = null;
    }
    this.sourceAdapter?.stop();
    this.running = false;
  }

  private async retryFailedSubscriptions(symbols: string[]): Promise<void> {
    if (!this.sourceAdapter || symbols.length === 0) return;
    try {
      await this.sourceAdapter.subscribe(symbols);
      // Success: move from failed to subscribed
      for (const symbol of symbols) {
        this.failedSubscribeSymbols.delete(symbol);
        this.subscribedSymbols.add(symbol);
      }
      this.consecutiveErrors = 0;
    } catch (err) {
      this.consecutiveErrors++;
      this.lastErrorAt = Date.now();
      this.lastError = err instanceof Error ? err.message : String(err);
    }
  }

  async subscribe(symbols: string[]): Promise<void> {
    const adapter = this.sourceAdapter;
    if (!adapter) return;
    const next = symbols
      .map((symbol) => adapter.toInternalSymbol(symbol))
      .filter((symbol) => symbol.length > 0)
      .filter((symbol) => !this.subscribedSymbols.has(symbol));
    if (next.length === 0) return;
    try {
      await adapter.subscribe(next);
      for (const symbol of next) {
        this.subscribedSymbols.add(symbol);
        this.failedSubscribeSymbols.delete(symbol);
      }
      this.consecutiveErrors = 0;
    } catch (err) {
      // Add to retry queue but still propagate the error
      for (const symbol of next) this.failedSubscribeSymbols.add(symbol);
      this.consecutiveErrors++;
      this.lastErrorAt = Date.now();
      this.lastError = err instanceof Error ? err.message : String(err);
      throw err;
    }
  }

  async unsubscribe(symbols: string[]): Promise<void> {
    const adapter = this.sourceAdapter;
    if (!adapter) return;
    const next = symbols
      .map((symbol) => adapter.toInternalSymbol(symbol))
      .filter((symbol) => this.subscribedSymbols.has(symbol));
    if (next.length === 0) return;
    await adapter.unsubscribe(next);
    for (const symbol of next) this.subscribedSymbols.delete(symbol);
  }

  async syncSubscriptions(symbols: string[]): Promise<void> {
    const normalized = new Set(
      symbols
        .map((symbol) => this.sourceAdapter?.toInternalSymbol(symbol) ?? symbol)
        .filter(Boolean),
    );
    const toSubscribe = [...normalized].filter((symbol) => !this.subscribedSymbols.has(symbol));
    const toUnsubscribe = [...this.subscribedSymbols].filter((symbol) => !normalized.has(symbol));

    if (toSubscribe.length > 0) {
      await this.subscribe(toSubscribe);
    }
    if (toUnsubscribe.length > 0) {
      await this.unsubscribe(toUnsubscribe);
    }
  }

  onTick(handler: TickHandler): () => void {
    this.tickHandlers.add(handler);
    return () => this.tickHandlers.delete(handler);
  }

  onQuote(handler: QuoteHandler): () => void {
    this.quoteHandlers.add(handler);
    return () => this.quoteHandlers.delete(handler);
  }

  onTickFeatures(handler: TickFeatureHandler): () => void {
    this.tickFeatureHandlers.add(handler);
    return () => this.tickFeatureHandlers.delete(handler);
  }

  onBar(handler: BarHandler): () => void {
    this.barHandlers.add(handler);
    return () => this.barHandlers.delete(handler);
  }

  publishTickFeatures(features: TickFeatures): void {
    for (const handler of this.tickFeatureHandlers) handler(features);
    this.eventBus?.emit("tick", { type: "feature", features });
  }

  /** Expose the internal feature store for use by admin routes / history store wiring. */
  getFeatureStore(): TickFeatureStore {
    return this.featureStore;
  }

  getStatus(): TickIngressStatus {
    return {
      running: this.running,
      source: this.sourceAdapter?.kind ?? "none",
      sourceEndpoint: this.sourceAdapter?.endpoint,
      subscribedSymbols: [...this.subscribedSymbols].sort(),
      lastEventAt: this.lastEventAt ? new Date(this.lastEventAt).toISOString() : null,
      pollIntervalMs: this.pollIntervalMs,
      failedSubscribeSymbols:
        this.failedSubscribeSymbols.size > 0 ? [...this.failedSubscribeSymbols].sort() : undefined,
      consecutiveErrors: this.consecutiveErrors > 0 ? this.consecutiveErrors : undefined,
      lastErrorAt: this.lastErrorAt ? new Date(this.lastErrorAt).toISOString() : null,
      lastError: this.lastError,
    };
  }

  getLatestTick(symbol: string): TickData | null {
    const ticks = this.recentTicks.get(symbol);
    return ticks && ticks.length > 0 ? ticks[ticks.length - 1]! : null;
  }

  getRecentTicks(symbol: string, limit = 100): TickData[] {
    const ticks = this.recentTicks.get(symbol);
    if (!ticks || ticks.length === 0) return [];
    const n = Math.max(1, Math.floor(limit));
    return ticks.slice(-n);
  }

  getRecentBars(symbol: string, interval: TickAggregationInterval, limit = 100): KlineBar[] {
    const key = `${symbol}:${interval}`;
    const bars = this.recentBars.get(key);
    if (!bars || bars.length === 0) return [];
    const n = Math.max(1, Math.floor(limit));
    return bars.slice(-n);
  }

  private handleEvent(event: string, data: unknown): void {
    if (event !== "quote" || !data || typeof data !== "object" || Array.isArray(data)) return;
    if (!this.sourceAdapter) return;

    const raw = data as Record<string, unknown>;
    const quote = normalizeQuote(raw, this.sourceAdapter.toInternalSymbol.bind(this.sourceAdapter));
    if (!quote) return;

    const tick = normalizeTick(raw, quote);
    if (this.sourceAdapter.kind === "qmt-bridge") tick.source = "qmt-bridge";
    else if (this.sourceAdapter.kind === "receiver-proxy") tick.source = "receiver";
    this.lastEventAt = Math.max(this.lastEventAt ?? 0, tick.timestamp);
    this.pushRecentTick(tick);

    for (const handler of this.quoteHandlers) handler(quote);
    for (const handler of this.tickHandlers) handler(tick);

    this.eventBus?.emit("tick", { type: "tick", tick });
    const completedBars = this.aggregator.addTick(tick);
    for (const aggregated of completedBars) {
      this.pushRecentBar(aggregated.symbol, aggregated.interval, aggregated.bar);
      this.eventBus?.emit("tick", { type: "bar", ...aggregated });
      for (const handler of this.barHandlers)
        handler(aggregated.symbol, aggregated.bar, aggregated.interval);
    }

    // Compute tick features and broadcast to strategy handlers
    const features = this.featureStore.recordTick(tick);
    this.publishTickFeatures(features);
  }

  private pushRecentTick(tick: TickData): void {
    const existing = this.recentTicks.get(tick.symbol.code) ?? [];
    existing.push(tick);
    if (existing.length > this.maxRecentTicksPerSymbol) {
      existing.splice(0, existing.length - this.maxRecentTicksPerSymbol);
    }
    this.recentTicks.set(tick.symbol.code, existing);
  }

  private pushRecentBar(symbol: string, interval: TickAggregationInterval, bar: KlineBar): void {
    const key = `${symbol}:${interval}`;
    const existing = this.recentBars.get(key) ?? [];
    existing.push(bar);
    if (existing.length > this.maxRecentBarsPerSeries) {
      existing.splice(0, existing.length - this.maxRecentBarsPerSeries);
    }
    this.recentBars.set(key, existing);
  }
}
