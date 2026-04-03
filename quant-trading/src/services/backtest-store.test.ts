import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { BacktestResult } from "../types.js";
import { BacktestStore } from "./backtest-store.js";
import type { BacktestIndexEntry, BacktestRecord } from "./backtest-store.js";

// ── Helpers ──────────────────────────────────────────────────────

function makeFakeResult(overrides?: Partial<BacktestResult>): BacktestResult {
  return {
    strategyId: "dual-ma",
    symbols: ["600519"],
    startDate: "2024-01-01",
    endDate: "2024-12-31",
    initialCash: 100000,
    finalValue: 120000,
    totalReturn: 20,
    annualizedReturn: 20,
    maxDrawdown: 5,
    sharpeRatio: 1.5,
    winRate: 55,
    totalTrades: 30,
    profitFactor: 1.8,
    totalFees: 150,
    signals: [],
    equityCurve: [
      { date: "2024-01-01", value: 100000 },
      { date: "2024-12-31", value: 120000 },
    ],
    ...overrides,
  };
}

let testDir: string;
let store: BacktestStore;

beforeEach(() => {
  testDir = join(tmpdir(), `bt-store-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  store = new BacktestStore({ dataDir: testDir });
  store.start();
});

afterEach(() => {
  try {
    if (existsSync(testDir)) {
      rmSync(testDir, { recursive: true, force: true });
    }
  } catch {
    // best-effort cleanup
  }
});

// ── Basic CRUD ───────────────────────────────────────────────────

describe("BacktestStore", () => {
  describe("save and retrieve", () => {
    it("should save a result and return an id", async () => {
      const id = await store.save(makeFakeResult());
      expect(id).toMatch(/^bt_/);
      expect(store.size).toBe(1);
    });

    it("should retrieve full result by id", async () => {
      const result = makeFakeResult();
      const id = await store.save(result, { tags: ["test"], notes: "test run" });
      const record = store.getFullResult(id);
      expect(record).not.toBeNull();
      expect(record!.id).toBe(id);
      expect(record!.result.strategyId).toBe("dual-ma");
      expect(record!.tags).toEqual(["test"]);
      expect(record!.notes).toBe("test run");
      expect(record!.starred).toBe(false);
    });

    it("should retrieve lightweight entry by id", async () => {
      const id = await store.save(makeFakeResult());
      const entry = store.getEntry(id);
      expect(entry).toBeDefined();
      expect(entry!.strategyId).toBe("dual-ma");
      expect(entry!.totalReturn).toBe(20);
      // Index entries should NOT have signals/equityCurve
      expect((entry as unknown as Record<string, unknown>).signals).toBeUndefined();
      expect((entry as unknown as Record<string, unknown>).equityCurve).toBeUndefined();
    });

    it("should return null for non-existent id", () => {
      expect(store.getFullResult("bt_nonexistent")).toBeNull();
      expect(store.getEntry("bt_nonexistent")).toBeUndefined();
    });
  });

  describe("delete", () => {
    it("should delete a result", async () => {
      const id = await store.save(makeFakeResult());
      expect(store.size).toBe(1);
      expect(await store.delete(id)).toBe(true);
      expect(store.size).toBe(0);
      expect(store.getFullResult(id)).toBeNull();
    });

    it("should return false for non-existent id", async () => {
      expect(await store.delete("bt_nonexistent")).toBe(false);
    });
  });

  describe("tags and notes", () => {
    it("should set tags", async () => {
      const id = await store.save(makeFakeResult());
      expect(await store.setTags(id, ["a", "b"])).toBe(true);
      expect(store.getEntry(id)!.tags).toEqual(["a", "b"]);
      // Also persisted in full record
      expect(store.getFullResult(id)!.tags).toEqual(["a", "b"]);
    });

    it("should toggle star", async () => {
      const id = await store.save(makeFakeResult());
      expect(store.getEntry(id)!.starred).toBe(false);
      expect(await store.toggleStar(id)).toBe(true);
      expect(store.getEntry(id)!.starred).toBe(true);
      expect(await store.toggleStar(id)).toBe(true);
      expect(store.getEntry(id)!.starred).toBe(false);
    });

    it("should set notes", async () => {
      const id = await store.save(makeFakeResult());
      expect(await store.setNotes(id, "great run")).toBe(true);
      expect(store.getEntry(id)!.notes).toBe("great run");
      expect(store.getFullResult(id)!.notes).toBe("great run");
    });

    it("should return false for non-existent ids", async () => {
      expect(await store.setTags("nope", [])).toBe(false);
      expect(await store.toggleStar("nope")).toBe(false);
      expect(await store.setNotes("nope", "x")).toBe(false);
    });
  });

  // ── Querying ──────────────────────────────────────────────────

  describe("query", () => {
    beforeEach(async () => {
      // Use fake timers to guarantee distinct createdAt timestamps
      vi.useFakeTimers({ now: new Date("2026-01-01T00:00:00Z") });
      await store.save(makeFakeResult({ strategyId: "dual-ma", totalReturn: 20 }), {
        tags: ["tag-a"],
        engine: "builtin",
      });
      vi.advanceTimersByTime(1000);
      await store.save(
        makeFakeResult({ strategyId: "momentum", totalReturn: 15, symbols: ["000001"] }),
        {
          tags: ["tag-b"],
          engine: "backtrader",
        },
      );
      vi.advanceTimersByTime(1000);
      await store.save(makeFakeResult({ strategyId: "dual-ma", totalReturn: -5 }), {
        tags: ["tag-a", "tag-c"],
        engine: "builtin",
      });
      vi.useRealTimers();
    });

    it("should return all by default (newest first)", () => {
      const results = store.query();
      expect(results).toHaveLength(3);
      // Default sort: createdAt desc
      expect(results[0].strategyId).toBe("dual-ma"); // 3rd saved = newest
      expect(results[0].totalReturn).toBe(-5);
    });

    it("should filter by strategyId", () => {
      const results = store.query({ strategyId: "dual-ma" });
      expect(results).toHaveLength(2);
      for (const r of results) expect(r.strategyId).toBe("dual-ma");
    });

    it("should filter by symbol", () => {
      const results = store.query({ symbol: "000001" });
      expect(results).toHaveLength(1);
      expect(results[0].strategyId).toBe("momentum");
    });

    it("should filter by engine", () => {
      const results = store.query({ engine: "backtrader" });
      expect(results).toHaveLength(1);
    });

    it("should filter by tag", () => {
      const results = store.query({ tag: "tag-a" });
      expect(results).toHaveLength(2);
    });

    it("should sort by totalReturn desc", () => {
      const results = store.query({ sortBy: "totalReturn", sortDir: "desc" });
      expect(results[0].totalReturn).toBe(20);
      expect(results[2].totalReturn).toBe(-5);
    });

    it("should sort by sharpeRatio asc", () => {
      const results = store.query({ sortBy: "sharpeRatio", sortDir: "asc" });
      expect(results[0].sharpeRatio).toBeLessThanOrEqual(results[1].sharpeRatio);
    });

    it("should support pagination", () => {
      const page1 = store.query({ limit: 2, offset: 0 });
      const page2 = store.query({ limit: 2, offset: 2 });
      expect(page1).toHaveLength(2);
      expect(page2).toHaveLength(1);
    });

    it("should filter by starred", async () => {
      const all = store.query();
      await store.toggleStar(all[0].id); // star the newest
      const starred = store.query({ starred: true });
      expect(starred).toHaveLength(1);
      expect(starred[0].id).toBe(all[0].id);
    });

    it("should filter by date range", () => {
      const all = store.query({ sortBy: "createdAt", sortDir: "asc" });
      // Use the middle entry's createdAt as boundary
      const middleTs = all[1].createdAt;
      const after = store.query({ after: middleTs });
      expect(after.length).toBeGreaterThanOrEqual(1); // at least the middle + later
    });
  });

  // ── Comparison ────────────────────────────────────────────────

  describe("compare", () => {
    it("should compare two or more runs", async () => {
      const id1 = await store.save(
        makeFakeResult({ totalReturn: 30, sharpeRatio: 2.0, maxDrawdown: 3 }),
      );
      const id2 = await store.save(
        makeFakeResult({ totalReturn: 10, sharpeRatio: 0.8, maxDrawdown: 15 }),
      );

      const cmp = store.compare([id1, id2]);
      expect(cmp).not.toBeNull();
      expect(cmp!.ids).toEqual([id1, id2]);
      expect(cmp!.entries).toHaveLength(2);

      // totalReturn: best = id1 (30), worst = id2 (10)
      expect(cmp!.metrics.totalReturn.best.id).toBe(id1);
      expect(cmp!.metrics.totalReturn.worst.id).toBe(id2);
      expect(cmp!.metrics.totalReturn.avg).toBe(20);

      // maxDrawdown: best = id1 (3, lowest), worst = id2 (15)
      expect(cmp!.metrics.maxDrawdown.best.id).toBe(id1);
      expect(cmp!.metrics.maxDrawdown.worst.id).toBe(id2);
    });

    it("should return null for less than 2 ids", async () => {
      const id = await store.save(makeFakeResult());
      expect(store.compare([id])).toBeNull();
      expect(store.compare([])).toBeNull();
    });

    it("should include profitFactor comparison", async () => {
      const id1 = await store.save(makeFakeResult({ profitFactor: 2.5 }));
      const id2 = await store.save(makeFakeResult({ profitFactor: 1.2 }));
      const cmp = store.compare([id1, id2])!;
      expect(cmp.metrics.profitFactor.best.value).toBe(2.5);
      expect(cmp.metrics.profitFactor.worst.value).toBe(1.2);
    });

    it("should return null when all IDs are invalid/deleted", async () => {
      const id1 = await store.save(makeFakeResult());
      const id2 = await store.save(makeFakeResult());
      await store.delete(id1);
      await store.delete(id2);
      // Entries still exist in the passed IDs but records are deleted
      expect(store.compare([id1, id2])).toBeNull();
    });

    it("should generate unique IDs across calls", async () => {
      const id1 = await store.save(makeFakeResult());
      const id2 = await store.save(makeFakeResult());
      expect(id1).not.toBe(id2);
      expect(id1).toMatch(/^bt_/);
      expect(id2).toMatch(/^bt_/);
    });
  });

  // ── Performance summary ───────────────────────────────────────

  describe("getPerformanceSummary", () => {
    it("should return empty summary for no data", () => {
      const summary = store.getPerformanceSummary();
      expect(summary.totalRuns).toBe(0);
      expect(summary.avgReturn).toBe(0);
    });

    it("should compute aggregate metrics", async () => {
      await store.save(
        makeFakeResult({
          strategyId: "dual-ma",
          totalReturn: 20,
          sharpeRatio: 1.5,
          maxDrawdown: 5,
          winRate: 60,
        }),
      );
      await store.save(
        makeFakeResult({
          strategyId: "dual-ma",
          totalReturn: -10,
          sharpeRatio: 0.5,
          maxDrawdown: 15,
          winRate: 40,
        }),
      );
      await store.save(
        makeFakeResult({
          strategyId: "momentum",
          totalReturn: 30,
          sharpeRatio: 2.0,
          maxDrawdown: 8,
          winRate: 65,
        }),
      );

      const summary = store.getPerformanceSummary();
      expect(summary.totalRuns).toBe(3);
      expect(summary.profitableRuns).toBe(2);
      expect(summary.avgReturn).toBeCloseTo((20 - 10 + 30) / 3);
      expect(summary.avgSharpe).toBeCloseTo((1.5 + 0.5 + 2.0) / 3);
      expect(summary.avgMaxDrawdown).toBeCloseTo((5 + 15 + 8) / 3);
      expect(summary.bestRun!.totalReturn).toBe(30);
      expect(summary.worstRun!.totalReturn).toBe(-10);
    });

    it("should filter by strategyId", async () => {
      await store.save(makeFakeResult({ strategyId: "dual-ma", totalReturn: 20 }));
      await store.save(makeFakeResult({ strategyId: "momentum", totalReturn: 30 }));

      const summary = store.getPerformanceSummary("dual-ma");
      expect(summary.totalRuns).toBe(1);
      expect(summary.avgReturn).toBe(20);
    });

    it("should compute per-strategy breakdown", async () => {
      await store.save(makeFakeResult({ strategyId: "dual-ma", totalReturn: 20 }));
      await store.save(makeFakeResult({ strategyId: "dual-ma", totalReturn: -10 }));
      await store.save(makeFakeResult({ strategyId: "momentum", totalReturn: 30 }));

      const summary = store.getPerformanceSummary();
      expect(summary.byStrategy["dual-ma"].runs).toBe(2);
      expect(summary.byStrategy["dual-ma"].avgReturn).toBeCloseTo(5); // (20 + -10) / 2
      expect(summary.byStrategy["dual-ma"].bestReturn).toBe(20);
      expect(summary.byStrategy["dual-ma"].worstReturn).toBe(-10);
      expect(summary.byStrategy["momentum"].runs).toBe(1);
    });
  });

  // ── Persistence ───────────────────────────────────────────────

  describe("persistence", () => {
    it("should persist and reload index", async () => {
      const id = await store.save(makeFakeResult());

      // Create a new store pointing to the same dir
      const store2 = new BacktestStore({ dataDir: testDir });
      store2.start();
      expect(store2.size).toBe(1);
      expect(store2.getEntry(id)!.strategyId).toBe("dual-ma");
    });

    it("should persist and reload full results", async () => {
      const id = await store.save(makeFakeResult(), { notes: "persisted" });

      const store2 = new BacktestStore({ dataDir: testDir });
      store2.start();
      const record = store2.getFullResult(id);
      expect(record).not.toBeNull();
      expect(record!.notes).toBe("persisted");
      expect(record!.result.equityCurve).toHaveLength(2);
    });

    it("should delete result file on delete", async () => {
      const id = await store.save(makeFakeResult());
      const filePath = join(testDir, `${id}.json`);
      expect(existsSync(filePath)).toBe(true);

      await store.delete(id);
      expect(existsSync(filePath)).toBe(false);
    });

    it("should handle corrupt index gracefully", () => {
      writeFileSync(join(testDir, "index.json"), "NOT JSON", "utf-8");
      const store2 = new BacktestStore({ dataDir: testDir });
      store2.start();
      expect(store2.size).toBe(0); // Falls back to empty
    });
  });

  // ── Pruning ───────────────────────────────────────────────────

  describe("pruning", () => {
    it("should prune oldest non-starred results beyond maxResults", async () => {
      const smallStore = new BacktestStore({ dataDir: testDir, maxResults: 3 });
      smallStore.start();

      const id1 = await smallStore.save(makeFakeResult({ totalReturn: 1 }));
      const id2 = await smallStore.save(makeFakeResult({ totalReturn: 2 }));
      await smallStore.toggleStar(id1); // star the first
      const _id3 = await smallStore.save(makeFakeResult({ totalReturn: 3 }));
      const _id4 = await smallStore.save(makeFakeResult({ totalReturn: 4 })); // triggers prune

      // id1 is starred so it survives; id2 (oldest non-starred) should be removed
      expect(smallStore.size).toBe(3);
      expect(smallStore.getEntry(id1)).toBeDefined(); // starred = kept
      expect(smallStore.getEntry(id2)).toBeUndefined(); // oldest non-starred = pruned
    });

    it("should not remove starred results during pruning", async () => {
      const smallStore = new BacktestStore({ dataDir: testDir, maxResults: 2 });
      smallStore.start();

      const id1 = await smallStore.save(makeFakeResult());
      await smallStore.toggleStar(id1);
      const id2 = await smallStore.save(makeFakeResult());
      await smallStore.toggleStar(id2);
      const id3 = await smallStore.save(makeFakeResult()); // prune triggered but all older are starred

      // All 3 should remain since the 2 older are starred
      expect(smallStore.size).toBe(3);
      expect(smallStore.getEntry(id1)).toBeDefined();
      expect(smallStore.getEntry(id2)).toBeDefined();
      expect(smallStore.getEntry(id3)).toBeDefined();
    });
  });
});
