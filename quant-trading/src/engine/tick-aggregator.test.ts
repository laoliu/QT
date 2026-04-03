import { describe, expect, it } from "vitest";
import type { TickData } from "../types.js";
import { TickAggregator } from "./tick-aggregator.js";

function makeTick(timestamp: number, price: number, volume = 100, amount?: number): TickData {
  return {
    symbol: { code: "600519", name: "600519", market: "cn_stock" },
    timestamp,
    price,
    volume,
    amount: amount ?? price * volume,
    source: "qmt-bridge",
  };
}

describe("TickAggregator", () => {
  it("aggregates ticks into bars and emits completed buckets", () => {
    const aggregator = new TickAggregator(["3s", "1m"]);

    expect(aggregator.addTick(makeTick(0, 100, 10))).toEqual([]);
    expect(aggregator.addTick(makeTick(2_000, 102, 20))).toEqual([]);

    const completed = aggregator.addTick(makeTick(3_000, 101, 5));
    expect(completed).toHaveLength(1);
    expect(completed[0]).toMatchObject({
      symbol: "600519",
      interval: "3s",
      bar: {
        timestamp: 0,
        open: 100,
        high: 102,
        low: 100,
        close: 102,
        volume: 30,
      },
    });
  });

  it("maintains independent state per interval", () => {
    const aggregator = new TickAggregator(["15s", "1m"]);

    aggregator.addTick(makeTick(0, 100, 1));
    aggregator.addTick(makeTick(10_000, 103, 2));

    const completed = aggregator.addTick(makeTick(15_000, 101, 3));
    expect(completed).toHaveLength(1);
    expect(completed[0]?.interval).toBe("15s");
    expect(completed[0]?.bar.volume).toBe(3);
    expect(completed[0]?.bar.high).toBe(103);
  });
});
