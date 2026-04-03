import { describe, expect, it } from "vitest";
import type { TickData, TickFeatures } from "../types.js";
import { TickHistoryStore } from "./tick-history-store.js";

function makeTick(symbol: string, timestamp: number, price: number): TickData {
  return {
    symbol: { code: symbol, name: symbol, market: "cn_stock" },
    timestamp,
    price,
    volume: 1_000,
    amount: price * 1_000,
    source: "qmt-bridge",
  };
}

function makeFeature(symbol: string, timestamp: number, price: number): TickFeatures {
  return {
    symbol,
    timestamp,
    lastPrice: price,
    microVwap: price,
    shortMomentumPct: 0.1,
    mediumMomentumPct: 0.2,
    spreadBps: 8,
    orderBookImbalance: 0.2,
    realizedVolBps: 3,
    volumeDelta: 100,
    amountDelta: 10_000,
  };
}

describe("TickHistoryStore", () => {
  it("stores and queries ticks/bars/features with time range", () => {
    const store = new TickHistoryStore({
      persistPath: null,
      maxTicksPerSymbol: 10,
      maxBarsPerSeries: 10,
      maxFeaturesPerSymbol: 10,
    });

    store.appendTick(makeTick("600519", 1_000, 100));
    store.appendTick(makeTick("600519", 2_000, 101));
    store.appendTick(makeTick("600519", 3_000, 102));

    store.appendBar("600519", "1m", {
      timestamp: 1_000,
      open: 100,
      high: 101,
      low: 99.8,
      close: 100.8,
      volume: 10_000,
      amount: 1_008_000,
    });
    store.appendBar("600519", "1m", {
      timestamp: 2_000,
      open: 100.8,
      high: 102,
      low: 100.5,
      close: 101.7,
      volume: 12_000,
      amount: 1_220_400,
    });

    store.appendFeature(makeFeature("600519", 1_000, 100));
    store.appendFeature(makeFeature("600519", 2_000, 101));
    store.appendFeature(makeFeature("600519", 3_000, 102));

    const ticks = store.getRecentTicks("600519", 10, { from: 1_500, to: 2_500 });
    expect(ticks).toHaveLength(1);
    expect(ticks[0]?.timestamp).toBe(2_000);

    const bars = store.getRecentBars("600519", "1m", 10, { from: 1_500 });
    expect(bars).toHaveLength(1);
    expect(bars[0]?.timestamp).toBe(2_000);

    const features = store.getRecentFeatures("600519", 10, { to: 2_500 });
    expect(features.map((row) => row.timestamp)).toEqual([1_000, 2_000]);

    const latest = store.getLatestFeatures(10);
    expect(latest).toHaveLength(1);
    expect(latest[0]?.lastPrice).toBe(102);
  });

  it("respects max retention caps", () => {
    const store = new TickHistoryStore({
      persistPath: null,
      maxTicksPerSymbol: 2,
      maxBarsPerSeries: 2,
      maxFeaturesPerSymbol: 2,
    });

    store.appendTick(makeTick("600519", 1_000, 100));
    store.appendTick(makeTick("600519", 2_000, 101));
    store.appendTick(makeTick("600519", 3_000, 102));

    store.appendBar("600519", "1m", {
      timestamp: 1_000,
      open: 100,
      high: 101,
      low: 99,
      close: 100.5,
      volume: 100,
      amount: 10_050,
    });
    store.appendBar("600519", "1m", {
      timestamp: 2_000,
      open: 100.5,
      high: 101.5,
      low: 100,
      close: 101,
      volume: 120,
      amount: 12_120,
    });
    store.appendBar("600519", "1m", {
      timestamp: 3_000,
      open: 101,
      high: 102,
      low: 100.8,
      close: 101.8,
      volume: 130,
      amount: 13_234,
    });

    store.appendFeature(makeFeature("600519", 1_000, 100));
    store.appendFeature(makeFeature("600519", 2_000, 101));
    store.appendFeature(makeFeature("600519", 3_000, 102));

    expect(store.getRecentTicks("600519", 10).map((row) => row.timestamp)).toEqual([2_000, 3_000]);
    expect(store.getRecentBars("600519", "1m", 10).map((row) => row.timestamp)).toEqual([
      2_000, 3_000,
    ]);
    expect(store.getRecentFeatures("600519", 10).map((row) => row.timestamp)).toEqual([
      2_000, 3_000,
    ]);
  });
});
