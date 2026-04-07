/**
 * Comprehensive tests for the Equity Curve Interactive Analysis module.
 *
 * Covers:
 *  1. Data model validation (EquitySnapshot shape, persistence round-trip)
 *  2. Calculation engine accuracy (getSummary, max drawdown, PnL, win rate)
 *  3. Chart rendering output (dashboard HTML contains equity curve elements)
 *  4. Boundary conditions (edge dates, maxSnapshots exact limit, duplicates)
 *  5. Empty / degenerate data scenarios (zero equity, single snapshot, NaN-safe)
 *  6. Integration: EquityHistory + BenchmarkService end-to-end
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { BrokerAccount, LivePosition } from "../types.js";
import { EquityHistory } from "./equity-history.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Helpers ─────────────────────────────────────────────────────

function tmpDir(): string {
  return path.join(
    os.tmpdir(),
    `eq-curve-test-${Date.now()}-${Math.random().toString(36).slice(2)}`,
  );
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

function makeSnap(date: string, equity: number, dailyPnl: number, positionCount = 2): EquitySnapshot {
  return {
    date,
    equity,
    cash: equity * 0.6,
    marketValue: equity * 0.4,
    dailyPnl,
    positionCount,
  };
}

// ══════════════════════════════════════════════════════════════════
// 1. Data Model Validation
// ══════════════════════════════════════════════════════════════════

describe("Equity Curve Analysis — Data Model", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  it("EquitySnapshot contains all required fields", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount(), mockPositions());

    expect(snap).toHaveProperty("date");
    expect(snap).toHaveProperty("equity");
    expect(snap).toHaveProperty("cash");
    expect(snap).toHaveProperty("marketValue");
    expect(snap).toHaveProperty("dailyPnl");
    expect(snap).toHaveProperty("positionCount");
    expect(typeof snap.date).toBe("string");
    expect(typeof snap.equity).toBe("number");
    expect(typeof snap.cash).toBe("number");
    expect(typeof snap.marketValue).toBe("number");
    expect(typeof snap.dailyPnl).toBe("number");
    expect(typeof snap.positionCount).toBe("number");
  });

  it("date format is ISO YYYY-MM-DD", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount(), mockPositions());
    expect(snap.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });

  it("equity equals totalAssets from account", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount({ totalAssets: 123456 }), mockPositions());
    expect(snap.equity).toBe(123456);
  });

  it("positionCount matches positions array length", () => {
    const hist = new EquityHistory({ dataDir });
    const snap0 = hist.record(mockAccount(), []);
    expect(snap0.positionCount).toBe(0);

    // Add a snapshot for a different date to avoid de-duplication
    hist.addSnapshot(makeSnap("2020-01-01", 100000, 0, 0));
    const snap5 = hist.record(mockAccount(), mockPositions(5));
    expect(snap5.positionCount).toBe(5);
  });

  it("benchmark field is optional and undefined when not provided", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount(), mockPositions());
    expect(snap.benchmark).toBeUndefined();
  });

  it("benchmark field is present when provided", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount(), mockPositions(), 4567.89);
    expect(snap.benchmark).toBe(4567.89);
  });

  it("persistence round-trip preserves all snapshot fields", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot({
      date: "2024-06-15",
      equity: 150000,
      cash: 90000,
      marketValue: 60000,
      dailyPnl: 1234.56,
      positionCount: 3,
      benchmark: 3800.5,
    });
    await hist.save();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    const snap = hist2.getLatest()!;
    expect(snap.date).toBe("2024-06-15");
    expect(snap.equity).toBe(150000);
    expect(snap.cash).toBe(90000);
    expect(snap.marketValue).toBe(60000);
    expect(snap.dailyPnl).toBe(1234.56);
    expect(snap.positionCount).toBe(3);
    expect(snap.benchmark).toBe(3800.5);
  });

  it("persistence uses separate files for paper and live modes", async () => {
    const histPaper = new EquityHistory({ dataDir, mode: "paper" });
    histPaper.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    await histPaper.save();

    const histLive = new EquityHistory({ dataDir, mode: "live" });
    histLive.addSnapshot(makeSnap("2024-01-01", 200000, 0));
    await histLive.save();

    // Reload and verify isolation
    const reloadPaper = new EquityHistory({ dataDir, mode: "paper" });
    await reloadPaper.load();
    expect(reloadPaper.getLatest()!.equity).toBe(100000);

    const reloadLive = new EquityHistory({ dataDir, mode: "live" });
    await reloadLive.load();
    expect(reloadLive.getLatest()!.equity).toBe(200000);
  });
});

// ══════════════════════════════════════════════════════════════════
// 2. Calculation Engine Accuracy
// ══════════════════════════════════════════════════════════════════

describe("Equity Curve Analysis — Calculation Engine", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  // ── Total Return ──────────────────────────────────────────

  it("total return calculation for profitable series", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 110000, 10000));
    hist.addSnapshot(makeSnap("2024-01-03", 120000, 10000));

    const summary = hist.getSummary();
    expect(summary.totalReturn).toBe(20000);
    expect(summary.totalReturnPct).toBeCloseTo(20.0);
  });

  it("total return calculation for losing series", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 95000, -5000));
    hist.addSnapshot(makeSnap("2024-01-03", 80000, -15000));

    const summary = hist.getSummary();
    expect(summary.totalReturn).toBe(-20000);
    expect(summary.totalReturnPct).toBeCloseTo(-20.0);
  });

  it("total return is zero for flat series", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-03", 100000, 0));

    const summary = hist.getSummary();
    expect(summary.totalReturn).toBe(0);
    expect(summary.totalReturnPct).toBe(0);
  });

  // ── Max Drawdown ──────────────────────────────────────────

  it("max drawdown for simple peak-trough pattern", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 120000, 20000));
    hist.addSnapshot(makeSnap("2024-01-03", 90000, -30000)); // DD = 30k from peak 120k
    hist.addSnapshot(makeSnap("2024-01-04", 110000, 20000));

    const summary = hist.getSummary();
    expect(summary.maxDrawdown).toBe(30000);
    expect(summary.maxDrawdownPct).toBeCloseTo(25.0); // 30k / 120k * 100
  });

  it("max drawdown for multiple peaks — picks worst", () => {
    const hist = new EquityHistory({ dataDir });
    // First peak at 110k, drop to 95k (DD=15k, 13.6%)
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 110000, 10000));
    hist.addSnapshot(makeSnap("2024-01-03", 95000, -15000));
    // Second peak at 130k, drop to 100k (DD=30k, 23.1%) — this is worse
    hist.addSnapshot(makeSnap("2024-01-04", 130000, 35000));
    hist.addSnapshot(makeSnap("2024-01-05", 100000, -30000));

    const summary = hist.getSummary();
    expect(summary.maxDrawdown).toBe(30000);
    expect(summary.maxDrawdownPct).toBeCloseTo(23.08, 1);
  });

  it("max drawdown is zero for monotonically increasing series", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 101000, 1000));
    hist.addSnapshot(makeSnap("2024-01-03", 102000, 1000));
    hist.addSnapshot(makeSnap("2024-01-04", 103000, 1000));

    const summary = hist.getSummary();
    expect(summary.maxDrawdown).toBe(0);
    expect(summary.maxDrawdownPct).toBe(0);
  });

  it("max drawdown for monotonically decreasing series", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 90000, -10000));
    hist.addSnapshot(makeSnap("2024-01-03", 80000, -10000));
    hist.addSnapshot(makeSnap("2024-01-04", 70000, -10000));

    const summary = hist.getSummary();
    expect(summary.maxDrawdown).toBe(30000);
    expect(summary.maxDrawdownPct).toBeCloseTo(30.0);
  });

  // ── Win / Loss Days ───────────────────────────────────────

  it("win/loss day counting is accurate", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));     // neutral
    hist.addSnapshot(makeSnap("2024-01-02", 102000, 2000));   // win
    hist.addSnapshot(makeSnap("2024-01-03", 101000, -1000));  // loss
    hist.addSnapshot(makeSnap("2024-01-04", 103000, 2000));   // win
    hist.addSnapshot(makeSnap("2024-01-05", 104000, 1000));   // win

    const summary = hist.getSummary();
    expect(summary.winDays).toBe(3);
    expect(summary.lossDays).toBe(1);
    expect(summary.winRate).toBeCloseTo(75.0);
  });

  it("zero-PnL days are neither win nor loss", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-03", 100000, 0));

    const summary = hist.getSummary();
    expect(summary.winDays).toBe(0);
    expect(summary.lossDays).toBe(0);
    expect(summary.winRate).toBe(0); // 0/0 → 0
  });

  it("all winning days produces 100% win rate", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 1000));
    hist.addSnapshot(makeSnap("2024-01-02", 102000, 2000));
    hist.addSnapshot(makeSnap("2024-01-03", 105000, 3000));

    const summary = hist.getSummary();
    expect(summary.winRate).toBeCloseTo(100.0);
  });

  it("all losing days produces 0% win rate", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, -1000));
    hist.addSnapshot(makeSnap("2024-01-02", 98000, -2000));
    hist.addSnapshot(makeSnap("2024-01-03", 95000, -3000));

    const summary = hist.getSummary();
    expect(summary.winRate).toBeCloseTo(0.0);
  });

  // ── Best / Worst Day ──────────────────────────────────────

  it("bestDay and worstDay identify correct dates", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 500));
    hist.addSnapshot(makeSnap("2024-01-02", 103000, 3000));   // best
    hist.addSnapshot(makeSnap("2024-01-03", 98000, -5000));    // worst
    hist.addSnapshot(makeSnap("2024-01-04", 100000, 2000));

    const summary = hist.getSummary();
    expect(summary.bestDay!.date).toBe("2024-01-02");
    expect(summary.bestDay!.pnl).toBe(3000);
    expect(summary.worstDay!.date).toBe("2024-01-03");
    expect(summary.worstDay!.pnl).toBe(-5000);
  });

  // ── Average Daily PnL ─────────────────────────────────────

  it("avgDailyPnl is the mean of all dailyPnl values", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 1000));
    hist.addSnapshot(makeSnap("2024-01-02", 102000, 2000));
    hist.addSnapshot(makeSnap("2024-01-03", 99000, -3000));
    hist.addSnapshot(makeSnap("2024-01-04", 101000, 2000));

    const summary = hist.getSummary();
    // mean = (1000 + 2000 - 3000 + 2000) / 4 = 500
    expect(summary.avgDailyPnl).toBeCloseTo(500);
  });

  // ── Large value accuracy ──────────────────────────────────

  it("handles very large equity values without precision loss", () => {
    const hist = new EquityHistory({ dataDir });
    const largeVal = 1_000_000_000; // 1 billion
    hist.addSnapshot(makeSnap("2024-01-01", largeVal, 0));
    hist.addSnapshot(makeSnap("2024-01-02", largeVal + 1000000, 1000000));

    const summary = hist.getSummary();
    expect(summary.totalReturn).toBe(1000000);
    expect(summary.totalReturnPct).toBeCloseTo(0.1);
  });

  // ── DailyPnl fallback logic ───────────────────────────────

  it("uses todayProfit when available", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount({ todayProfit: 777 }), mockPositions());
    expect(snap.dailyPnl).toBe(777);
  });

  it("computes delta from previous day when todayProfit is null", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    const snap = hist.record(
      mockAccount({ totalAssets: 103000, todayProfit: undefined }),
      mockPositions(),
    );
    expect(snap.dailyPnl).toBe(3000);
  });

  it("uses 0 when todayProfit is null and no previous snapshot", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(
      mockAccount({ todayProfit: undefined }),
      mockPositions(),
    );
    expect(snap.dailyPnl).toBe(0);
  });
});

// ══════════════════════════════════════════════════════════════════
// 3. Chart Rendering Output (Dashboard HTML)
// ══════════════════════════════════════════════════════════════════

describe("Equity Curve Analysis — Chart Rendering Output", () => {
  // Lazy import to avoid circular dependencies at module level
  let getDashboardHtml: (baseUrl: string) => string;

  beforeEach(async () => {
    const mod = await import("../http/dashboard.js");
    getDashboardHtml = mod.getDashboardHtml;
  });

  it("dashboard HTML includes equity chart container with correct ID", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain('id="equityChart"');
  });

  it("dashboard HTML includes 收益曲线 (equity curve) title", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("收益曲线");
  });

  it("dashboard HTML includes SVG chart rendering function", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("renderSvgChart");
  });

  it("dashboard HTML includes equity-history API endpoint calls", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("/quant/api/equity-history");
    expect(html).toContain("/quant/api/equity-history/summary");
  });

  it("dashboard HTML includes equity summary stats display element", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain('id="equitySummary"');
  });

  it("dashboard HTML includes chart CSS classes for line and area", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain(".chart-line");
    expect(html).toContain(".chart-area");
  });

  it("dashboard HTML includes benchmark selector for equity curve overlay", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain('id="benchmarkSelect"');
    expect(html).toContain("沪深300");
    expect(html).toContain("loadBenchmarkOverlay");
  });

  it("dashboard HTML includes loadEquityChart in SSE event handler", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("loadEquityChart");
    // The SSE filter includes 'equity' event type
    expect(html).toContain("equity");
  });

  it("dashboard HTML handles single data point display gracefully", () => {
    const html = getDashboardHtml("http://localhost:18789");
    // Check for the single-point display path
    expect(html).toContain("首日，暂无趋势");
  });

  it("dashboard HTML includes fallback to legacy pnl endpoint", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("/quant/api/pnl");
    expect(html).toContain("equityHistory");
  });

  it("dashboard HTML includes benchmark stats display area", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain('id="benchmarkStats"');
  });

  it("dashboard HTML displays maxDrawdownPct in summary", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("最大回撤");
    expect(html).toContain("maxDrawdownPct");
  });

  it("dashboard HTML displays winRate in summary", () => {
    const html = getDashboardHtml("http://localhost:18789");
    expect(html).toContain("胜率");
    expect(html).toContain("winRate");
  });
});

// ══════════════════════════════════════════════════════════════════
// 4. Boundary Conditions
// ══════════════════════════════════════════════════════════════════

describe("Equity Curve Analysis — Boundary Conditions", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  it("maxSnapshots=1 keeps only the latest snapshot", () => {
    const hist = new EquityHistory({ dataDir, maxSnapshots: 1 });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 110000, 10000));
    hist.addSnapshot(makeSnap("2024-01-03", 120000, 10000));

    expect(hist.count).toBe(1);
    expect(hist.getLatest()!.date).toBe("2024-01-03");
  });

  it("exactly at maxSnapshots does not prune", () => {
    const hist = new EquityHistory({ dataDir, maxSnapshots: 3 });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 110000, 10000));
    hist.addSnapshot(makeSnap("2024-01-03", 120000, 10000));

    expect(hist.count).toBe(3);
    const all = hist.getAll();
    expect(all[0]!.date).toBe("2024-01-01");
    expect(all[2]!.date).toBe("2024-01-03");
  });

  it("one over maxSnapshots prunes the oldest", () => {
    const hist = new EquityHistory({ dataDir, maxSnapshots: 3 });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 110000, 10000));
    hist.addSnapshot(makeSnap("2024-01-03", 120000, 10000));
    hist.addSnapshot(makeSnap("2024-01-04", 130000, 10000));

    expect(hist.count).toBe(3);
    const all = hist.getAll();
    expect(all[0]!.date).toBe("2024-01-02"); // 01-01 pruned
  });

  it("de-duplication via record on same date updates in-place", () => {
    const hist = new EquityHistory({ dataDir });
    hist.record(mockAccount({ totalAssets: 100000 }), mockPositions());
    hist.record(mockAccount({ totalAssets: 105000 }), mockPositions());
    hist.record(mockAccount({ totalAssets: 110000 }), mockPositions());

    expect(hist.count).toBe(1);
    expect(hist.getLatest()!.equity).toBe(110000);
  });

  it("de-duplication via addSnapshot on same date updates in-place", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-03-15", 100000, 0));
    hist.addSnapshot(makeSnap("2024-03-15", 105000, 5000));

    expect(hist.count).toBe(1);
    expect(hist.getLatest()!.equity).toBe(105000);
    expect(hist.getLatest()!.dailyPnl).toBe(5000);
  });

  it("getRange with start > end returns empty", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-05", 110000, 10000));

    const range = hist.getRange("2024-01-10", "2024-01-01");
    expect(range).toHaveLength(0);
  });

  it("getRange with no overlap returns empty", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-05", 110000, 10000));

    const range = hist.getRange("2024-06-01", "2024-06-30");
    expect(range).toHaveLength(0);
  });

  it("getRecent with limit larger than count returns all", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 110000, 10000));

    const recent = hist.getRecent(100);
    expect(recent).toHaveLength(2);
  });

  it("getRecent with limit=0 returns all (slice(-0) is full array)", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 110000, 10000));

    // Note: Array.slice(-0) === Array.slice(0) returns entire array
    const recent = hist.getRecent(0);
    expect(recent).toHaveLength(2);
  });

  it("snapshots inserted out of order are returned sorted by getAll", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-03-15", 103000, 3000));
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-02-10", 102000, 2000));

    const all = hist.getAll();
    expect(all.map((s) => s.date)).toEqual(["2024-01-01", "2024-02-10", "2024-03-15"]);
  });

  it("persistence handles large number of snapshots", async () => {
    const hist = new EquityHistory({ dataDir, maxSnapshots: 500 });
    for (let i = 1; i <= 500; i++) {
      const month = String(Math.ceil(i / 28)).padStart(2, "0");
      const day = String(((i - 1) % 28) + 1).padStart(2, "0");
      hist.addSnapshot(makeSnap(`2024-${month}-${day}`, 100000 + i * 100, i * 10));
    }
    await hist.save();

    const hist2 = new EquityHistory({ dataDir, maxSnapshots: 500 });
    await hist2.load();
    expect(hist2.count).toBe(500);
  });

  it("load handles corrupt JSON gracefully", async () => {
    const dir = tmpDir();
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(path.join(dir, "equity-history.json"), "NOT VALID JSON {{{");

    const hist = new EquityHistory({ dataDir: dir });
    await hist.load();
    expect(hist.count).toBe(0);

    await fs.rm(dir, { recursive: true, force: true });
  });

  it("load handles non-array JSON gracefully", async () => {
    const dir = tmpDir();
    await fs.mkdir(dir, { recursive: true });
    await fs.writeFile(path.join(dir, "equity-history.json"), '{"not": "an array"}');

    const hist = new EquityHistory({ dataDir: dir });
    await hist.load();
    expect(hist.count).toBe(0);

    await fs.rm(dir, { recursive: true, force: true });
  });

  it("save creates directory if it does not exist", async () => {
    const nestedDir = path.join(tmpDir(), "deep", "nested", "dir");
    const hist = new EquityHistory({ dataDir: nestedDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    await hist.save();

    const hist2 = new EquityHistory({ dataDir: nestedDir });
    await hist2.load();
    expect(hist2.count).toBe(1);

    await fs.rm(path.dirname(path.dirname(nestedDir)), { recursive: true, force: true });
  });
});

// ══════════════════════════════════════════════════════════════════
// 5. Empty & Degenerate Data Scenarios
// ══════════════════════════════════════════════════════════════════

describe("Equity Curve Analysis — Empty & Degenerate Data", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  it("getSummary on empty history returns all zeros and nulls", () => {
    const hist = new EquityHistory({ dataDir });
    const summary = hist.getSummary();

    expect(summary.totalDays).toBe(0);
    expect(summary.startEquity).toBe(0);
    expect(summary.endEquity).toBe(0);
    expect(summary.totalReturn).toBe(0);
    expect(summary.totalReturnPct).toBe(0);
    expect(summary.maxDrawdown).toBe(0);
    expect(summary.maxDrawdownPct).toBe(0);
    expect(summary.bestDay).toBeNull();
    expect(summary.worstDay).toBeNull();
    expect(summary.avgDailyPnl).toBe(0);
    expect(summary.winDays).toBe(0);
    expect(summary.lossDays).toBe(0);
    expect(summary.winRate).toBe(0);
  });

  it("getSummary with single snapshot has zero return", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 500));

    const summary = hist.getSummary();
    expect(summary.totalDays).toBe(1);
    expect(summary.totalReturn).toBe(0); // same start and end
    expect(summary.totalReturnPct).toBe(0);
    expect(summary.maxDrawdown).toBe(0);
    expect(summary.bestDay!.pnl).toBe(500);
    expect(summary.worstDay!.pnl).toBe(500); // only one day
    expect(summary.bestDay!.date).toBe(summary.worstDay!.date);
  });

  it("getAll on empty history returns empty array", () => {
    const hist = new EquityHistory({ dataDir });
    expect(hist.getAll()).toEqual([]);
  });

  it("getLatest on empty history returns null", () => {
    const hist = new EquityHistory({ dataDir });
    expect(hist.getLatest()).toBeNull();
  });

  it("getRecent on empty history returns empty array", () => {
    const hist = new EquityHistory({ dataDir });
    expect(hist.getRecent(10)).toEqual([]);
  });

  it("getRange on empty history returns empty array", () => {
    const hist = new EquityHistory({ dataDir });
    expect(hist.getRange("2024-01-01", "2024-12-31")).toEqual([]);
  });

  it("count is zero on fresh instance", () => {
    const hist = new EquityHistory({ dataDir });
    expect(hist.count).toBe(0);
  });

  it("handles zero equity without division errors", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 0, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 0, 0));

    const summary = hist.getSummary();
    // totalReturnPct: 0/0 should be 0, not NaN
    expect(summary.totalReturnPct).toBe(0);
    expect(Number.isFinite(summary.totalReturnPct)).toBe(true);
    expect(Number.isFinite(summary.maxDrawdownPct)).toBe(true);
  });

  it("handles zero starting equity with non-zero end", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot(makeSnap("2024-01-01", 0, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 100000, 100000));

    const summary = hist.getSummary();
    expect(summary.totalReturn).toBe(100000);
    // Can't compute percentage from 0 base
    expect(summary.totalReturnPct).toBe(0);
  });

  it("record with zero positions returns positionCount=0", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount(), []);
    expect(snap.positionCount).toBe(0);
  });

  it("save and load empty history works", async () => {
    const hist = new EquityHistory({ dataDir });
    await hist.save();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    expect(hist2.count).toBe(0);
  });

  it("close on empty history does not throw", async () => {
    const hist = new EquityHistory({ dataDir });
    await expect(hist.close()).resolves.not.toThrow();
  });

  it("close on clean (non-dirty) history does not write file", async () => {
    const hist = new EquityHistory({ dataDir });
    await hist.load();
    await hist.close();

    // File should not exist since no data was added
    const files = await fs.readdir(dataDir).catch(() => []);
    // dataDir may not even exist since load doesn't create it
    expect(files.length === 0 || files.length === undefined).toBeTruthy();
  });
});

// ══════════════════════════════════════════════════════════════════
// 6. Integration: EquityHistory + Summary + Persistence round-trip
// ══════════════════════════════════════════════════════════════════

describe("Equity Curve Analysis — Integration", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  it("full lifecycle: record → save → reload → getSummary produces consistent results", async () => {
    const hist = new EquityHistory({ dataDir });

    // Simulate 5 trading days
    hist.addSnapshot(makeSnap("2024-03-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-03-04", 102000, 2000));
    hist.addSnapshot(makeSnap("2024-03-05", 99000, -3000));
    hist.addSnapshot(makeSnap("2024-03-06", 101500, 2500));
    hist.addSnapshot(makeSnap("2024-03-07", 104000, 2500));

    const summaryBefore = hist.getSummary();
    await hist.save();

    // Reload from disk
    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    const summaryAfter = hist2.getSummary();

    // Summaries must match exactly
    expect(summaryAfter.totalDays).toBe(summaryBefore.totalDays);
    expect(summaryAfter.totalReturn).toBe(summaryBefore.totalReturn);
    expect(summaryAfter.totalReturnPct).toBe(summaryBefore.totalReturnPct);
    expect(summaryAfter.maxDrawdown).toBe(summaryBefore.maxDrawdown);
    expect(summaryAfter.maxDrawdownPct).toBe(summaryBefore.maxDrawdownPct);
    expect(summaryAfter.bestDay).toEqual(summaryBefore.bestDay);
    expect(summaryAfter.worstDay).toEqual(summaryBefore.worstDay);
    expect(summaryAfter.winDays).toBe(summaryBefore.winDays);
    expect(summaryAfter.lossDays).toBe(summaryBefore.lossDays);
    expect(summaryAfter.winRate).toBe(summaryBefore.winRate);
  });

  it("record + addSnapshot coexist correctly", () => {
    const hist = new EquityHistory({ dataDir });

    // Manual historical snapshots
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-01-02", 102000, 2000));

    // Live recording
    hist.record(mockAccount({ totalAssets: 105000, todayProfit: 3000 }), mockPositions(3));

    expect(hist.count).toBe(3);
    const summary = hist.getSummary();
    expect(summary.totalDays).toBe(3);
    expect(summary.endEquity).toBe(105000);
  });

  it("getRange filters correctly after save/reload", async () => {
    const hist = new EquityHistory({ dataDir });
    for (let d = 1; d <= 20; d++) {
      const day = String(d).padStart(2, "0");
      hist.addSnapshot(makeSnap(`2024-03-${day}`, 100000 + d * 500, d * 50));
    }
    await hist.save();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();

    const range = hist2.getRange("2024-03-05", "2024-03-10");
    expect(range).toHaveLength(6);
    expect(range[0]!.date).toBe("2024-03-05");
    expect(range[5]!.date).toBe("2024-03-10");
  });

  it("paper and live histories are fully independent", async () => {
    const histPaper = new EquityHistory({ dataDir, mode: "paper" });
    const histLive = new EquityHistory({ dataDir, mode: "live" });

    // Paper: 3 snapshots, ending at 110k
    histPaper.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    histPaper.addSnapshot(makeSnap("2024-01-02", 105000, 5000));
    histPaper.addSnapshot(makeSnap("2024-01-03", 110000, 5000));

    // Live: 2 snapshots, ending at 95k
    histLive.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    histLive.addSnapshot(makeSnap("2024-01-02", 95000, -5000));

    await Promise.all([histPaper.save(), histLive.save()]);

    // Reload
    const rPaper = new EquityHistory({ dataDir, mode: "paper" });
    const rLive = new EquityHistory({ dataDir, mode: "live" });
    await Promise.all([rPaper.load(), rLive.load()]);

    expect(rPaper.count).toBe(3);
    expect(rLive.count).toBe(2);

    const pSummary = rPaper.getSummary();
    const lSummary = rLive.getSummary();
    expect(pSummary.totalReturn).toBe(10000);
    expect(lSummary.totalReturn).toBe(-5000);
  });

  it("pruning + save + reload maintains correct count", async () => {
    const hist = new EquityHistory({ dataDir, maxSnapshots: 5 });
    for (let d = 1; d <= 10; d++) {
      const day = String(d).padStart(2, "0");
      hist.addSnapshot(makeSnap(`2024-01-${day}`, 100000 + d * 1000, 1000));
    }

    expect(hist.count).toBe(5);
    await hist.save();

    const hist2 = new EquityHistory({ dataDir, maxSnapshots: 5 });
    await hist2.load();
    expect(hist2.count).toBe(5);

    const all = hist2.getAll();
    expect(all[0]!.date).toBe("2024-01-06"); // oldest 5 pruned
    expect(all[4]!.date).toBe("2024-01-10");
  });

  it("getSummary is consistent across getAll ordering", () => {
    const hist = new EquityHistory({ dataDir });

    // Add snapshots out of chronological order
    hist.addSnapshot(makeSnap("2024-06-15", 108000, 3000));
    hist.addSnapshot(makeSnap("2024-01-01", 100000, 0));
    hist.addSnapshot(makeSnap("2024-03-10", 105000, 5000));

    const summary = hist.getSummary();
    // Sorted: 01-01(100k) → 03-10(105k) → 06-15(108k)
    expect(summary.startEquity).toBe(100000);
    expect(summary.endEquity).toBe(108000);
    expect(summary.totalReturn).toBe(8000);
    expect(summary.totalReturnPct).toBeCloseTo(8.0);
  });
});
