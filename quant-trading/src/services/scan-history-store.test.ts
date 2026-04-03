import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { ScanStockEntry } from "./scan-history-store.js";
import { ScanHistoryStore } from "./scan-history-store.js";

// ── Helpers ──────────────────────────────────────────────────────

function makeStocks(count: number, prefix = "6005"): ScanStockEntry[] {
  return Array.from({ length: count }, (_, i) => ({
    code: `${prefix}${String(i).padStart(2, "0")}`,
    name: `Stock${i}`,
    score: 5 + (i % 5),
    rank: i + 1,
    factors: { pe: 10 + i, pb: 1.5 + i * 0.1 },
    signals: i % 2 === 0 ? ["bullish"] : [],
  }));
}

let testDir: string;
let store: ScanHistoryStore;

beforeEach(() => {
  testDir = join(tmpdir(), `scan-store-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  store = new ScanHistoryStore({ dataDir: testDir });
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

describe("ScanHistoryStore", () => {
  describe("save and retrieve", () => {
    it("should save a scan and return an id", async () => {
      const id = await store.save("fscore", "all", "cn_stock", 5000, makeStocks(10));
      expect(id).toMatch(/^scan_/);
      expect(store.size).toBe(1);
    });

    it("should retrieve full result by id", async () => {
      const stocks = makeStocks(5);
      const id = await store.save("fscore", "hs300", "cn_stock", 300, stocks, {
        tags: ["daily"],
        notes: "morning scan",
      });
      const record = store.getFullResult(id);
      expect(record).not.toBeNull();
      expect(record!.id).toBe(id);
      expect(record!.screenerId).toBe("fscore");
      expect(record!.universe).toBe("hs300");
      expect(record!.market).toBe("cn_stock");
      expect(record!.totalScreened).toBe(300);
      expect(record!.resultCount).toBe(5);
      expect(record!.results).toHaveLength(5);
      expect(record!.results[0].code).toBe("600500");
      expect(record!.tags).toEqual(["daily"]);
      expect(record!.notes).toBe("morning scan");
      expect(record!.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    });

    it("should retrieve lightweight index entry by id", async () => {
      const id = await store.save("canslim", "zz500", "cn_stock", 500, makeStocks(15));
      const entry = store.getEntry(id);
      expect(entry).toBeDefined();
      expect(entry!.id).toBe(id);
      expect(entry!.screenerId).toBe("canslim");
      expect(entry!.universe).toBe("zz500");
      expect(entry!.resultCount).toBe(15);
      // topCodes should have at most 10
      expect(entry!.topCodes).toHaveLength(10);
      expect(entry!.topCodes[0]).toBe("600500");
    });

    it("should return null for non-existent id", () => {
      expect(store.getFullResult("scan_nonexist")).toBeNull();
      expect(store.getEntry("scan_nonexist")).toBeUndefined();
    });
  });

  describe("delete", () => {
    it("should delete a scan record", async () => {
      const id = await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      expect(store.size).toBe(1);
      const deleted = await store.delete(id);
      expect(deleted).toBe(true);
      expect(store.size).toBe(0);
      expect(store.getFullResult(id)).toBeNull();
    });

    it("should return false for non-existent delete", async () => {
      const deleted = await store.delete("scan_nonexist");
      expect(deleted).toBe(false);
    });
  });

  // ── Query ──────────────────────────────────────────────────────

  describe("query", () => {
    it("should return all entries when no filter", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));
      await store.save("canslim", "hs300", "cn_stock", 300, makeStocks(3));
      const results = store.query();
      expect(results).toHaveLength(2);
    });

    it("should filter by screenerId", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));
      await store.save("canslim", "hs300", "cn_stock", 300, makeStocks(3));
      await store.save("fscore", "zz500", "cn_stock", 500, makeStocks(8));
      const results = store.query({ screenerId: "fscore" });
      expect(results).toHaveLength(2);
      expect(results.every((r) => r.screenerId === "fscore")).toBe(true);
    });

    it("should filter by universe", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));
      await store.save("fscore", "hs300", "cn_stock", 300, makeStocks(3));
      const results = store.query({ universe: "hs300" });
      expect(results).toHaveLength(1);
      expect(results[0].universe).toBe("hs300");
    });

    it("should filter by tag", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5), {
        tags: ["daily", "morning"],
      });
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3), {
        tags: ["weekly"],
      });
      const results = store.query({ tag: "daily" });
      expect(results).toHaveLength(1);
    });

    it("should filter by date", async () => {
      // Save two — both will have today's date
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      const today = new Date().toISOString().slice(0, 10);
      const results = store.query({ date: today });
      expect(results).toHaveLength(2);
      // Non-matching date
      const results2 = store.query({ date: "2000-01-01" });
      expect(results2).toHaveLength(0);
    });

    it("should sort by resultCount ascending", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(10));
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(7));
      const results = store.query({ sortBy: "resultCount", sortDir: "asc" });
      expect(results.map((r) => r.resultCount)).toEqual([3, 7, 10]);
    });

    it("should respect limit and offset", async () => {
      for (let i = 0; i < 10; i++) {
        await store.save("fscore", "all", "cn_stock", 5000, makeStocks(i + 1));
      }
      const page1 = store.query({ limit: 3, offset: 0, sortBy: "createdAt", sortDir: "asc" });
      expect(page1).toHaveLength(3);
      const page2 = store.query({ limit: 3, offset: 3, sortBy: "createdAt", sortDir: "asc" });
      expect(page2).toHaveLength(3);
      // No overlap
      expect(page1[0].id).not.toBe(page2[0].id);
    });
  });

  // ── getLatestPair ───────────────────────────────────────────────

  describe("getLatestPair", () => {
    it("should return latest two scans for a screener", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      await store.save("canslim", "all", "cn_stock", 5000, makeStocks(2));
      // Add a small delay so timestamps differ
      await new Promise((r) => setTimeout(r, 5));
      const id2 = await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));

      const pair = store.getLatestPair("fscore");
      expect(pair).not.toBeNull();
      // compareId is the newest fscore scan
      expect(pair!.compareId).toBe(id2);
      // baseId is the older fscore scan
      expect(pair!.baseId).not.toBe(id2);
    });

    it("should return null when fewer than 2 scans exist", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      expect(store.getLatestPair("fscore")).toBeNull();
    });

    it("should return null with no scans", () => {
      expect(store.getLatestPair()).toBeNull();
    });

    it("should return latest two across all screeners when no screenerId", async () => {
      const id1 = await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      await new Promise((r) => setTimeout(r, 5));
      const id2 = await store.save("canslim", "all", "cn_stock", 5000, makeStocks(5));

      const pair = store.getLatestPair();
      expect(pair).not.toBeNull();
      expect(pair!.baseId).toBe(id1);
      expect(pair!.compareId).toBe(id2);
    });
  });

  // ── Stock frequency ────────────────────────────────────────────

  describe("getStockFrequency", () => {
    it("should track stock appearances across scans", async () => {
      // Scan1: stocks 00,01,02
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      // Scan2: stocks 00,01,02,03,04 (overlap on 00,01,02)
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));

      const freq = store.getStockFrequency();
      // 600500, 600501, 600502 should have 2 appearances
      const top = freq.find((f) => f.code === "600500");
      expect(top).toBeDefined();
      expect(top!.appearances).toBe(2);

      // 600503, 600504 should have 1 appearance
      const bottom = freq.find((f) => f.code === "600503");
      expect(bottom).toBeDefined();
      expect(bottom!.appearances).toBe(1);
    });

    it("should respect minAppearances filter", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));

      const freq = store.getStockFrequency({ minAppearances: 2 });
      expect(freq).toHaveLength(3); // 600500, 600501, 600502
      expect(freq.every((f) => f.appearances >= 2)).toBe(true);
    });

    it("should filter by screenerId", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      await store.save("canslim", "all", "cn_stock", 5000, makeStocks(5));

      const freq = store.getStockFrequency({ screenerId: "fscore" });
      // Only 3 stocks from the fscore scan
      expect(freq).toHaveLength(3);
      expect(freq.every((f) => f.appearances === 1)).toBe(true);
    });

    it("should compute average and latest scores", async () => {
      // Two scans with same stocks but different scores
      const stocks1: ScanStockEntry[] = [
        { code: "600500", name: "A", score: 6 },
        { code: "600501", name: "B", score: 8 },
      ];
      const stocks2: ScanStockEntry[] = [
        { code: "600500", name: "A", score: 10 },
        { code: "600501", name: "B", score: 4 },
      ];
      await store.save("fscore", "all", "cn_stock", 5000, stocks1);
      await store.save("fscore", "all", "cn_stock", 5000, stocks2);

      const freq = store.getStockFrequency();
      const a = freq.find((f) => f.code === "600500")!;
      expect(a.avgScore).toBe(8); // (6+10)/2
      expect(a.latestScore).toBe(10);

      const b = freq.find((f) => f.code === "600501")!;
      expect(b.avgScore).toBe(6); // (8+4)/2
      expect(b.latestScore).toBe(4);
    });

    it("should respect limit", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(20));
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(20));
      const freq = store.getStockFrequency({ limit: 5 });
      expect(freq).toHaveLength(5);
    });
  });

  // ── Compare ────────────────────────────────────────────────────

  describe("compare", () => {
    it("should compare two scans showing overlap/added/removed", async () => {
      // Base: stocks 0-4
      const base = makeStocks(5);
      const baseId = await store.save("fscore", "all", "cn_stock", 5000, base);

      // Compare: stocks 2-6 (overlap on 2,3,4; removed 0,1; added 5,6—wait, prefix 6005)
      // stocks 2-6 means codes 600502..600506
      const compare: ScanStockEntry[] = [
        { code: "600502", name: "Stock2", score: 9 },
        { code: "600503", name: "Stock3", score: 7 },
        { code: "600504", name: "Stock4", score: 6 },
        { code: "600505", name: "Stock5", score: 5 },
        { code: "600506", name: "Stock6", score: 4 },
      ];
      const compareId = await store.save("fscore", "all", "cn_stock", 5000, compare);

      const result = store.compare(baseId, compareId);
      expect(result).not.toBeNull();
      expect(result!.overlapCount).toBe(3); // 600502, 600503, 600504
      expect(result!.removedCount).toBe(2); // 600500, 600501
      expect(result!.addedCount).toBe(2); // 600505, 600506

      // Verify overlap has score deltas
      const stock02 = result!.overlap.find((o) => o.code === "600502");
      expect(stock02).toBeDefined();
      expect(stock02!.compareScore).toBe(9);
    });

    it("should return null for non-existent ids", async () => {
      const id = await store.save("fscore", "all", "cn_stock", 5000, makeStocks(3));
      expect(store.compare(id, "scan_nonexist")).toBeNull();
      expect(store.compare("scan_nonexist", id)).toBeNull();
    });
  });

  // ── Persistence ────────────────────────────────────────────────

  describe("persistence", () => {
    it("should persist and reload index across restarts", async () => {
      await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));
      await store.save("canslim", "hs300", "cn_stock", 300, makeStocks(3));
      expect(store.size).toBe(2);

      // Create a new store pointing at same dir
      const store2 = new ScanHistoryStore({ dataDir: testDir });
      store2.start();
      expect(store2.size).toBe(2);

      // Verify can load full results
      const entries = store2.query();
      const full = store2.getFullResult(entries[0].id);
      expect(full).not.toBeNull();
      expect(full!.results.length).toBeGreaterThan(0);
    });

    it("should rebuild index from record files if index is missing", async () => {
      const id1 = await store.save("fscore", "all", "cn_stock", 5000, makeStocks(5));
      await store.save("canslim", "hs300", "cn_stock", 300, makeStocks(3));

      // Delete the _index.json file
      const { unlinkSync } = await import("node:fs");
      unlinkSync(join(testDir, "_index.json"));

      // New store should rebuild from record files
      const store2 = new ScanHistoryStore({ dataDir: testDir });
      store2.start();
      expect(store2.size).toBe(2);
      expect(store2.getFullResult(id1)).not.toBeNull();
    });
  });

  // ── Pruning ────────────────────────────────────────────────────

  describe("pruning", () => {
    it("should prune oldest entries when exceeding maxResults", async () => {
      const smallStore = new ScanHistoryStore({
        dataDir: testDir,
        maxResults: 5,
      });
      smallStore.start();

      const ids: string[] = [];
      for (let i = 0; i < 8; i++) {
        ids.push(await smallStore.save("fscore", "all", "cn_stock", 5000, makeStocks(1)));
      }

      // Should have pruned to 5
      expect(smallStore.size).toBe(5);

      // First 3 should be pruned — their files should be gone
      for (let i = 0; i < 3; i++) {
        expect(existsSync(join(testDir, `${ids[i]}.json`))).toBe(false);
      }

      // Last 5 should remain
      for (let i = 3; i < 8; i++) {
        expect(smallStore.getEntry(ids[i])).toBeDefined();
      }
    });
  });

  // ── Edge cases ─────────────────────────────────────────────────

  describe("edge cases", () => {
    it("should handle empty results list", async () => {
      const id = await store.save("fscore", "all", "cn_stock", 5000, []);
      const record = store.getFullResult(id);
      expect(record!.resultCount).toBe(0);
      expect(record!.results).toEqual([]);
      const entry = store.getEntry(id);
      expect(entry!.topCodes).toEqual([]);
    });

    it("should work without dataDir (in-memory only)", async () => {
      const memStore = new ScanHistoryStore({});
      memStore.start();
      const id = await memStore.save("fscore", "all", "cn_stock", 100, makeStocks(3));
      expect(memStore.size).toBe(1);
      expect(memStore.getEntry(id)).toBeDefined();
      // Full result is not available without dataDir
      expect(memStore.getFullResult(id)).toBeNull();
    });

    it("should handle concurrent saves correctly", async () => {
      const promises = Array.from({ length: 10 }, (_, i) =>
        store.save("fscore", "all", "cn_stock", 5000, makeStocks(i + 1)),
      );
      const ids = await Promise.all(promises);
      expect(new Set(ids).size).toBe(10);
      expect(store.size).toBe(10);
    });
  });
});
