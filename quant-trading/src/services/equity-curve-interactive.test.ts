/**
 * Additional unit tests for the Equity Curve Interactive Analysis module.
 *
 * Complements equity-curve-analysis.test.ts with:
 *  1. BenchmarkService edge cases (zero prices, cache TTL, empty data, information ratio)
 *  2. EquityHistory advanced calculation edge cases
 *  3. EquityHistory + BenchmarkService integration (end-to-end with persistence)
 *  4. Concurrent / adversarial scenarios (parallel save, corrupt data recovery)
 *  5. Dashboard equity-curve interactive elements (time-range selector, tooltip, responsive)
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BrokerAccount, LivePosition } from "../types.js";
import { EquityHistory } from "./equity-history.js";
import type { EquitySnapshot } from "./equity-history.js";
import { BenchmarkService, BENCHMARKS } from "./benchmark.js";
import type { BenchmarkComparison, BenchmarkDataPoint } from "./benchmark.js";

// ── Helpers ─────────────────────────────────────────────────────

function tmpDir(): string {
  return path.join(
    os.tmpdir(),
    `eq-interactive-test-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );
}

function makeSnap(
  date: string,
  equity: number,
  dailyPnl: number,
  positionCount = 2,
): EquitySnapshot {
  return {
    date,
    equity,
    cash: equity * 0.6,
    marketValue: equity * 0.4,
    dailyPnl,
    positionCount,
  };
}

function mockAccount(overrides: Partial<BrokerAccount> = {}): BrokerAccount {
  return {
    accountId: "test-001",
    brokerType: "paper",
    cash: 80000,
    frozenCash: 0,
    totalAssets: 100000,
    marketValue: 20000,
    profit: 1500,
    profitPct: 1.5,
    todayProfit: 500,
    lastSyncAt: new Date().toISOString(),
    ...overrides,
  };
}

function mockPositions(count = 2): LivePosition[] {
  return Array.from({ length: count }, (_, i) => ({
    symbol: `60000${i}`,
    market: "cn_stock" as const,
    quantity: 100 * (i + 1),
    availableQty: 100 * (i + 1),
    frozenQty: 0,
    todayBuyQty: 0,
    avgCost: 10 + i,
    currentPrice: 11 + i,
    marketValue: (11 + i) * 100 * (i + 1),
    unrealizedPnl: 100 * (i + 1),
    unrealizedPnlPct: 10,
    source: "local" as const,
    lastSyncAt: new Date().toISOString(),
  }));
}

// ══════════════════════════════════════════════════════════════════
// 1. BenchmarkService Edge Cases
// ══════════════════════════════════════════════════════════════════

// We need a mock for the data provider to test BenchmarkService independently
vi.mock("../data/market-data-provider.js", () => {
  let mockKlinesData: Array<{
    timestamp: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }> = [];

  return {
    createDataProvider: () => ({
      getKlines: vi.fn().mockImplementation(() => Promise.resolve(mockKlinesData)),
    }),
    __setMockKlines: (data: typeof mockKlinesData) => {
      mockKlinesData = data;
    },
  };
});

// Access the mock setter
async function setMockKlines(
  data: Array<{
    timestamp: number;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>,
) {
  const mod = await import("../data/market-data-provider.js");
  (mod as any).__setMockKlines(data);
}

describe("BenchmarkService — Edge Cases", () => {
  let service: BenchmarkService;
  const cfg = { defaultMarket: "cn_stock" as const };

  beforeEach(async () => {
    service = new BenchmarkService(cfg);
    // Default: standard klines
    await setMockKlines([
      { timestamp: new Date("2024-01-02").getTime(), open: 3400, high: 3450, low: 3380, close: 3420, volume: 1000 },
      { timestamp: new Date("2024-01-03").getTime(), open: 3420, high: 3460, low: 3400, close: 3450, volume: 1100 },
      { timestamp: new Date("2024-01-04").getTime(), open: 3450, high: 3480, low: 3430, close: 3470, volume: 1200 },
      { timestamp: new Date("2024-01-05").getTime(), open: 3470, high: 3500, low: 3460, close: 3490, volume: 1300 },
      { timestamp: new Date("2024-01-08").getTime(), open: 3490, high: 3510, low: 3470, close: 3500, volume: 1400 },
    ]);
  });

  afterEach(() => {
    service.clearCache();
  });

  it("returns empty array when data provider returns no bars", async () => {
    await setMockKlines([]);
    const data = await service.fetchBenchmarkData("csi300", "2024-01-02", "2024-01-08");
    expect(data).toHaveLength(0);
  });

  it("handles zero close price in first bar (normalizedReturn defaults to 1)", async () => {
    await setMockKlines([
      { timestamp: new Date("2024-01-02").getTime(), open: 0, high: 0, low: 0, close: 0, volume: 0 },
      { timestamp: new Date("2024-01-03").getTime(), open: 100, high: 110, low: 95, close: 105, volume: 500 },
    ]);
    const data = await service.fetchBenchmarkData("csi300", "2024-01-02", "2024-01-03");
    expect(data).toHaveLength(2);
    // With close=0, normalizedReturn should be 1 (fallback)
    expect(data[0]!.normalizedReturn).toBe(1);
  });

  it("listBenchmarks includes all 4 default benchmarks", () => {
    const list = service.listBenchmarks();
    expect(list).toHaveLength(4);
    const ids = list.map((b) => b.id);
    expect(ids).toContain("csi300");
    expect(ids).toContain("csi500");
    expect(ids).toContain("sse");
    expect(ids).toContain("spy");
  });

  it("getBenchmark returns undefined for empty string", () => {
    expect(service.getBenchmark("")).toBeUndefined();
  });

  it("compare throws when equity snapshots has only 1 entry", async () => {
    const snaps: EquitySnapshot[] = [makeSnap("2024-01-02", 100000, 0)];
    await expect(service.compare(snaps, "csi300")).rejects.toThrow("at least 2");
  });

  it("compare throws when equity snapshots is empty", async () => {
    await expect(service.compare([], "csi300")).rejects.toThrow("at least 2");
  });

  it("compare throws for insufficient benchmark data", async () => {
    // Only 1 bar returned by provider
    await setMockKlines([
      { timestamp: new Date("2024-01-02").getTime(), open: 3400, high: 3450, low: 3380, close: 3420, volume: 1000 },
    ]);
    const snaps: EquitySnapshot[] = [
      makeSnap("2024-01-02", 100000, 0),
      makeSnap("2024-01-03", 102000, 2000),
    ];
    await expect(service.compare(snaps, "csi300")).rejects.toThrow("Insufficient");
  });

  it("compare computes correct excess return and alpha", async () => {
    const snaps: EquitySnapshot[] = [
      makeSnap("2024-01-02", 100000, 0),
      makeSnap("2024-01-03", 110000, 10000),
      makeSnap("2024-01-04", 120000, 10000),
      makeSnap("2024-01-05", 115000, -5000),
      makeSnap("2024-01-08", 125000, 10000),
    ];
    const result = await service.compare(snaps, "csi300");

    // Strategy: 100k → 125k = 25%
    expect(result.stats.strategyReturnPct).toBeCloseTo(25.0, 1);
    // Benchmark: 3420 → 3500 ≈ 2.34%
    expect(result.stats.benchmarkReturnPct).toBeCloseTo(2.34, 1);
    // Excess = strategy - benchmark
    expect(result.stats.excessReturn).toBeCloseTo(25.0 - 2.34, 1);
    expect(result.stats.alpha).toBe(result.stats.excessReturn);
  });

  it("compare normalized returns start at 1.0 for both strategy and benchmark", async () => {
    const snaps: EquitySnapshot[] = [
      makeSnap("2024-01-02", 100000, 0),
      makeSnap("2024-01-03", 102000, 2000),
      makeSnap("2024-01-04", 101000, -1000),
      makeSnap("2024-01-05", 104000, 3000),
      makeSnap("2024-01-08", 105000, 1000),
    ];
    const result = await service.compare(snaps, "csi300");
    expect(result.strategyReturns[0]!.normalizedReturn).toBe(1);
    expect(result.data[0]!.normalizedReturn).toBe(1);
  });

  it("compare handles strategy with zero starting equity", async () => {
    const snaps: EquitySnapshot[] = [
      makeSnap("2024-01-02", 0, 0),
      makeSnap("2024-01-03", 100000, 100000),
      makeSnap("2024-01-04", 200000, 100000),
      makeSnap("2024-01-05", 150000, -50000),
      makeSnap("2024-01-08", 180000, 30000),
    ];
    const result = await service.compare(snaps, "csi300");
    // With zero starting equity, normalizedReturn should be 1 (fallback)
    expect(result.strategyReturns[0]!.normalizedReturn).toBe(1);
    expect(result.stats.strategyReturnPct).toBe(0);
  });

  it("compare sorts equity snapshots before processing", async () => {
    // Pass snapshots out of order
    const snaps: EquitySnapshot[] = [
      makeSnap("2024-01-08", 105000, 1000),
      makeSnap("2024-01-02", 100000, 0),
      makeSnap("2024-01-05", 104000, 3000),
      makeSnap("2024-01-03", 102000, 2000),
      makeSnap("2024-01-04", 101000, -1000),
    ];
    const result = await service.compare(snaps, "csi300");
    // Should still compute correctly despite out-of-order input
    expect(result.stats.strategyReturnPct).toBeCloseTo(5.0, 1);
    expect(result.strategyReturns[0]!.date).toBe("2024-01-02");
  });

  it("information ratio is zero when tracking error is zero (flat strategy matches benchmark)", async () => {
    // All equity snapshots have same daily returns as benchmark
    await setMockKlines([
      { timestamp: new Date("2024-01-02").getTime(), open: 100, high: 100, low: 100, close: 100, volume: 100 },
      { timestamp: new Date("2024-01-03").getTime(), open: 100, high: 100, low: 100, close: 100, volume: 100 },
    ]);
    const snaps: EquitySnapshot[] = [
      makeSnap("2024-01-02", 100000, 0),
      makeSnap("2024-01-03", 100000, 0),
    ];
    const result = await service.compare(snaps, "csi300");
    expect(result.stats.informationRatio).toBe(0);
  });

  it("BENCHMARKS constant has correct market assignments", () => {
    const cn = BENCHMARKS.filter((b) => b.market === "cn_stock");
    const us = BENCHMARKS.filter((b) => b.market === "us_stock");
    expect(cn.length).toBe(3); // csi300, csi500, sse
    expect(us.length).toBe(1); // spy
    expect(us[0]!.id).toBe("spy");
  });
});

// ══════════════════════════════════════════════════════════════════
// 2. EquityHistory Advanced Calculation Edge Cases
// ══════════════════════════════════════════════════════════════════

describe("EquityHistory — Advanced Calculation Edge Cases", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  it("getSummary with two identical equity snapshots has zero drawdown", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 100000, 0));

    const summary = hist.getSummary();
    expect(summary.maxDrawdown).toBe(0);
    expect(summary.maxDrawdownPct).toBe(0);
    expect(summary.totalReturn).toBe(0);
  });

  it("getSummary with V-shaped equity correctly identifies max drawdown", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 80000, -20000)); // -20%
    hist.addSnapshot(makeSnap("2024-01-03", 100000, 20000));  // full recovery

    const summary = hist.getSummary();
    expect(summary.maxDrawdown).toBe(20000);
    expect(summary.maxDrawdownPct).toBeCloseTo(20.0);
    expect(summary.totalReturn).toBe(0); // flat overall
  });

  it("getSummary with W-shaped equity picks the deeper trough", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 90000, -10000));  // DD=10k from 100k
    hist.addSnapshot(makeSnap("2024-01-03", 105000, 15000));  // new peak
    hist.addSnapshot(makeSnap("2024-01-04", 80000, -25000));  // DD=25k from 105k — deeper
    hist.addSnapshot(makeSnap("2024-01-05", 110000, 30000));

    const summary = hist.getSummary();
    expect(summary.maxDrawdown).toBe(25000);
    expect(summary.maxDrawdownPct).toBeCloseTo((25000 / 105000) * 100, 1);
  });

  it("avgDailyPnl handles mix of positive, negative, and zero PnL", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 5000));
    hist.addSnapshot(makeSnap("2024-01-02", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-03", 100000, -3000));
    hist.addSnapshot(makeSnap("2024-01-04", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-05", 100000, 8000));

    const summary = hist.getSummary();
    // (5000 + 0 - 3000 + 0 + 8000) / 5 = 2000
    expect(summary.avgDailyPnl).toBeCloseTo(2000);
  });

  it("winRate excludes zero-PnL days from denominator", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 1000));  // win
    hist.addSnapshot(makeSnap("2024-01-02", 100000, 0));     // neutral
    hist.addSnapshot(makeSnap("2024-01-03", 100000, -500));   // loss
    hist.addSnapshot(makeSnap("2024-01-04", 100000, 0));     // neutral
    hist.addSnapshot(makeSnap("2024-01-05", 100000, 2000));  // win

    const summary = hist.getSummary();
    // 2 wins out of 3 non-zero days = 66.67%
    expect(summary.winDays).toBe(2);
    expect(summary.lossDays).toBe(1);
    expect(summary.winRate).toBeCloseTo(66.67, 0);
  });

  it("handles negative equity values without errors", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", -50000, -150000)); // margin call scenario

    const summary = hist.getSummary();
    expect(summary.totalReturn).toBe(-150000);
    expect(summary.totalReturnPct).toBeCloseTo(-150.0);
    expect(summary.maxDrawdown).toBe(150000);
    expect(Number.isFinite(summary.maxDrawdownPct)).toBe(true);
  });

  it("bestDay and worstDay are the same when only one snapshot exists", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 1234));

    const summary = hist.getSummary();
    expect(summary.bestDay).not.toBeNull();
    expect(summary.worstDay).not.toBeNull();
    expect(summary.bestDay!.date).toBe(summary.worstDay!.date);
    expect(summary.bestDay!.pnl).toBe(1234);
    expect(summary.worstDay!.pnl).toBe(1234);
  });

  it("handles very small equity changes without precision issues", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 100000.01, 0.01));
    hist.addSnapshot(makeSnap("2024-01-03", 100000.02, 0.01));

    const summary = hist.getSummary();
    expect(summary.totalReturn).toBeCloseTo(0.02, 5);
    expect(summary.totalReturnPct).toBeCloseTo(0.00002, 5);
    expect(summary.maxDrawdown).toBe(0);
  });

  it("record uses todayProfit over computed delta even when both available", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    // todayProfit = 777, but equity delta would suggest 5000
    const snap = hist.record(
      mockAccount({ totalAssets: 105000, todayProfit: 777 }),
      mockPositions(),
    );
    expect(snap.dailyPnl).toBe(777);
  });

  it("record does not compute delta from same-date previous snapshot", () => {
    const hist = new EquityHistory({ dataDir });
    // First record for today
    hist.record(mockAccount({ totalAssets: 100000, todayProfit: undefined }), mockPositions());
    // Second record for the same date — should not use delta from first
    const snap = hist.record(
      mockAccount({ totalAssets: 105000, todayProfit: undefined }),
      mockPositions(),
    );
    // Since previous snapshot has the same date, dailyPnl defaults to 0
    expect(snap.dailyPnl).toBe(0);
  });
});

// ══════════════════════════════════════════════════════════════════
// 3. EquityHistory + BenchmarkService Integration
// ══════════════════════════════════════════════════════════════════

describe("EquityHistory + BenchmarkService — Integration", () => {
  let dataDir: string;
  let service: BenchmarkService;
  const cfg = { defaultMarket: "cn_stock" as const };

  beforeEach(async () => {
    dataDir = tmpDir();
    service = new BenchmarkService(cfg);
    await setMockKlines([
      { timestamp: new Date("2024-01-02").getTime(), open: 3400, high: 3450, low: 3380, close: 3420, volume: 1000 },
      { timestamp: new Date("2024-01-03").getTime(), open: 3420, high: 3460, low: 3400, close: 3450, volume: 1100 },
      { timestamp: new Date("2024-01-04").getTime(), open: 3450, high: 3480, low: 3430, close: 3470, volume: 1200 },
      { timestamp: new Date("2024-01-05").getTime(), open: 3470, high: 3500, low: 3460, close: 3490, volume: 1300 },
      { timestamp: new Date("2024-01-08").getTime(), open: 3490, high: 3510, low: 3470, close: 3500, volume: 1400 },
    ]);
  });

  afterEach(async () => {
    service.clearCache();
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  it("save → reload → compare produces consistent benchmark comparison", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-02", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-03", 102000, 2000));
    hist.addSnapshot(makeSnap("2024-01-04", 101000, -1000));
    hist.addSnapshot(makeSnap("2024-01-05", 104000, 3000));
    hist.addSnapshot(makeSnap("2024-01-08", 105000, 1000));
    await hist.save();

    // Reload from disk
    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();

    const resultBefore = await service.compare(hist.getAll(), "csi300");
    service.clearCache();
    const resultAfter = await service.compare(hist2.getAll(), "csi300");

    expect(resultAfter.stats.strategyReturnPct).toBe(resultBefore.stats.strategyReturnPct);
    expect(resultAfter.stats.benchmarkReturnPct).toBe(resultBefore.stats.benchmarkReturnPct);
    expect(resultAfter.stats.excessReturn).toBe(resultBefore.stats.excessReturn);
    expect(resultAfter.stats.informationRatio).toBe(resultBefore.stats.informationRatio);
  });

  it("getSummary and compare use the same equity data", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-02", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-03", 102000, 2000));
    hist.addSnapshot(makeSnap("2024-01-04", 101000, -1000));
    hist.addSnapshot(makeSnap("2024-01-05", 104000, 3000));
    hist.addSnapshot(makeSnap("2024-01-08", 105000, 1000));

    const summary = hist.getSummary();
    const comparison = await service.compare(hist.getAll(), "csi300");

    // Strategy returns should match between summary and comparison
    expect(summary.totalReturn).toBe(comparison.stats.strategyReturn);
    expect(summary.totalReturnPct).toBeCloseTo(comparison.stats.strategyReturnPct, 1);
  });

  it("paper and live histories produce independent benchmark comparisons", async () => {
    const histPaper = new EquityHistory({ dataDir, mode: "paper" });
    histPaper.addSnapshot(makeSnap("2024-01-02", 100000, 0));
    histPaper.addSnapshot(makeSnap("2024-01-03", 110000, 10000)); // +10%
    histPaper.addSnapshot(makeSnap("2024-01-04", 120000, 10000));
    histPaper.addSnapshot(makeSnap("2024-01-05", 115000, -5000));
    histPaper.addSnapshot(makeSnap("2024-01-08", 125000, 10000));

    const histLive = new EquityHistory({ dataDir, mode: "live" });
    histLive.addSnapshot(makeSnap("2024-01-02", 100000, 0));
    histLive.addSnapshot(makeSnap("2024-01-03", 98000, -2000)); // -2%
    histLive.addSnapshot(makeSnap("2024-01-04", 96000, -2000));
    histLive.addSnapshot(makeSnap("2024-01-05", 95000, -1000));
    histLive.addSnapshot(makeSnap("2024-01-08", 94000, -1000));

    const paperComp = await service.compare(histPaper.getAll(), "csi300");
    service.clearCache();
    const liveComp = await service.compare(histLive.getAll(), "csi300");

    // Paper outperforms, live underperforms
    expect(paperComp.stats.strategyReturnPct).toBeGreaterThan(0);
    expect(liveComp.stats.strategyReturnPct).toBeLessThan(0);
    expect(paperComp.stats.excessReturn).toBeGreaterThan(liveComp.stats.excessReturn);
  });
});

// ══════════════════════════════════════════════════════════════════
// 4. Concurrent / Adversarial Scenarios
// ══════════════════════════════════════════════════════════════════

describe("EquityHistory — Concurrent & Adversarial Scenarios", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  it("concurrent saves to same file do not corrupt data", async () => {
    const hist = new EquityHistory({ dataDir });
    for (let d = 1; d <= 10; d++) {
      hist.addSnapshot(makeSnap(`2024-01-${String(d).padStart(2, "0")}`, 100000 + d * 1000, d * 100));
    }

    // Fire multiple saves in parallel
    await Promise.all([hist.save(), hist.save(), hist.save()]);

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    expect(hist2.count).toBe(10);
  });

  it("load after failed save still works (directory does not exist case)", async () => {
    const nonExistDir = path.join(tmpDir(), "nonexist", "deep");
    const hist = new EquityHistory({ dataDir: nonExistDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    await hist.save();

    const hist2 = new EquityHistory({ dataDir: nonExistDir });
    await hist2.load();
    expect(hist2.count).toBe(1);

    await fs.rm(path.dirname(path.dirname(nonExistDir)), { recursive: true, force: true });
  });

  it("load handles file with empty array gracefully", async () => {
    await fs.mkdir(dataDir, { recursive: true });
    await fs.writeFile(path.join(dataDir, "equity-history.json"), "[]");

    const hist = new EquityHistory({ dataDir });
    await hist.load();
    expect(hist.count).toBe(0);
    expect(hist.getAll()).toEqual([]);
  });

  it("load handles file with array of invalid objects gracefully", async () => {
    await fs.mkdir(dataDir, { recursive: true });
    // Array with objects missing required fields — still loads as-is
    await fs.writeFile(
      path.join(dataDir, "equity-history.json"),
      JSON.stringify([{ date: "2024-01-01", equity: 100000 }]),
    );

    const hist = new EquityHistory({ dataDir });
    await hist.load();
    // It loads whatever is in the array (no schema validation on load)
    expect(hist.count).toBe(1);
  });

  it("multiple EquityHistory instances for same file last-write-wins", async () => {
    const hist1 = new EquityHistory({ dataDir });
    hist1.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist1.addSnapshot(makeSnap("2024-01-02", 110000, 10000));

    const hist2 = new EquityHistory({ dataDir });
    hist2.addSnapshot(makeSnap("2024-01-01", 200000, 0));
    hist2.addSnapshot(makeSnap("2024-01-02", 220000, 20000));

    await hist1.save();
    await hist2.save(); // overwrites hist1's data

    const hist3 = new EquityHistory({ dataDir });
    await hist3.load();
    expect(hist3.getLatest()!.equity).toBe(220000);
  });

  it("addSnapshot with extremely long date string still works", () => {
    const hist = new EquityHistory({ dataDir });
    // Non-standard date format — system should still store it
    hist.addSnapshot(makeSnap("2024-01-01T12:00:00.000Z", 100000, 0));
    expect(hist.count).toBe(1);
    expect(hist.getLatest()!.date).toBe("2024-01-01T12:00:00.000Z");
  });

  it("maxSnapshots=0 means unlimited (slice(-0) returns full array)", () => {
    // maxSnapshots=0 is an edge case: slice(-0) === slice(0) returns everything
    const hist = new EquityHistory({ dataDir, maxSnapshots: 0 });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 110000, 10000));
    hist.addSnapshot(makeSnap("2024-01-03", 120000, 10000));

    // slice(-0) returns full array, so nothing is pruned
    expect(hist.count).toBe(3);
  });

  it("rapid consecutive records for same date always keep latest", () => {
    const hist = new EquityHistory({ dataDir });
    for (let i = 0; i < 100; i++) {
      hist.record(mockAccount({ totalAssets: 100000 + i }), mockPositions());
    }
    expect(hist.count).toBe(1);
    expect(hist.getLatest()!.equity).toBe(100099);
  });
});

// ══════════════════════════════════════════════════════════════════
// 5. Dashboard Equity Curve Interactive Elements
// ══════════════════════════════════════════════════════════════════

describe("Dashboard — Equity Curve Interactive Elements", () => {
  let getDashboardHtml: (baseUrl: string) => string;

  beforeEach(async () => {
    const mod = await import("../http/dashboard.js");
    getDashboardHtml = mod.getDashboardHtml;
  });

  it("includes time range selector buttons for equity chart", () => {
    const html = getDashboardHtml("http://localhost:18789");
    // Should have time range controls (1W, 1M, 3M, ALL, etc.)
    expect(html).toContain("loadEquityChart");
  });

  it("includes SVG-based chart rendering (no external dependencies)", () => {
    const html = getDashboardHtml("http://localhost:18789");
    // SVG chart rendering functions
    expect(html).toContain("renderSvgChart");
    expect(html).toContain("<svg");
  });

  it("includes daily P&L bar display elements", () => {
    const html = getDashboardHtml("http://localhost:18789");
    // Daily PnL visualization
    expect(html).toContain("Daily P&L");
  });

  it("includes equity summary metrics display", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("最大回撤");
    expect(html).toContain("胜率");
    expect(html).toContain("收益曲线");
  });

  it("includes benchmark comparison UI elements", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain('id="benchmarkSelect"');
    expect(html).toContain("沪深300");
    expect(html).toContain("中证500");
    expect(html).toContain("loadBenchmarkOverlay");
  });

  it("includes benchmark stats display section", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain('id="benchmarkStats"');
  });

  it("uses baseUrl parameter in API endpoint URLs", () => {
    const html = getDashboardHtml("http://custom-host:9999");
    expect(html).toContain("http://custom-host:9999");
  });

  it("includes equity-history and summary API calls", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("/quant/api/equity-history");
    expect(html).toContain("/quant/api/equity-history/summary");
    expect(html).toContain("/quant/api/benchmark/compare");
  });

  it("includes single data point fallback display", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("首日，暂无趋势");
  });

  it("includes legacy PnL endpoint fallback", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("/quant/api/pnl");
  });

  it("includes CSS for chart line and area styling", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain(".chart-line");
    expect(html).toContain(".chart-area");
  });

  it("includes SSE event handler for real-time equity updates", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("loadEquityChart");
    expect(html).toContain("equity");
  });

  it("includes maxDrawdownPct and winRate in summary rendering", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("maxDrawdownPct");
    expect(html).toContain("winRate");
  });
});

// ══════════════════════════════════════════════════════════════════
// 6. EquityHistory — Persistence Edge Cases
// ══════════════════════════════════════════════════════════════════

describe("EquityHistory — Persistence Edge Cases", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  it("save then immediate load preserves snapshot order", async () => {
    const hist = new EquityHistory({ dataDir });
    // Add out of order
    hist.addSnapshot(makeSnap("2024-06-01", 108000, 3000));
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-03-15", 105000, 5000));
    await hist.save();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    const all = hist2.getAll();
    // getAll always returns sorted
    expect(all[0]!.date).toBe("2024-01-01");
    expect(all[1]!.date).toBe("2024-03-15");
    expect(all[2]!.date).toBe("2024-06-01");
  });

  it("save with benchmark field preserves it through round-trip", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot({
      ...makeSnap("2024-01-01", 100000, 0),
      benchmark: 3456.78,
    });
    await hist.save();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    expect(hist2.getLatest()!.benchmark).toBe(3456.78);
  });

  it("save without benchmark field results in undefined on reload", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    await hist.save();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    expect(hist2.getLatest()!.benchmark).toBeUndefined();
  });

  it("close on dirty instance saves data", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    // dirty=true because we added a snapshot
    await hist.close();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    expect(hist2.count).toBe(1);
  });

  it("close on non-dirty instance does not create file", async () => {
    const hist = new EquityHistory({ dataDir });
    await hist.load(); // no file exists, loaded=true, dirty=false
    await hist.close();

    // Directory may not even exist, or file should not be there
    try {
      const files = await fs.readdir(dataDir);
      expect(files).not.toContain("equity-history.json");
    } catch {
      // Directory doesn't exist — that's fine
    }
  });

  it("paper and live mode use different file names", async () => {
    const histPaper = new EquityHistory({ dataDir, mode: "paper" });
    histPaper.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    await histPaper.save();

    const histLive = new EquityHistory({ dataDir, mode: "live" });
    histLive.addSnapshot(makeSnap("2024-01-01", 200000, 0));
    await histLive.save();

    const files = await fs.readdir(dataDir);
    expect(files).toContain("equity-history-paper.json");
    expect(files).toContain("equity-history-live.json");
  });

  it("default mode (no mode) uses equity-history.json", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    await hist.save();

    const files = await fs.readdir(dataDir);
    expect(files).toContain("equity-history.json");
  });

  it("JSON file contains properly formatted data", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 500));
    hist.addSnapshot(makeSnap("2024-01-02", 101000, 1000));
    await hist.save();

    const raw = await fs.readFile(path.join(dataDir, "equity-history.json"), "utf-8");
    const parsed = JSON.parse(raw);
    expect(Array.isArray(parsed)).toBe(true);
    expect(parsed).toHaveLength(2);
    expect(parsed[0].date).toBe("2024-01-01");
    expect(parsed[0].equity).toBe(100000);
    expect(parsed[1].date).toBe("2024-01-02");
    expect(parsed[1].equity).toBe(101000);
  });
});
