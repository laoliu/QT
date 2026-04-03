import { describe, expect, it } from "vitest";
import type { TickData } from "../types.js";
import { TickFeatureStore } from "./tick-feature-store.js";

function makeTick(
  timestamp: number,
  price: number,
  cumulativeVolume: number,
  cumulativeAmount: number,
): TickData {
  return {
    symbol: { code: "600519", name: "Kweichow Moutai", market: "cn_stock" },
    timestamp,
    price,
    volume: cumulativeVolume,
    amount: cumulativeAmount,
    bidLevels: [
      { price: price - 0.02, volume: 200 },
      { price: price - 0.03, volume: 100 },
    ],
    askLevels: [
      { price: price + 0.02, volume: 120 },
      { price: price + 0.03, volume: 80 },
    ],
    source: "qmt-bridge",
  };
}

describe("TickFeatureStore", () => {
  it("computes incremental tick features from cumulative quote snapshots", () => {
    const store = new TickFeatureStore({
      shortMomentumTicks: 2,
      mediumMomentumTicks: 3,
      vwapWindowTicks: 4,
      volatilityWindowTicks: 4,
    });

    store.recordTick(makeTick(1_000, 100, 1_000, 100_000));
    store.recordTick(makeTick(2_000, 101, 1_100, 110_100));
    const features = store.recordTick(makeTick(3_000, 102, 1_250, 125_400));

    expect(features.symbol).toBe("600519");
    expect(features.lastPrice).toBe(102);
    expect(features.volumeDelta).toBe(150);
    expect(features.amountDelta).toBe(15_300);
    expect(features.shortMomentumPct).toBeGreaterThan(0);
    expect(features.microVwap).toBeGreaterThan(100);
    expect(features.orderBookImbalance).toBeGreaterThan(0);
    expect(features.spreadBps).toBeGreaterThan(0);
  });

  it("stores the latest feature snapshot per symbol", () => {
    const store = new TickFeatureStore();
    const tick = makeTick(1_000, 100, 500, 50_000);
    const features = store.recordTick(tick);

    expect(store.getFeatures("600519")).toEqual(features);
    expect(store.getFeatures("000001")).toBeNull();
  });

  it("keeps recent feature history with cap and limit", () => {
    const store = new TickFeatureStore({ maxFeatureHistoryPerSymbol: 3 });
    store.recordTick(makeTick(1_000, 100, 1_000, 100_000));
    store.recordTick(makeTick(2_000, 101, 1_100, 110_100));
    store.recordTick(makeTick(3_000, 102, 1_200, 120_300));
    store.recordTick(makeTick(4_000, 103, 1_300, 130_600));

    const recentAll = store.getRecentFeatures("600519", 10);
    expect(recentAll).toHaveLength(3);
    expect(recentAll[0]?.timestamp).toBe(2_000);
    expect(recentAll[2]?.timestamp).toBe(4_000);

    const recentTwo = store.getRecentFeatures("600519", 2);
    expect(recentTwo.map((item) => item.timestamp)).toEqual([3_000, 4_000]);
    expect(store.getRecentFeatures("000001", 2)).toEqual([]);
  });
});
