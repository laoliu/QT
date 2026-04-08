/**
 * Tests for market data provider abstraction.
 */
import { describe, expect, it, vi } from "vitest";
import { createDataProvider, detectMarketFromSymbol, LRUCache } from "./market-data-provider.js";

describe("market-data-provider", () => {
  describe("DemoProvider (default)", () => {
    const provider = createDataProvider({ dataProvider: "demo" });

    it("should default to demo provider", () => {
      expect(provider.name).toBe("demo");
    });

    it("getQuote should return a valid snapshot", async () => {
      const quote = await provider.getQuote("600519");
      expect(quote).not.toBeNull();
      expect(quote!.symbol.code).toBe("600519");
      expect(typeof quote!.price).toBe("number");
      expect(quote!.price).toBeGreaterThan(0);
      expect(typeof quote!.changePct).toBe("number");
      expect(typeof quote!.volume).toBe("number");
    });

    it("getKlines should return bars for a date range", async () => {
      const bars = await provider.getKlines("600519", "1d", "2024-01-01", "2024-03-31");
      expect(bars.length).toBeGreaterThan(0);
      for (const bar of bars) {
        expect(typeof bar.open).toBe("number");
        expect(typeof bar.high).toBe("number");
        expect(typeof bar.low).toBe("number");
        expect(typeof bar.close).toBe("number");
        expect(typeof bar.volume).toBe("number");
        expect(bar.high).toBeGreaterThanOrEqual(bar.low);
      }
    });

    it("getKlines bars should be chronologically ordered", async () => {
      const bars = await provider.getKlines("000300", "1d", "2024-01-01", "2024-06-30");
      for (let i = 1; i < bars.length; i++) {
        expect(bars[i]!.timestamp).toBeGreaterThan(bars[i - 1]!.timestamp);
      }
    });

    it("searchSymbols should return matches", async () => {
      const results = await provider.searchSymbols("ę́");
      expect(results.length).toBeGreaterThan(0);
      expect(results[0]!.code).toBe("600519");
      expect(results[0]!.name).toContain("ę́");
    });

    it("searchSymbols should return empty for no match", async () => {
      const results = await provider.searchSymbols("ZZZZZZZ_NO_MATCH");
      expect(results.length).toBe(0);
    });
  });

  describe("provider selection", () => {
    it("should use smart provider by default (no explicit config)", () => {
      const provider = createDataProvider({});
      expect(provider.name).toBe("cached(smart)");
    });

    it("should use akshare via smart provider when configured", () => {
      const provider = createDataProvider({
        dataProviders: { akshare: { enabled: true } },
      });
      expect(provider.name).toBe("cached(smart)");
    });

    it("should use tushare when configured", () => {
      const provider = createDataProvider({
        dataProviders: { tushare: { enabled: true, token: "test" } },
      });
      expect(provider.name).toBe("cached(tushare)");
    });

    it("should prefer tushare over akshare when both enabled", () => {
      const provider = createDataProvider({
        dataProviders: {
          akshare: { enabled: true },
          tushare: { enabled: true, token: "test" },
        },
      });
      expect(provider.name).toBe("cached(tushare)");
    });

    it("should use demo when dataProvider is set to demo", () => {
      const provider = createDataProvider({ dataProvider: "demo" });
      expect(provider.name).toBe("demo");
    });
  });

  describe("detectMarketFromSymbol", () => {
    // Existing markets
    it("detects A-share stocks", () => {
      expect(detectMarketFromSymbol("600519")).toBe("cn_stock");
      expect(detectMarketFromSymbol("000001")).toBe("cn_stock");
    });

    it("detects HK stocks", () => {
      expect(detectMarketFromSymbol("00700.HK")).toBe("hk_stock");
    });

    it("detects US stocks", () => {
      expect(detectMarketFromSymbol("AAPL")).toBe("us_stock");
      expect(detectMarketFromSymbol("TSLA")).toBe("us_stock");
    });

    // ETF options
    it("detects ETF options (SSE 8-digit codes starting 1000)", () => {
      expect(detectMarketFromSymbol("10005646")).toBe("etf_option");
      expect(detectMarketFromSymbol("10006789")).toBe("etf_option");
    });

    it("detects ETF options (SZSE 8-digit codes starting 9000)", () => {
      expect(detectMarketFromSymbol("90000123")).toBe("etf_option");
    });

    // Index options
    it("detects CFFEX index options (IO-style)", () => {
      expect(detectMarketFromSymbol("IO2406-C-4000")).toBe("index_option");
      expect(detectMarketFromSymbol("IO2406-P-3800")).toBe("index_option");
    });

    // Commodity options
    it("detects DCE commodity options (dash-separated)", () => {
      expect(detectMarketFromSymbol("m2404-C-3200")).toBe("commodity_option");
      expect(detectMarketFromSymbol("c2405-P-2600")).toBe("commodity_option");
      expect(detectMarketFromSymbol("i2406-C-850")).toBe("commodity_option");
    });

    it("detects SHFE/CZCE commodity options (no dash)", () => {
      expect(detectMarketFromSymbol("cu2404C52000")).toBe("commodity_option");
      expect(detectMarketFromSymbol("au2406C500")).toBe("commodity_option");
      expect(detectMarketFromSymbol("SR405C5600")).toBe("commodity_option");
      expect(detectMarketFromSymbol("CF405P14000")).toBe("commodity_option");
    });

    // Futures
    it("detects CFFEX index futures", () => {
      expect(detectMarketFromSymbol("IF2503")).toBe("futures");
      expect(detectMarketFromSymbol("IH2506")).toBe("futures");
      expect(detectMarketFromSymbol("IC2504")).toBe("futures");
      expect(detectMarketFromSymbol("IM2505")).toBe("futures");
    });

    it("detects commodity futures", () => {
      expect(detectMarketFromSymbol("RB2505")).toBe("futures");
      expect(detectMarketFromSymbol("AU2406")).toBe("futures");
      expect(detectMarketFromSymbol("CU2504")).toBe("futures");
      expect(detectMarketFromSymbol("M2409")).toBe("futures");
    });

    it("detects futures product codes without contract month", () => {
      expect(detectMarketFromSymbol("IF")).toBe("futures");
      expect(detectMarketFromSymbol("RB")).toBe("futures");
      expect(detectMarketFromSymbol("AU")).toBe("futures");
    });
  });
});

describe("LRUCache", () => {
  it("stores and retrieves values", () => {
    const cache = new LRUCache<string>(10, 60_000);
    cache.set("a", "hello");
    expect(cache.get("a")).toBe("hello");
  });

  it("returns undefined for missing keys", () => {
    const cache = new LRUCache<string>(10, 60_000);
    expect(cache.get("missing")).toBeUndefined();
  });

  it("evicts oldest entry when max size exceeded", () => {
    const cache = new LRUCache<string>(2, 60_000);
    cache.set("a", "1");
    cache.set("b", "2");
    cache.set("c", "3"); // evicts "a"
    expect(cache.get("a")).toBeUndefined();
    expect(cache.get("b")).toBe("2");
    expect(cache.get("c")).toBe("3");
    expect(cache.size).toBe(2);
  });

  it("expires entries after TTL", () => {
    vi.useFakeTimers();
    const cache = new LRUCache<string>(10, 100); // 100 ms TTL
    cache.set("a", "val");
    expect(cache.get("a")).toBe("val");
    vi.advanceTimersByTime(150);
    expect(cache.get("a")).toBeUndefined();
    vi.useRealTimers();
  });

  it("promotes accessed entries (LRU behavior)", () => {
    const cache = new LRUCache<string>(2, 60_000);
    cache.set("a", "1");
    cache.set("b", "2");
    // Access "a" to promote it
    cache.get("a");
    // Now "b" is oldest, inserting "c" should evict "b"
    cache.set("c", "3");
    expect(cache.get("a")).toBe("1");
    expect(cache.get("b")).toBeUndefined();
    expect(cache.get("c")).toBe("3");
  });

  it("clear() removes all entries", () => {
    const cache = new LRUCache<string>(10, 60_000);
    cache.set("a", "1");
    cache.set("b", "2");
    cache.clear();
    expect(cache.size).toBe(0);
    expect(cache.get("a")).toBeUndefined();
  });

  it("overwrites existing keys without growing size", () => {
    const cache = new LRUCache<string>(2, 60_000);
    cache.set("a", "1");
    cache.set("b", "2");
    cache.set("a", "updated");
    expect(cache.size).toBe(2);
    expect(cache.get("a")).toBe("updated");
  });
});

// ── DemoProvider: no negative prices ─────────────────────────

describe("DemoProvider price safety", () => {
  const provider = createDataProvider({ dataProvider: "demo" });

  it("getKlines never produces negative open/low/close prices", async () => {
    // Run multiple times to increase chance of catching edge cases
    for (let attempt = 0; attempt < 5; attempt++) {
      const bars = await provider.getKlines("600519", "1d", "2024-01-01", "2024-12-31");
      for (const bar of bars) {
        expect(bar.open).toBeGreaterThan(0);
        expect(bar.low).toBeGreaterThan(0);
        expect(bar.close).toBeGreaterThan(0);
      }
    }
  });
});
