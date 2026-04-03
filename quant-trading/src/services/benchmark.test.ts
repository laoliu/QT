import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { BenchmarkService, BENCHMARKS } from "./benchmark.js";
import type { EquitySnapshot } from "./equity-history.js";

// Mock the data provider
vi.mock("../data/market-data-provider.js", () => ({
  createDataProvider: () => ({
    getKlines: vi.fn().mockResolvedValue([
      {
        timestamp: new Date("2024-01-02").getTime(),
        open: 3400,
        high: 3450,
        low: 3380,
        close: 3420,
        volume: 1000,
      },
      {
        timestamp: new Date("2024-01-03").getTime(),
        open: 3420,
        high: 3460,
        low: 3400,
        close: 3450,
        volume: 1100,
      },
      {
        timestamp: new Date("2024-01-04").getTime(),
        open: 3450,
        high: 3480,
        low: 3430,
        close: 3470,
        volume: 1200,
      },
      {
        timestamp: new Date("2024-01-05").getTime(),
        open: 3470,
        high: 3500,
        low: 3460,
        close: 3490,
        volume: 1300,
      },
      {
        timestamp: new Date("2024-01-08").getTime(),
        open: 3490,
        high: 3510,
        low: 3470,
        close: 3500,
        volume: 1400,
      },
    ]),
  }),
}));

const cfg: QuantTradingConfig = { defaultMarket: "cn_stock" };

function makeEquitySnapshots(): EquitySnapshot[] {
  return [
    {
      date: "2024-01-02",
      equity: 100000,
      cash: 80000,
      marketValue: 20000,
      dailyPnl: 0,
      positionCount: 2,
    },
    {
      date: "2024-01-03",
      equity: 102000,
      cash: 82000,
      marketValue: 20000,
      dailyPnl: 2000,
      positionCount: 2,
    },
    {
      date: "2024-01-04",
      equity: 101000,
      cash: 81000,
      marketValue: 20000,
      dailyPnl: -1000,
      positionCount: 2,
    },
    {
      date: "2024-01-05",
      equity: 104000,
      cash: 84000,
      marketValue: 20000,
      dailyPnl: 3000,
      positionCount: 2,
    },
    {
      date: "2024-01-08",
      equity: 105000,
      cash: 85000,
      marketValue: 20000,
      dailyPnl: 1000,
      positionCount: 2,
    },
  ];
}

describe("BenchmarkService", () => {
  let service: BenchmarkService;

  beforeEach(() => {
    service = new BenchmarkService(cfg);
  });

  afterEach(() => {
    service.clearCache();
  });

  // ── Benchmark listing ─────────────────────────────────────

  it("lists all available benchmarks", () => {
    const benchmarks = service.listBenchmarks();
    expect(benchmarks.length).toBeGreaterThanOrEqual(4);
    expect(benchmarks.map((b) => b.id)).toContain("csi300");
    expect(benchmarks.map((b) => b.id)).toContain("spy");
  });

  it("gets benchmark by id", () => {
    const bench = service.getBenchmark("csi300");
    expect(bench).toBeDefined();
    expect(bench!.name).toBe("沪深300");
    expect(bench!.symbol).toBe("000300");
  });

  it("returns undefined for unknown benchmark", () => {
    expect(service.getBenchmark("nonexistent")).toBeUndefined();
  });

  // ── Data fetching ─────────────────────────────────────────

  it("fetches benchmark data with normalized returns", async () => {
    const data = await service.fetchBenchmarkData("csi300", "2024-01-02", "2024-01-08");
    expect(data).toHaveLength(5);
    expect(data[0]!.normalizedReturn).toBe(1); // starts at 1.0
    expect(data[4]!.close).toBe(3500);
    expect(data[4]!.normalizedReturn).toBeCloseTo(3500 / 3420, 4);
  });

  it("throws for unknown benchmark id", async () => {
    await expect(service.fetchBenchmarkData("invalid", "2024-01-01", "2024-01-31")).rejects.toThrow(
      "Unknown benchmark",
    );
  });

  it("uses cache for repeated requests", async () => {
    const data1 = await service.fetchBenchmarkData("csi300", "2024-01-02", "2024-01-08");
    const data2 = await service.fetchBenchmarkData("csi300", "2024-01-02", "2024-01-08");
    expect(data1).toBe(data2); // exact same reference (cached)
  });

  // ── Comparison ────────────────────────────────────────────

  it("compares strategy against benchmark", async () => {
    const equitySnaps = makeEquitySnapshots();
    const result = await service.compare(equitySnaps, "csi300");

    expect(result.benchmark.id).toBe("csi300");
    expect(result.data).toHaveLength(5);
    expect(result.strategyReturns).toHaveLength(5);

    // Strategy: 100k → 105k = 5% return
    expect(result.stats.strategyReturnPct).toBeCloseTo(5.0, 1);
    // Benchmark: 3420 → 3500 = 2.34% return
    expect(result.stats.benchmarkReturnPct).toBeCloseTo(2.34, 1);
    // Excess return: ~2.66%
    expect(result.stats.excessReturn).toBeCloseTo(2.66, 1);
    expect(result.stats.alpha).toBeCloseTo(2.66, 1);
  });

  it("normalized strategy returns start at 1.0", async () => {
    const equitySnaps = makeEquitySnapshots();
    const result = await service.compare(equitySnaps, "csi300");
    expect(result.strategyReturns[0]!.normalizedReturn).toBe(1);
    expect(result.strategyReturns[4]!.normalizedReturn).toBeCloseTo(1.05, 2);
  });

  it("throws when equity snapshots are insufficient", async () => {
    await expect(service.compare([makeEquitySnapshots()[0]!], "csi300")).rejects.toThrow(
      "at least 2",
    );
  });

  it("throws for unknown benchmark in comparison", async () => {
    await expect(service.compare(makeEquitySnapshots(), "invalid")).rejects.toThrow(
      "Unknown benchmark",
    );
  });

  // ── BENCHMARKS constant ───────────────────────────────────

  it("BENCHMARKS includes expected entries", () => {
    expect(BENCHMARKS.find((b) => b.id === "csi300")!.symbol).toBe("000300");
    expect(BENCHMARKS.find((b) => b.id === "csi500")!.symbol).toBe("000905");
    expect(BENCHMARKS.find((b) => b.id === "sse")!.symbol).toBe("000001");
    expect(BENCHMARKS.find((b) => b.id === "spy")!.symbol).toBe("SPY");
  });

  // ── Cache management ──────────────────────────────────────

  it("clearCache empties the cache", async () => {
    await service.fetchBenchmarkData("csi300", "2024-01-02", "2024-01-08");
    service.clearCache();
    // After clear, next fetch will re-call provider (still returns mock data)
    const data = await service.fetchBenchmarkData("csi300", "2024-01-02", "2024-01-08");
    expect(data).toHaveLength(5);
  });
});
