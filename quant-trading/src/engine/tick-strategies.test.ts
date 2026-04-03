import { describe, expect, it } from "vitest";
import type { TickData, TickFeatures } from "../types.js";
import type { KlineBar } from "../types.js";
import {
  createTickMomentumImbalanceStrategy,
  createTickVwapReversionStrategy,
  createTickMomentum1mConfirmStrategy,
} from "./tick-strategies.js";

function makeTick(price: number, timestamp: number): TickData {
  return {
    symbol: { code: "600519", name: "600519", market: "cn_stock" },
    timestamp,
    price,
    volume: 1_000,
    amount: price * 1_000,
    source: "qmt-bridge",
  };
}

function makeFeatures(overrides: Partial<TickFeatures>): TickFeatures {
  return {
    symbol: "600519",
    timestamp: 1_000,
    lastPrice: 100,
    microVwap: 99.8,
    shortMomentumPct: 0.1,
    mediumMomentumPct: 0.15,
    spreadBps: 8,
    orderBookImbalance: 0.2,
    realizedVolBps: 4,
    volumeDelta: 100,
    amountDelta: 10_000,
    ...overrides,
  };
}

describe("tick-driven strategies", () => {
  it("momentum imbalance strategy enters and exits on momentum decay", () => {
    const strategy = createTickMomentumImbalanceStrategy({ symbol: "600519" });
    const buy = strategy.onTick({
      tick: makeTick(100, 1_000),
      features: makeFeatures({ timestamp: 1_000 }),
    });
    expect(buy?.direction).toBe("buy");

    const sell = strategy.onTick({
      tick: makeTick(100.1, 2_000),
      features: makeFeatures({
        timestamp: 2_000,
        lastPrice: 100.1,
        shortMomentumPct: -0.05,
        mediumMomentumPct: 0.01,
        orderBookImbalance: -0.12,
      }),
    });
    expect(sell?.direction).toBe("sell");
  });

  it("vwap reversion strategy buys below VWAP and exits on reversion", () => {
    const strategy = createTickVwapReversionStrategy({ symbol: "600519" });
    const buy = strategy.onTick({
      tick: makeTick(99.7, 1_000),
      features: makeFeatures({
        timestamp: 1_000,
        lastPrice: 99.7,
        microVwap: 100,
        shortMomentumPct: -0.01,
        mediumMomentumPct: -0.02,
        orderBookImbalance: 0,
      }),
    });
    expect(buy?.direction).toBe("buy");

    const sell = strategy.onTick({
      tick: makeTick(99.98, 2_000),
      features: makeFeatures({
        timestamp: 2_000,
        lastPrice: 99.98,
        microVwap: 100,
        shortMomentumPct: 0.02,
        mediumMomentumPct: 0.03,
        orderBookImbalance: 0.05,
      }),
    });
    expect(sell?.direction).toBe("sell");
  });
});

function makeBar(open: number, close: number, timestamp = 1_000): KlineBar {
  return {
    timestamp,
    open,
    high: Math.max(open, close),
    low: Math.min(open, close),
    close,
    volume: 1000,
  };
}

describe("tick-momentum-1m-confirm strategy", () => {
  it("does not buy until a bullish 1m bar is provided", () => {
    const strategy = createTickMomentum1mConfirmStrategy({ symbol: "600519" });
    const tick = makeTick(101, 1_000);
    const features = makeFeatures({
      lastPrice: 101,
      microVwap: 100.5,
      shortMomentumPct: 0.1,
      orderBookImbalance: 0.2,
      spreadBps: 8,
    });

    // No bar yet — should not buy
    expect(strategy.onTick({ tick, features })).toBeNull();

    // Feed a bearish bar — still should not buy
    strategy.onBar?.(makeBar(101, 100), "1m");
    expect(strategy.onTick({ tick, features })).toBeNull();

    // Feed a bullish bar — now tick conditions met → buy
    strategy.onBar?.(makeBar(100, 101.5), "1m");
    const buy = strategy.onTick({ tick, features });
    expect(buy?.direction).toBe("buy");
    expect(buy?.strategyId).toBe("tick-momentum-1m-confirm");
  });

  it("exits on stop-loss even without new bar input", () => {
    const strategy = createTickMomentum1mConfirmStrategy({ symbol: "600519" });

    // Seed bullish bar to allow entry
    strategy.onBar?.(makeBar(99, 100.5), "1m");

    const entryTick = makeTick(100, 1_000);
    const entryFeatures = makeFeatures({
      lastPrice: 100,
      microVwap: 99.8,
      shortMomentumPct: 0.1,
      orderBookImbalance: 0.2,
      spreadBps: 8,
    });
    const buy = strategy.onTick({ tick: entryTick, features: entryFeatures });
    expect(buy?.direction).toBe("buy");

    // Price drops beyond stop-loss threshold (default 0.22%)
    const exitFeatures = makeFeatures({
      lastPrice: 99.75,
      microVwap: 99.8,
      shortMomentumPct: -0.1,
      orderBookImbalance: -0.1,
      spreadBps: 8,
    });
    const sell = strategy.onTick({ tick: makeTick(99.75, 2_000), features: exitFeatures });
    expect(sell?.direction).toBe("sell");
  });

  it("ignores bars for non-1m intervals", () => {
    const strategy = createTickMomentum1mConfirmStrategy({ symbol: "600519" });

    // Feed bullish 5m bar — should NOT satisfy 1m confirmation
    strategy.onBar?.(makeBar(99, 101), "5m");

    const tick = makeTick(101, 1_000);
    const features = makeFeatures({
      lastPrice: 101,
      microVwap: 100,
      shortMomentumPct: 0.1,
      orderBookImbalance: 0.2,
      spreadBps: 8,
    });
    expect(strategy.onTick({ tick, features })).toBeNull();
  });
});
