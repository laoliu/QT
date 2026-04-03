import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TradingSignal } from "../types.js";
import { SignalHitRateTracker } from "./signal-hitrate.js";

function makeSignal(overrides: Partial<TradingSignal> = {}): TradingSignal {
  return {
    timestamp: Date.now(),
    symbol: "600519",
    direction: "buy",
    price: 1800,
    strategyId: "dual-ma",
    ...overrides,
  };
}

describe("SignalHitRateTracker", () => {
  let tracker: SignalHitRateTracker;

  beforeEach(() => {
    tracker = new SignalHitRateTracker({ horizons: [1, 3], maxRecords: 50 });
  });

  // ── trackSignal ──

  it("tracks a signal with checkpoints for each horizon", () => {
    tracker.trackSignal(makeSignal());
    expect(tracker.size).toBe(1);

    const records = tracker.getRecent(10);
    expect(records).toHaveLength(1);
    expect(records[0].checkpoints).toHaveProperty("T+1");
    expect(records[0].checkpoints).toHaveProperty("T+3");
    expect(records[0].checkpoints["T+1"].result).toBe("pending");
    expect(records[0].checkpoints["T+3"].result).toBe("pending");
  });

  it("prunes oldest records when exceeding maxRecords", () => {
    const tracker2 = new SignalHitRateTracker({ maxRecords: 3 });
    for (let i = 0; i < 5; i++) {
      tracker2.trackSignal(makeSignal({ symbol: `SYM${i}` }));
    }
    expect(tracker2.size).toBe(3);
    // Newest should be preserved
    const records = tracker2.getRecent(3);
    expect(records[0].symbol).toBe("SYM4");
  });

  // ── checkAccuracy ──

  it("returns 0 when no quoteFn set", async () => {
    tracker.trackSignal(makeSignal());
    const updated = await tracker.checkAccuracy();
    expect(updated).toBe(0);
  });

  it("marks buy signal as hit when price rises", async () => {
    // Track signal with past dates (so checkpoints are due)
    const signal = makeSignal({ price: 100 });
    tracker.trackSignal(signal);

    // Override checkpoint dates to the past
    const records = tracker.getRecent(1);
    for (const cp of Object.values(records[0].checkpoints)) {
      cp.targetDate = "2020-01-01"; // past date
    }

    tracker.setQuoteFn(vi.fn().mockResolvedValue({ price: 110 })); // price up
    const updated = await tracker.checkAccuracy();
    expect(updated).toBe(2); // T+1, T+3

    const after = tracker.getRecent(1);
    expect(after[0].checkpoints["T+1"].result).toBe("hit");
    expect(after[0].checkpoints["T+1"].price).toBe(110);
    expect(after[0].checkpoints["T+3"].result).toBe("hit");
  });

  it("marks buy signal as miss when price drops", async () => {
    tracker.trackSignal(makeSignal({ price: 100 }));
    const records = tracker.getRecent(1);
    for (const cp of Object.values(records[0].checkpoints)) {
      cp.targetDate = "2020-01-01";
    }

    tracker.setQuoteFn(vi.fn().mockResolvedValue({ price: 90 })); // price down
    await tracker.checkAccuracy();

    const after = tracker.getRecent(1);
    expect(after[0].checkpoints["T+1"].result).toBe("miss");
  });

  it("marks sell signal as hit when price drops", async () => {
    tracker.trackSignal(makeSignal({ direction: "sell", price: 100 }));
    const records = tracker.getRecent(1);
    for (const cp of Object.values(records[0].checkpoints)) {
      cp.targetDate = "2020-01-01";
    }

    tracker.setQuoteFn(vi.fn().mockResolvedValue({ price: 90 })); // price down → hit for sell
    await tracker.checkAccuracy();

    const after = tracker.getRecent(1);
    expect(after[0].checkpoints["T+1"].result).toBe("hit");
  });

  it("marks sell signal as miss when price rises", async () => {
    tracker.trackSignal(makeSignal({ direction: "sell", price: 100 }));
    const records = tracker.getRecent(1);
    for (const cp of Object.values(records[0].checkpoints)) {
      cp.targetDate = "2020-01-01";
    }

    tracker.setQuoteFn(vi.fn().mockResolvedValue({ price: 110 }));
    await tracker.checkAccuracy();

    const after = tracker.getRecent(1);
    expect(after[0].checkpoints["T+1"].result).toBe("miss");
  });

  it("marks no_data when quote unavailable", async () => {
    tracker.trackSignal(makeSignal());
    const records = tracker.getRecent(1);
    for (const cp of Object.values(records[0].checkpoints)) {
      cp.targetDate = "2020-01-01";
    }

    tracker.setQuoteFn(vi.fn().mockResolvedValue(null));
    await tracker.checkAccuracy();

    const after = tracker.getRecent(1);
    expect(after[0].checkpoints["T+1"].result).toBe("no_data");
  });

  it("skips checkpoints with future dates", async () => {
    tracker.trackSignal(makeSignal());
    // Default dates are in the future (T+1, T+3 from now)
    tracker.setQuoteFn(vi.fn().mockResolvedValue({ price: 200 }));
    const updated = await tracker.checkAccuracy();
    expect(updated).toBe(0);
  });

  it("deduplicates quote calls for same symbol", async () => {
    tracker.trackSignal(makeSignal({ symbol: "A", price: 100 }));
    tracker.trackSignal(makeSignal({ symbol: "A", price: 100 }));
    const records = tracker.getRecent(10);
    for (const r of records) {
      for (const cp of Object.values(r.checkpoints)) {
        cp.targetDate = "2020-01-01";
      }
    }

    const quoteFn = vi.fn().mockResolvedValue({ price: 110 });
    tracker.setQuoteFn(quoteFn);
    await tracker.checkAccuracy();

    // Should only call quoteFn once for symbol "A"
    expect(quoteFn).toHaveBeenCalledTimes(1);
  });

  // ── getStats ──

  it("returns correct stats after checks", async () => {
    tracker.trackSignal(makeSignal({ strategyId: "s1", price: 100 }));
    tracker.trackSignal(makeSignal({ strategyId: "s2", direction: "sell", price: 100 }));

    const records = tracker.getRecent(10);
    for (const r of records) {
      for (const cp of Object.values(r.checkpoints)) {
        cp.targetDate = "2020-01-01";
      }
    }

    tracker.setQuoteFn(vi.fn().mockResolvedValue({ price: 110 })); // buy→hit, sell→miss
    await tracker.checkAccuracy();

    const stats = tracker.getStats();
    expect(stats.totalSignals).toBe(2);
    expect(stats.checkedSignals).toBe(2);
    expect(stats.horizons["T+1"].hits).toBe(1);
    expect(stats.horizons["T+1"].misses).toBe(1);
    expect(stats.horizons["T+1"].hitRate).toBe(0.5);

    expect(stats.strategies["s1"].hitRate).toBeGreaterThan(0);
  });

  it("handles empty tracker stats", () => {
    const stats = tracker.getStats();
    expect(stats.totalSignals).toBe(0);
    expect(stats.checkedSignals).toBe(0);
  });

  // ── getRecent ──

  it("returns records in reverse chronological order", () => {
    tracker.trackSignal(makeSignal({ symbol: "FIRST" }));
    tracker.trackSignal(makeSignal({ symbol: "SECOND" }));
    const recent = tracker.getRecent(2);
    expect(recent[0].symbol).toBe("SECOND");
    expect(recent[1].symbol).toBe("FIRST");
  });

  // ── Persistence ──

  it("persists and reloads from disk", () => {
    const tmpDir = `/tmp/test-hitrate-${Date.now()}`;
    const t1 = new SignalHitRateTracker({ dataDir: tmpDir });
    t1.trackSignal(makeSignal({ symbol: "SAVED" }));

    const t2 = new SignalHitRateTracker({ dataDir: tmpDir });
    expect(t2.size).toBe(1);
    expect(t2.getRecent(1)[0].symbol).toBe("SAVED");
  });
});
