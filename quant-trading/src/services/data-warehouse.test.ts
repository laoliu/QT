import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { MarketDataProvider } from "../data/market-data-provider.js";
import type { KlineBar, KlineInterval, MarketType, QuoteSnapshot, SymbolInfo } from "../types.js";
import { HistoricalDataWarehouse } from "./data-warehouse.js";
import { InMemoryStorage } from "./sqlite-storage.js";

// ── Mock provider ────────────────────────────────────────────

function makeBars(startDate: string, count: number, base = 100): KlineBar[] {
  const bars: KlineBar[] = [];
  const [y, m, d] = startDate.split("-").map(Number);
  for (let i = 0; i < count; i++) {
    const date = new Date(Date.UTC(y!, m! - 1, d! + i));
    bars.push({
      timestamp: date.getTime(),
      open: base + i,
      high: base + i + 5,
      low: base + i - 2,
      close: base + i + 3,
      volume: 10000 + i * 100,
    });
  }
  return bars;
}

function createMockProvider(
  bars: KlineBar[] = [],
): MarketDataProvider & { getKlines: ReturnType<typeof vi.fn> } {
  return {
    name: "mock",
    getKlines: vi.fn().mockResolvedValue(bars),
    getQuote: vi.fn().mockResolvedValue(null),
    searchSymbols: vi.fn().mockResolvedValue([]),
  };
}

// ── Tests ────────────────────────────────────────────────────

describe("HistoricalDataWarehouse", () => {
  let storage: InMemoryStorage;
  let provider: ReturnType<typeof createMockProvider>;
  let warehouse: HistoricalDataWarehouse;

  beforeEach(async () => {
    storage = new InMemoryStorage(null);
    await storage.initialize();
    provider = createMockProvider(makeBars("2024-01-02", 5, 100));
    warehouse = new HistoricalDataWarehouse({
      provider,
      storage,
      refreshToday: false, // disable for deterministic tests
      concurrency: 2,
    });
  });

  afterEach(async () => {
    await storage.close();
  });

  // ── Basic fetch & cache ───────────────────────────────────

  it("fetches from upstream on first request", async () => {
    const bars = await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-06");
    expect(bars).toHaveLength(5);
    expect(provider.getKlines).toHaveBeenCalledOnce();
    expect(bars[0]!.close).toBe(103);
  });

  it("serves from cache on second request (no upstream call)", async () => {
    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-06");
    provider.getKlines.mockClear();

    const bars = await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-06");
    expect(bars).toHaveLength(5);
    expect(provider.getKlines).not.toHaveBeenCalled();
  });

  it("tracks cache hit/miss stats", async () => {
    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-06");
    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-06");

    const stats = await warehouse.getStats();
    expect(stats.cacheHits).toBe(1);
    expect(stats.cacheMisses).toBe(1);
    expect(stats.hitRate).toBe("50.0%");
  });

  // ── Incremental fetch ─────────────────────────────────────

  it("fetches only missing date ranges (incremental)", async () => {
    // First: fetch Jan 2-4
    provider.getKlines.mockResolvedValueOnce(makeBars("2024-01-02", 3, 100));
    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-04");
    expect(provider.getKlines).toHaveBeenCalledOnce();

    // Second: fetch Jan 2-6 — should only request Jan 5-6 from upstream
    provider.getKlines.mockClear();
    provider.getKlines.mockResolvedValueOnce(makeBars("2024-01-05", 2, 104));
    const bars = await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-06");
    expect(bars).toHaveLength(5);
    expect(provider.getKlines).toHaveBeenCalledOnce();
    // The call should request starting from "2024-01-05"
    const callArgs = provider.getKlines.mock.calls[0];
    expect(callArgs![2]).toBe("2024-01-05"); // start
    expect(callArgs![3]).toBe("2024-01-06"); // end
  });

  it("handles gaps in the middle of cached data", async () => {
    // Pre-populate: Jan 2, Jan 4 (gap on Jan 3)
    await storage.upsertKlines([
      {
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 100,
        high: 105,
        low: 98,
        close: 103,
        volume: 10000,
      },
      {
        symbol: "600519",
        date: "2024-01-04",
        interval: "1d",
        open: 102,
        high: 107,
        low: 100,
        close: 105,
        volume: 10200,
      },
    ]);

    provider.getKlines.mockResolvedValueOnce(makeBars("2024-01-03", 1, 101));
    const bars = await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-04");
    expect(bars).toHaveLength(3);
    expect(provider.getKlines).toHaveBeenCalledOnce();
    const callArgs = provider.getKlines.mock.calls[0];
    expect(callArgs![2]).toBe("2024-01-03"); // only the gap
    expect(callArgs![3]).toBe("2024-01-03");
  });

  // ── Upstream failures ─────────────────────────────────────

  it("returns cached data even if upstream fails for gaps", async () => {
    // Pre-populate Jan 2
    await storage.upsertKlines([
      {
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 100,
        high: 105,
        low: 98,
        close: 103,
        volume: 10000,
      },
    ]);

    provider.getKlines.mockRejectedValueOnce(new Error("network error"));
    const bars = await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-03");
    // Should still return Jan 2 from cache
    expect(bars).toHaveLength(1);
    expect(bars[0]!.close).toBe(103);
  });

  // ── RefreshToday ──────────────────────────────────────────

  it("re-fetches today's bar when refreshToday is enabled", async () => {
    const todayStr = new Date().toISOString().slice(0, 10);
    const [ty, tm, td] = todayStr.split("-").map(Number);
    const todayWarehouse = new HistoricalDataWarehouse({
      provider,
      storage,
      refreshToday: true,
    });

    // Pre-populate with today's bar
    await storage.upsertKlines([
      {
        symbol: "600519",
        date: todayStr,
        interval: "1d",
        open: 100,
        high: 105,
        low: 98,
        close: 103,
        volume: 10000,
      },
    ]);

    provider.getKlines.mockResolvedValueOnce([
      {
        timestamp: Date.UTC(ty!, tm! - 1, td!),
        open: 100,
        high: 110,
        low: 98,
        close: 108,
        volume: 15000,
      },
    ]);

    await todayWarehouse.getKlines("600519", "1d", todayStr, todayStr);
    // With refreshToday, it should call upstream even though data exists
    expect(provider.getKlines).toHaveBeenCalledOnce();
  });

  // ── Warmup ────────────────────────────────────────────────

  it("warms up multiple symbols in parallel", async () => {
    provider.getKlines
      .mockResolvedValueOnce(makeBars("2024-01-02", 3, 100))
      .mockResolvedValueOnce(makeBars("2024-01-02", 3, 200))
      .mockResolvedValueOnce(makeBars("2024-01-02", 3, 300));

    const results = await warehouse.warmup(
      ["600519", "000001", "000858"],
      "1d",
      "2024-01-02",
      "2024-01-04",
    );
    expect(results).toHaveLength(3);
    expect(results.every((r) => r.cached === 3)).toBe(true);
    expect(provider.getKlines).toHaveBeenCalledTimes(3);
  });

  it("reports errors during warmup without failing", async () => {
    provider.getKlines
      .mockResolvedValueOnce(makeBars("2024-01-02", 3, 100))
      .mockRejectedValueOnce(new Error("timeout"));

    const results = await warehouse.warmup(["600519", "000001"], "1d", "2024-01-02", "2024-01-04");
    expect(results).toHaveLength(2);
    // First succeeds (or falls through)
    const ok = results.find((r) => r.symbol === "600519");
    expect(ok?.cached).toBe(3);
  });

  // ── Stats ─────────────────────────────────────────────────

  it("provides detailed statistics", async () => {
    provider.getKlines.mockResolvedValue(makeBars("2024-01-02", 3, 100));
    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-04");

    const stats = await warehouse.getStats();
    expect(stats.totalBars).toBe(3);
    expect(stats.symbolCount).toBe(1);
    expect(stats.symbols[0]!.symbol).toBe("600519");
    expect(stats.symbols[0]!.dateRange).toEqual({ min: "2024-01-02", max: "2024-01-04" });
    expect(stats.upstreamFetches).toBeGreaterThan(0);
  });

  it("resets stats counters", async () => {
    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-06");
    warehouse.resetStats();
    const stats = await warehouse.getStats();
    expect(stats.cacheHits).toBe(0);
    expect(stats.cacheMisses).toBe(0);
    expect(stats.upstreamFetches).toBe(0);
  });

  // ── CachedRange ───────────────────────────────────────────

  it("reports cached date range", async () => {
    await storage.upsertKlines([
      {
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 100,
        high: 105,
        low: 98,
        close: 103,
        volume: 10000,
      },
      {
        symbol: "600519",
        date: "2024-01-10",
        interval: "1d",
        open: 110,
        high: 115,
        low: 108,
        close: 113,
        volume: 12000,
      },
    ]);
    const range = await warehouse.getCachedRange("600519", "1d");
    expect(range).toEqual({ min: "2024-01-02", max: "2024-01-10" });
  });

  it("returns null for symbol with no cached data", async () => {
    const range = await warehouse.getCachedRange("999999", "1d");
    expect(range).toBeNull();
  });

  // ── Different symbols/intervals are isolated ──────────────

  it("maintains separate caches per symbol", async () => {
    provider.getKlines
      .mockResolvedValueOnce(makeBars("2024-01-02", 3, 100))
      .mockResolvedValueOnce(makeBars("2024-01-02", 3, 200));

    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-04");
    await warehouse.getKlines("000001", "1d", "2024-01-02", "2024-01-04");

    const count519 = await storage.getKlineCount("600519");
    const count001 = await storage.getKlineCount("000001");
    expect(count519).toBe(3);
    expect(count001).toBe(3);
    expect(provider.getKlines).toHaveBeenCalledTimes(2);
  });

  // ── Invalidate ────────────────────────────────────────────

  it("reports invalidation count", async () => {
    await storage.upsertKlines([
      {
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 100,
        high: 105,
        low: 98,
        close: 103,
        volume: 10000,
      },
      {
        symbol: "600519",
        date: "2024-01-03",
        interval: "1d",
        open: 101,
        high: 106,
        low: 99,
        close: 104,
        volume: 10100,
      },
    ]);
    const result = await warehouse.invalidate("600519", "1d");
    expect(result.cleared).toBe(2);
    // Verify the data is actually deleted
    expect(await storage.getKlineCount("600519")).toBe(0);
    expect(await storage.getKlineDateRange("600519", "1d")).toBeNull();
  });

  it("invalidate() clears knownSymbols tracking", async () => {
    provider.getKlines.mockResolvedValue(makeBars("2024-01-02", 3, 100));
    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-04");
    const statsBefore = await warehouse.getStats();
    expect(statsBefore.symbolCount).toBe(1);

    await warehouse.invalidate("600519", "1d");
    const statsAfter = await warehouse.getStats();
    expect(statsAfter.symbolCount).toBe(0);
    expect(statsAfter.symbols).toHaveLength(0);
  });

  it("getStats returns correct per-symbol bar count (not total)", async () => {
    // Insert same symbol with two different intervals
    await storage.upsertKlines([
      {
        symbol: "600519",
        date: "2024-01-02",
        interval: "1d",
        open: 100,
        high: 105,
        low: 98,
        close: 103,
        volume: 10000,
      },
      {
        symbol: "600519",
        date: "2024-01-03",
        interval: "1d",
        open: 101,
        high: 106,
        low: 99,
        close: 104,
        volume: 10100,
      },
      {
        symbol: "600519",
        date: "2024-01-02",
        interval: "1w",
        open: 100,
        high: 110,
        low: 95,
        close: 108,
        volume: 50000,
      },
    ]);
    // Simulate that warehouse tracked both keys
    provider.getKlines.mockResolvedValue(makeBars("2024-01-02", 2, 100));
    await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-03");
    provider.getKlines.mockResolvedValue(makeBars("2024-01-02", 1, 100));
    await warehouse.getKlines("600519", "1w", "2024-01-02", "2024-01-02");

    const stats = await warehouse.getStats();
    const daily = stats.symbols.find((s) => s.interval === "1d");
    const weekly = stats.symbols.find((s) => s.interval === "1w");
    // Each should report its own count, not the total
    expect(daily!.bars).toBe(2);
    expect(weekly!.bars).toBe(1);
  });

  // ── Data conversion ───────────────────────────────────────

  it("correctly converts between KlineBar and KlineRow formats", async () => {
    const inputBars: KlineBar[] = [
      {
        timestamp: Date.UTC(2024, 0, 2),
        open: 1800,
        high: 1850,
        low: 1790,
        close: 1830,
        volume: 10000,
        amount: 18000000,
      },
    ];
    provider.getKlines.mockResolvedValueOnce(inputBars);

    const bars = await warehouse.getKlines("600519", "1d", "2024-01-02", "2024-01-02");
    expect(bars).toHaveLength(1);
    expect(bars[0]!.open).toBe(1800);
    expect(bars[0]!.close).toBe(1830);
    expect(bars[0]!.amount).toBe(18000000);
    expect(bars[0]!.timestamp).toBe(Date.UTC(2024, 0, 2));
  });
});
