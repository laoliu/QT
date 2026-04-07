import type { KlineBar, TickAggregationInterval, TickData } from "../types.js";

export type AggregatedTickBar = {
  symbol: string;
  interval: TickAggregationInterval;
  bar: KlineBar;
};

type TickBucketState = {
  bucketStart: number;
  bar: KlineBar;
  tickCount: number;
};

const INTERVAL_MS: Record<TickAggregationInterval, number> = {
  "3s": 3_000,
  "10s": 10_000,
  "15s": 15_000,
  "30s": 30_000,
  "1m": 60_000,
  "5m": 300_000,
};

function bucketStart(timestamp: number, interval: TickAggregationInterval): number {
  const ms = INTERVAL_MS[interval];
  return Math.floor(timestamp / ms) * ms;
}

function createBar(tick: TickData, start: number): KlineBar {
  return {
    timestamp: start,
    open: tick.price,
    high: tick.price,
    low: tick.price,
    close: tick.price,
    volume: tick.volume,
    amount: tick.amount,
    openInterest: tick.openInterest,
    settlementPrice: tick.settlementPrice,
  };
}

export class TickAggregator {
  private readonly states = new Map<string, TickBucketState>();

  constructor(private readonly intervals: TickAggregationInterval[] = ["3s", "15s", "1m", "5m"]) {}

  addTick(tick: TickData): AggregatedTickBar[] {
    const completed: AggregatedTickBar[] = [];
    for (const interval of this.intervals) {
      const key = `${tick.symbol.code}:${interval}`;
      const start = bucketStart(tick.timestamp, interval);
      const current = this.states.get(key);

      if (!current) {
        this.states.set(key, { bucketStart: start, bar: createBar(tick, start), tickCount: 1 });
        continue;
      }

      if (current.bucketStart !== start) {
        completed.push({
          symbol: tick.symbol.code,
          interval,
          bar: current.bar,
        });
        this.states.set(key, { bucketStart: start, bar: createBar(tick, start), tickCount: 1 });
        continue;
      }

      current.bar.high = Math.max(current.bar.high, tick.price);
      current.bar.low = Math.min(current.bar.low, tick.price);
      current.bar.close = tick.price;
      current.bar.volume += tick.volume;
      current.bar.amount = (current.bar.amount ?? 0) + tick.amount;
      current.bar.openInterest = tick.openInterest ?? current.bar.openInterest;
      current.bar.settlementPrice = tick.settlementPrice ?? current.bar.settlementPrice;
      current.tickCount++;
    }
    return completed;
  }

  /**
   * Flush all in-progress buckets, emitting them as completed bars.
   * Useful at end-of-session or when switching symbols.
   */
  flush(symbol?: string): AggregatedTickBar[] {
    const completed: AggregatedTickBar[] = [];
    const keysToDelete: string[] = [];

    for (const [key, state] of this.states.entries()) {
      if (symbol) {
        // Only flush buckets for the given symbol
        if (!key.startsWith(`${symbol}:`)) continue;
      }
      const [sym, interval] = key.split(":") as [string, TickAggregationInterval];
      completed.push({
        symbol: sym,
        interval,
        bar: state.bar,
      });
      keysToDelete.push(key);
    }

    for (const key of keysToDelete) {
      this.states.delete(key);
    }
    return completed;
  }

  /**
   * Get the current in-progress bar for a symbol+interval (not yet completed).
   * Returns null if no data is being accumulated.
   */
  getCurrentBar(symbol: string, interval: TickAggregationInterval): KlineBar | null {
    const state = this.states.get(`${symbol}:${interval}`);
    return state?.bar ?? null;
  }

  /**
   * Get aggregation stats: number of active buckets and configured intervals.
   */
  getStats(): { activeBuckets: number; intervals: TickAggregationInterval[] } {
    return {
      activeBuckets: this.states.size,
      intervals: [...this.intervals],
    };
  }

  /** Reset all accumulated state. */
  reset(): void {
    this.states.clear();
  }
}
