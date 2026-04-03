import type { KlineBar, TickAggregationInterval, TickData } from "../types.js";

export type AggregatedTickBar = {
  symbol: string;
  interval: TickAggregationInterval;
  bar: KlineBar;
};

type TickBucketState = {
  bucketStart: number;
  bar: KlineBar;
};

const INTERVAL_MS: Record<TickAggregationInterval, number> = {
  "3s": 3_000,
  "15s": 15_000,
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
        this.states.set(key, { bucketStart: start, bar: createBar(tick, start) });
        continue;
      }

      if (current.bucketStart !== start) {
        completed.push({
          symbol: tick.symbol.code,
          interval,
          bar: current.bar,
        });
        this.states.set(key, { bucketStart: start, bar: createBar(tick, start) });
        continue;
      }

      current.bar.high = Math.max(current.bar.high, tick.price);
      current.bar.low = Math.min(current.bar.low, tick.price);
      current.bar.close = tick.price;
      current.bar.volume += tick.volume;
      current.bar.amount = (current.bar.amount ?? 0) + tick.amount;
      current.bar.openInterest = tick.openInterest ?? current.bar.openInterest;
      current.bar.settlementPrice = tick.settlementPrice ?? current.bar.settlementPrice;
    }
    return completed;
  }
}
