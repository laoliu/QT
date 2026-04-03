/**
 * Historical Data Warehouse — persistent K-line cache with incremental fetch.
 *
 * Wraps an upstream `MarketDataProvider`, storing fetched klines in
 * `QuantStorage` (E1). On `getKlines()`, the warehouse:
 *   1. Checks what date ranges already exist locally
 *   2. Computes missing segments (gaps)
 *   3. Fetches only the missing segments from upstream
 *   4. Merges, deduplicates, and persists
 *   5. Returns the full requested range from local data
 *
 * Also supports:
 *   - Batch warmup (pre-fetch multiple symbols)
 *   - Automatic today-bar refresh (intraday data not marked "final")
 *   - Cache statistics and invalidation
 *   - Market calendar awareness (trading days only)
 */
import type { MarketDataProvider } from "../data/market-data-provider.js";
import type { KlineBar, KlineInterval } from "../types.js";
import type { KlineRow, QuantStorage } from "./sqlite-storage.js";

// ── Types ────────────────────────────────────────────────────

export interface WarehouseOptions {
  /** Data provider to fetch from when cache misses */
  provider: MarketDataProvider;
  /** Persistent storage backend */
  storage: QuantStorage;
  /** Whether today's bar should always be re-fetched (default: true) */
  refreshToday?: boolean;
  /** Max concurrent upstream fetches during warmup (default: 3) */
  concurrency?: number;
  /** Batch size for provider requests (max date range days, default: 365) */
  maxRangeDays?: number;
}

export interface WarehouseStats {
  totalBars: number;
  symbolCount: number;
  symbols: Array<{
    symbol: string;
    interval: string;
    bars: number;
    dateRange: { min: string; max: string } | null;
  }>;
  cacheHits: number;
  cacheMisses: number;
  upstreamFetches: number;
  hitRate: string;
}

export interface WarmupResult {
  symbol: string;
  fetched: number;
  cached: number;
  error?: string;
}

// ── Helpers ──────────────────────────────────────────────────

/** Format date as YYYY-MM-DD */
function fmtDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Parse YYYY-MM-DD to Date (UTC midnight) */
function parseDate(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(Date.UTC(y!, m! - 1, d!));
}

/** Add N calendar days */
function addDays(d: Date, n: number): Date {
  const r = new Date(d);
  r.setDate(r.getDate() + n);
  return r;
}

/** Today's date as YYYY-MM-DD */
function today(): string {
  return fmtDate(new Date());
}

/** Convert KlineBar (timestamp-based) → KlineRow (date-based) */
function barToRow(bar: KlineBar, symbol: string, interval: string): KlineRow {
  return {
    symbol,
    date: fmtDate(new Date(bar.timestamp)),
    interval,
    open: bar.open,
    high: bar.high,
    low: bar.low,
    close: bar.close,
    volume: bar.volume,
    amount: bar.amount,
  };
}

/** Convert KlineRow (date-based) → KlineBar (timestamp-based) */
function rowToBar(row: KlineRow): KlineBar {
  return {
    timestamp: parseDate(row.date).getTime(),
    open: row.open,
    high: row.high,
    low: row.low,
    close: row.close,
    volume: row.volume,
    amount: row.amount,
  };
}

/**
 * Compute date segments that are missing from a set of existing dates.
 *
 * Given a requested range [start, end] and a sorted array of existing dates,
 * returns an array of [gapStart, gapEnd] segments covering dates not yet stored.
 */
function computeGaps(
  start: string,
  end: string,
  existingDates: Set<string>,
): Array<[string, string]> {
  const gaps: Array<[string, string]> = [];
  let gapStart: string | null = null;
  let cur = parseDate(start);
  const endD = parseDate(end);

  while (cur <= endD) {
    const ds = fmtDate(cur);
    if (!existingDates.has(ds)) {
      if (!gapStart) gapStart = ds;
    } else if (gapStart) {
      // Close the gap at yesterday
      gaps.push([gapStart, fmtDate(addDays(cur, -1))]);
      gapStart = null;
    }
    cur = addDays(cur, 1);
  }
  if (gapStart) {
    gaps.push([gapStart, end]);
  }
  return gaps;
}

// ── HistoricalDataWarehouse ──────────────────────────────────

export class HistoricalDataWarehouse {
  private provider: MarketDataProvider;
  private storage: QuantStorage;
  private refreshToday: boolean;
  private concurrency: number;
  private maxRangeDays: number;

  // Stats counters
  private cacheHits = 0;
  private cacheMisses = 0;
  private upstreamFetches = 0;

  // Track symbols we know about for stats
  private knownSymbols = new Set<string>();

  constructor(opts: WarehouseOptions) {
    this.provider = opts.provider;
    this.storage = opts.storage;
    this.refreshToday = opts.refreshToday ?? true;
    this.concurrency = opts.concurrency ?? 3;
    this.maxRangeDays = opts.maxRangeDays ?? 365;
  }

  // ── Core API ───────────────────────────────────────────────

  /**
   * Get klines for a symbol, using cached data when available
   * and fetching only missing ranges from upstream.
   */
  async getKlines(
    symbol: string,
    interval: KlineInterval,
    start: string,
    end: string,
  ): Promise<KlineBar[]> {
    this.knownSymbols.add(`${symbol}:${interval}`);

    // 1. Retrieve existing cached rows in the requested range
    const cached = await this.storage.getKlines(symbol, interval, start, end);
    const existingDates = new Set(cached.map((r) => r.date));

    // 2. Determine what dates we need to fetch
    let gaps = computeGaps(start, end, existingDates);

    // 3. If refreshToday is on, always re-fetch today's bar (it may be incomplete)
    const todayStr = today();
    if (this.refreshToday && todayStr >= start && todayStr <= end) {
      existingDates.delete(todayStr);
      // Recalculate gaps including today
      gaps = computeGaps(start, end, existingDates);
    }

    // 4. If no gaps, pure cache hit
    if (gaps.length === 0) {
      this.cacheHits++;
      return cached.map(rowToBar);
    }

    // 5. Fetch missing segments
    this.cacheMisses++;
    const fetched: KlineRow[] = [];

    for (const [gapStart, gapEnd] of gaps) {
      try {
        this.upstreamFetches++;
        const bars = await this.provider.getKlines(symbol, interval, gapStart, gapEnd);
        const rows = bars.map((b) => barToRow(b, symbol, interval));
        fetched.push(...rows);
      } catch {
        // If upstream fails, we still return what we have cached
      }
    }

    // 6. Persist fetched data
    if (fetched.length > 0) {
      await this.storage.upsertKlines(fetched);
    }

    // 7. Return the full range from storage (freshest data)
    const allRows = await this.storage.getKlines(symbol, interval, start, end);
    return allRows.map(rowToBar);
  }

  /**
   * Pre-fetch kline data for multiple symbols.
   * Useful for warming the cache before market hours.
   */
  async warmup(
    symbols: string[],
    interval: KlineInterval,
    start: string,
    end: string,
  ): Promise<WarmupResult[]> {
    const results: WarmupResult[] = [];

    // Process in batches of `concurrency`
    for (let i = 0; i < symbols.length; i += this.concurrency) {
      const batch = symbols.slice(i, i + this.concurrency);
      const batchResults = await Promise.allSettled(
        batch.map(async (symbol) => {
          const countBefore = await this.storage.getKlineCount(symbol);
          const bars = await this.getKlines(symbol, interval, start, end);
          return {
            symbol,
            fetched: bars.length - countBefore,
            cached: bars.length,
          };
        }),
      );
      let batchIdx = 0;
      for (const r of batchResults) {
        if (r.status === "fulfilled") {
          results.push(r.value);
        } else {
          results.push({
            symbol: batch[batchIdx] ?? "unknown",
            fetched: 0,
            cached: 0,
            error: r.reason instanceof Error ? r.reason.message : String(r.reason),
          });
        }
        batchIdx++;
      }
    }
    return results;
  }

  /**
   * Get the stored date range for a symbol/interval,
   * returning null if no data is cached.
   */
  async getCachedRange(
    symbol: string,
    interval: KlineInterval,
  ): Promise<{ min: string; max: string } | null> {
    return this.storage.getKlineDateRange(symbol, interval);
  }

  /**
   * Invalidate cached data by deleting and re-fetching.
   * If symbol is omitted, invalidates all data.
   */
  async invalidate(symbol?: string, interval?: KlineInterval): Promise<{ cleared: number }> {
    const cleared = await this.storage.deleteKlines(symbol, interval);
    // Remove from knownSymbols tracking
    if (symbol && interval) {
      this.knownSymbols.delete(`${symbol}:${interval}`);
    } else if (symbol) {
      for (const key of [...this.knownSymbols]) {
        if (key.startsWith(symbol + ":")) this.knownSymbols.delete(key);
      }
    } else {
      this.knownSymbols.clear();
    }
    return { cleared };
  }

  /**
   * Get cache statistics.
   */
  async getStats(): Promise<WarehouseStats> {
    const totalBars = await this.storage.getKlineCount();
    const symbols: WarehouseStats["symbols"] = [];

    // Query storage directly so cached data survives gateway restarts
    const cachedPairs = await this.storage.listCachedSymbols();
    // Also include any runtime-only entries not yet persisted
    for (const key of this.knownSymbols) {
      const [s, i] = key.split(":");
      if (s && i && !cachedPairs.some((p) => p.symbol === s && p.interval === i)) {
        cachedPairs.push({ symbol: s, interval: i });
      }
    }

    for (const { symbol, interval } of cachedPairs) {
      const range = await this.storage.getKlineDateRange(symbol, interval);
      let count = 0;
      if (range) {
        const rows = await this.storage.getKlines(symbol, interval, range.min, range.max);
        count = rows.length;
      }
      symbols.push({ symbol, interval, bars: count, dateRange: range });
      // Backfill knownSymbols so invalidation tracking works
      this.knownSymbols.add(`${symbol}:${interval}`);
    }

    const totalRequests = this.cacheHits + this.cacheMisses;
    return {
      totalBars,
      symbolCount: cachedPairs.length,
      symbols,
      cacheHits: this.cacheHits,
      cacheMisses: this.cacheMisses,
      upstreamFetches: this.upstreamFetches,
      hitRate:
        totalRequests > 0 ? `${((this.cacheHits / totalRequests) * 100).toFixed(1)}%` : "N/A",
    };
  }

  /** Reset stats counters */
  resetStats(): void {
    this.cacheHits = 0;
    this.cacheMisses = 0;
    this.upstreamFetches = 0;
  }
}
