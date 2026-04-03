import { describe, it, expect } from "vitest";
import type { BacktestResult } from "../types.js";
import {
  normalizeValues,
  classifyTier,
  PerformanceRanking,
  DEFAULT_WEIGHTS,
  type RankingEntry,
} from "./performance-ranking.js";

// ── Helpers ──────────────────────────────────────────────────

function fakeResult(overrides: Partial<BacktestResult> = {}): BacktestResult {
  return {
    strategyId: "test",
    symbols: ["000300"],
    startDate: "2024-01-01",
    endDate: "2024-12-31",
    initialCash: 100_000,
    finalValue: 110_000,
    totalReturn: 0.1,
    annualizedReturn: 0.1,
    maxDrawdown: 0.05,
    sharpeRatio: 1.5,
    winRate: 0.6,
    totalTrades: 50,
    signals: [],
    equityCurve: [],
    ...overrides,
  };
}

function fakeItem(id: string, strategyId: string, overrides: Partial<BacktestResult> = {}) {
  return { id, strategyId, result: fakeResult({ strategyId, ...overrides }) };
}

// ── normalizeValues ──────────────────────────────────────────

describe("normalizeValues", () => {
  it("normalizes to [0, 1] when higher is better", () => {
    const result = normalizeValues([0, 50, 100], true);
    expect(result).toEqual([0, 0.5, 1]);
  });

  it("inverts when lower is better", () => {
    const result = normalizeValues([0, 50, 100], false);
    expect(result).toEqual([1, 0.5, 0]);
  });

  it("handles all equal values", () => {
    const result = normalizeValues([5, 5, 5], true);
    expect(result).toEqual([0.5, 0.5, 0.5]);
  });

  it("handles empty array", () => {
    expect(normalizeValues([], true)).toEqual([]);
  });

  it("handles single value", () => {
    const result = normalizeValues([42], true);
    expect(result).toEqual([0.5]);
  });
});

// ── classifyTier ─────────────────────────────────────────────

describe("classifyTier", () => {
  it("S tier for >= 0.8", () => {
    expect(classifyTier(0.8)).toBe("S");
    expect(classifyTier(0.9)).toBe("S");
    expect(classifyTier(1.0)).toBe("S");
  });

  it("A tier for [0.6, 0.8)", () => {
    expect(classifyTier(0.6)).toBe("A");
    expect(classifyTier(0.79)).toBe("A");
  });

  it("B tier for [0.4, 0.6)", () => {
    expect(classifyTier(0.4)).toBe("B");
    expect(classifyTier(0.59)).toBe("B");
  });

  it("C tier for [0.2, 0.4)", () => {
    expect(classifyTier(0.2)).toBe("C");
    expect(classifyTier(0.39)).toBe("C");
  });

  it("D tier for < 0.2", () => {
    expect(classifyTier(0.1)).toBe("D");
    expect(classifyTier(0)).toBe("D");
  });
});

// ── PerformanceRanking ───────────────────────────────────────

describe("PerformanceRanking", () => {
  const ranking = new PerformanceRanking();

  it("ranks items by composite score", () => {
    const items = [
      fakeItem("1", "good", {
        totalReturn: 0.3,
        sharpeRatio: 2.5,
        maxDrawdown: 0.03,
        winRate: 0.7,
      }),
      fakeItem("2", "ok", { totalReturn: 0.1, sharpeRatio: 1.0, maxDrawdown: 0.1, winRate: 0.5 }),
      fakeItem("3", "bad", {
        totalReturn: -0.05,
        sharpeRatio: -0.5,
        maxDrawdown: 0.25,
        winRate: 0.3,
      }),
    ];

    const result = ranking.rank(items);

    expect(result.entries).toHaveLength(3);
    expect(result.entries[0]!.id).toBe("1"); // best
    expect(result.entries[0]!.rank).toBe(1);
    expect(result.entries[2]!.id).toBe("3"); // worst
    expect(result.entries[2]!.rank).toBe(3);
  });

  it("handles empty input", () => {
    const result = ranking.rank([]);
    expect(result.entries).toHaveLength(0);
    expect(result.total).toBe(0);
    expect(result.stats.bestEntry).toBeNull();
  });

  it("handles single item", () => {
    const result = ranking.rank([fakeItem("1", "only")]);
    expect(result.entries).toHaveLength(1);
    expect(result.entries[0]!.rank).toBe(1);
    expect(result.entries[0]!.compositeScore).toBe(0.5); // normalized single = 0.5
  });

  it("filters by strategyId", () => {
    const items = [fakeItem("1", "ma"), fakeItem("2", "rsi"), fakeItem("3", "ma")];
    const result = ranking.rank(items, { strategyId: "ma" });
    expect(result.total).toBe(2);
    expect(result.entries.every((e) => e.strategyId === "ma")).toBe(true);
  });

  it("filters by minTrades", () => {
    const items = [
      fakeItem("1", "a", { totalTrades: 5 }),
      fakeItem("2", "b", { totalTrades: 50 }),
      fakeItem("3", "c", { totalTrades: 100 }),
    ];
    const result = ranking.rank(items, { minTrades: 20 });
    expect(result.total).toBe(2);
  });

  it("paginates with limit/offset", () => {
    const items = Array.from({ length: 10 }, (_, i) =>
      fakeItem(`${i}`, "s", { totalReturn: i * 0.01 }),
    );
    const page1 = ranking.rank(items, { limit: 3, offset: 0 });
    expect(page1.entries).toHaveLength(3);
    expect(page1.total).toBe(10);

    const page2 = ranking.rank(items, { limit: 3, offset: 3 });
    expect(page2.entries).toHaveLength(3);
    expect(page2.entries[0]!.rank).toBe(4);
  });

  it("sorts by specific metric", () => {
    const items = [
      fakeItem("1", "a", { sharpeRatio: 1.0 }),
      fakeItem("2", "b", { sharpeRatio: 3.0 }),
      fakeItem("3", "c", { sharpeRatio: 2.0 }),
    ];
    const result = ranking.rank(items, { sortBy: "sharpeRatio" });
    expect(result.entries[0]!.id).toBe("2");
    expect(result.entries[1]!.id).toBe("3");
    expect(result.entries[2]!.id).toBe("1");
  });

  it("sorts ascending", () => {
    const items = [
      fakeItem("1", "a", { maxDrawdown: 0.1 }),
      fakeItem("2", "b", { maxDrawdown: 0.05 }),
      fakeItem("3", "c", { maxDrawdown: 0.2 }),
    ];
    const result = ranking.rank(items, { sortBy: "maxDrawdown", direction: "asc" });
    expect(result.entries[0]!.metrics.maxDrawdown).toBe(0.05);
  });

  it("computes tier distribution", () => {
    const items = [
      fakeItem("1", "a", { totalReturn: 0.5, sharpeRatio: 3.0, maxDrawdown: 0.01, winRate: 0.9 }),
      fakeItem("2", "b", { totalReturn: 0.01, sharpeRatio: 0.1, maxDrawdown: 0.3, winRate: 0.2 }),
    ];
    const result = ranking.rank(items);
    const dist = result.stats.tierDistribution;
    expect(dist.S + dist.A + dist.B + dist.C + dist.D).toBe(2);
  });

  it("computes average composite score", () => {
    const items = [fakeItem("1", "a"), fakeItem("2", "b")];
    const result = ranking.rank(items);
    expect(result.stats.avgComposite).toBeGreaterThanOrEqual(0);
    expect(result.stats.avgComposite).toBeLessThanOrEqual(1);
  });

  it("uses custom weights", () => {
    const customRanking = new PerformanceRanking({
      totalReturn: 1.0, // only weight return
    });
    const items = [
      fakeItem("1", "a", { totalReturn: 0.05, sharpeRatio: 5.0 }), // great Sharpe, low return
      fakeItem("2", "b", { totalReturn: 0.5, sharpeRatio: 0.5 }), // bad Sharpe, high return
    ];
    const result = customRanking.rank(items);
    expect(result.entries[0]!.id).toBe("2"); // high return wins
  });

  it("assigns rank to all entries consistently", () => {
    const items = Array.from({ length: 5 }, (_, i) =>
      fakeItem(`${i}`, "s", { totalReturn: i * 0.05 }),
    );
    const result = ranking.rank(items);
    const ranks = result.entries.map((e) => e.rank);
    expect(ranks).toEqual([1, 2, 3, 4, 5]);
  });

  it("default weights are defined", () => {
    expect(Object.keys(DEFAULT_WEIGHTS).length).toBeGreaterThanOrEqual(5);
  });
});
