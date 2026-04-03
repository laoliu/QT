import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BrokerAccount, LivePosition } from "../types.js";
import { EquityHistory } from "./equity-history.js";

function tmpDir(): string {
  return path.join(
    os.tmpdir(),
    `eq-hist-test-${Date.now()}-${Math.random().toString(36).slice(2)}`,
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

describe("EquityHistory", () => {
  let dataDir: string;

  beforeEach(() => {
    dataDir = tmpDir();
  });

  afterEach(async () => {
    await fs.rm(dataDir, { recursive: true, force: true });
  });

  // ── Basic operations ──────────────────────────────────────

  it("records a snapshot from account + positions", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount(), mockPositions());
    expect(snap.equity).toBe(100000);
    expect(snap.cash).toBe(80000);
    expect(snap.marketValue).toBe(20000);
    expect(snap.dailyPnl).toBe(500);
    expect(snap.positionCount).toBe(2);
    expect(snap.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    expect(hist.count).toBe(1);
  });

  it("de-duplicates same-date snapshots (last write wins)", () => {
    const hist = new EquityHistory({ dataDir });
    hist.record(mockAccount({ totalAssets: 100000 }), mockPositions());
    hist.record(mockAccount({ totalAssets: 105000 }), mockPositions());
    expect(hist.count).toBe(1);
    expect(hist.getLatest()!.equity).toBe(105000);
  });

  it("stores benchmark value when provided", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount(), mockPositions(), 3500.5);
    expect(snap.benchmark).toBe(3500.5);
  });

  it("uses todayProfit=0 when undefined and no previous snapshot", () => {
    const hist = new EquityHistory({ dataDir });
    const snap = hist.record(mockAccount({ todayProfit: undefined }), mockPositions());
    expect(snap.dailyPnl).toBe(0);
  });

  it("computes dailyPnl from equity delta when todayProfit is undefined", () => {
    const hist = new EquityHistory({ dataDir });
    // Add a previous-day snapshot manually
    hist.addSnapshot({
      date: "2024-01-01",
      equity: 100000,
      cash: 80000,
      marketValue: 20000,
      dailyPnl: 0,
      positionCount: 2,
    });
    // Record today with totalAssets=105000 and no todayProfit
    const snap = hist.record(
      mockAccount({ totalAssets: 105000, todayProfit: undefined }),
      mockPositions(),
    );
    expect(snap.dailyPnl).toBe(5000); // 105000 - 100000
  });

  // ── Retrieval ─────────────────────────────────────────────

  it("getAll returns sorted snapshots", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot({
      date: "2024-03-15",
      equity: 102000,
      cash: 82000,
      marketValue: 20000,
      dailyPnl: 200,
      positionCount: 1,
    });
    hist.addSnapshot({
      date: "2024-03-13",
      equity: 100000,
      cash: 80000,
      marketValue: 20000,
      dailyPnl: 0,
      positionCount: 1,
    });
    hist.addSnapshot({
      date: "2024-03-14",
      equity: 101000,
      cash: 81000,
      marketValue: 20000,
      dailyPnl: 100,
      positionCount: 1,
    });
    const all = hist.getAll();
    expect(all.map((s) => s.date)).toEqual(["2024-03-13", "2024-03-14", "2024-03-15"]);
  });

  it("getRange filters by date range", () => {
    const hist = new EquityHistory({ dataDir });
    for (let d = 10; d <= 20; d++) {
      hist.addSnapshot({
        date: `2024-03-${d}`,
        equity: 100000 + d * 100,
        cash: 80000,
        marketValue: 20000,
        dailyPnl: d * 10,
        positionCount: 1,
      });
    }
    const range = hist.getRange("2024-03-13", "2024-03-16");
    expect(range).toHaveLength(4);
    expect(range[0]!.date).toBe("2024-03-13");
    expect(range[3]!.date).toBe("2024-03-16");
  });

  it("getRecent returns latest N snapshots", () => {
    const hist = new EquityHistory({ dataDir });
    for (let d = 1; d <= 10; d++) {
      hist.addSnapshot({
        date: `2024-03-${String(d).padStart(2, "0")}`,
        equity: 100000 + d * 100,
        cash: 80000,
        marketValue: 20000,
        dailyPnl: d * 10,
        positionCount: 1,
      });
    }
    const recent = hist.getRecent(3);
    expect(recent).toHaveLength(3);
    expect(recent[0]!.date).toBe("2024-03-08");
    expect(recent[2]!.date).toBe("2024-03-10");
  });

  it("getLatest returns null when empty", () => {
    const hist = new EquityHistory({ dataDir });
    expect(hist.getLatest()).toBeNull();
  });

  // ── Pruning ───────────────────────────────────────────────

  it("prunes oldest when maxSnapshots exceeded", () => {
    const hist = new EquityHistory({ dataDir, maxSnapshots: 5 });
    for (let d = 1; d <= 8; d++) {
      hist.addSnapshot({
        date: `2024-01-${String(d).padStart(2, "0")}`,
        equity: 100000 + d * 100,
        cash: 80000,
        marketValue: 20000,
        dailyPnl: 0,
        positionCount: 0,
      });
    }
    expect(hist.count).toBe(5);
    const all = hist.getAll();
    expect(all[0]!.date).toBe("2024-01-04"); // oldest 3 pruned
  });

  // ── Persistence ───────────────────────────────────────────

  it("save and load round-trips correctly", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.record(mockAccount({ totalAssets: 105000 }), mockPositions(3), 3500);
    await hist.save();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    expect(hist2.count).toBe(1);
    const snap = hist2.getLatest()!;
    expect(snap.equity).toBe(105000);
    expect(snap.positionCount).toBe(3);
    expect(snap.benchmark).toBe(3500);
  });

  it("load handles missing file gracefully", async () => {
    const hist = new EquityHistory({ dataDir: tmpDir() });
    await hist.load();
    expect(hist.count).toBe(0);
  });

  // ── Auto-save ─────────────────────────────────────────────

  it("auto-save flushes dirty state", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.record(mockAccount(), mockPositions());
    // Use a very short interval and real timers so file I/O works
    hist.startAutoSave(50);
    await new Promise((r) => setTimeout(r, 200));
    hist.stopAutoSave();

    // Verify saved
    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    expect(hist2.count).toBe(1);
  });

  // ── Performance summary ───────────────────────────────────

  it("getSummary returns correct stats", () => {
    const hist = new EquityHistory({ dataDir });
    hist.addSnapshot({
      date: "2024-01-01",
      equity: 100000,
      cash: 80000,
      marketValue: 20000,
      dailyPnl: 0,
      positionCount: 2,
    });
    hist.addSnapshot({
      date: "2024-01-02",
      equity: 102000,
      cash: 82000,
      marketValue: 20000,
      dailyPnl: 2000,
      positionCount: 2,
    });
    hist.addSnapshot({
      date: "2024-01-03",
      equity: 99000,
      cash: 79000,
      marketValue: 20000,
      dailyPnl: -3000,
      positionCount: 2,
    });
    hist.addSnapshot({
      date: "2024-01-04",
      equity: 103000,
      cash: 83000,
      marketValue: 20000,
      dailyPnl: 4000,
      positionCount: 2,
    });

    const summary = hist.getSummary();
    expect(summary.totalDays).toBe(4);
    expect(summary.startEquity).toBe(100000);
    expect(summary.endEquity).toBe(103000);
    expect(summary.totalReturn).toBe(3000);
    expect(summary.totalReturnPct).toBeCloseTo(3.0);
    // Max drawdown: peak at 102000, trough at 99000 = 3000
    expect(summary.maxDrawdown).toBe(3000);
    expect(summary.maxDrawdownPct).toBeCloseTo(2.94, 1);
    expect(summary.bestDay!.pnl).toBe(4000);
    expect(summary.worstDay!.pnl).toBe(-3000);
    expect(summary.winDays).toBe(2);
    expect(summary.lossDays).toBe(1);
    expect(summary.winRate).toBeCloseTo(66.67, 0);
  });

  it("getSummary handles empty history", () => {
    const hist = new EquityHistory({ dataDir });
    const summary = hist.getSummary();
    expect(summary.totalDays).toBe(0);
    expect(summary.totalReturn).toBe(0);
    expect(summary.bestDay).toBeNull();
  });

  // ── Close ─────────────────────────────────────────────────

  it("close stops auto-save and flushes", async () => {
    const hist = new EquityHistory({ dataDir });
    hist.record(mockAccount(), mockPositions());
    hist.startAutoSave(60000);
    await hist.close();

    const hist2 = new EquityHistory({ dataDir });
    await hist2.load();
    expect(hist2.count).toBe(1);
  });
});
