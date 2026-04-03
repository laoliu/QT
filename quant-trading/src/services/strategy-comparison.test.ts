import { describe, it, expect } from "vitest";
import type { BacktestResult, QuantTradingConfig } from "../types.js";
import {
  extractMetrics,
  computeRanks,
  computeMetricSummary,
  StrategyComparisonService,
  type ComparisonMetric,
} from "./strategy-comparison.js";

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

function fakeEntries(
  data: Array<{ strategyId: string; metrics: Partial<Record<ComparisonMetric, number>> }>,
) {
  return data.map((d) => ({
    strategyId: d.strategyId,
    metrics: {
      totalReturn: 0,
      annualizedReturn: 0,
      maxDrawdown: 0,
      sharpeRatio: 0,
      sortinoRatio: 0,
      calmarRatio: 0,
      winRate: 0,
      profitFactor: 0,
      totalTrades: 0,
      ...d.metrics,
    },
  }));
}

// ── extractMetrics ───────────────────────────────────────────

describe("extractMetrics", () => {
  it("extracts all metrics from a result", () => {
    const result = fakeResult({
      totalReturn: 0.15,
      annualizedReturn: 0.12,
      maxDrawdown: 0.08,
      sharpeRatio: 2.0,
      sortinoRatio: 2.5,
      calmarRatio: 1.5,
      winRate: 0.65,
      profitFactor: 1.8,
      totalTrades: 100,
    });
    const metrics = extractMetrics(result);
    expect(metrics.totalReturn).toBe(0.15);
    expect(metrics.annualizedReturn).toBe(0.12);
    expect(metrics.maxDrawdown).toBe(0.08);
    expect(metrics.sharpeRatio).toBe(2.0);
    expect(metrics.sortinoRatio).toBe(2.5);
    expect(metrics.calmarRatio).toBe(1.5);
    expect(metrics.winRate).toBe(0.65);
    expect(metrics.profitFactor).toBe(1.8);
    expect(metrics.totalTrades).toBe(100);
  });

  it("defaults optional metrics to 0", () => {
    const result = fakeResult();
    const metrics = extractMetrics(result);
    expect(metrics.sortinoRatio).toBe(0);
    expect(metrics.calmarRatio).toBe(0);
    expect(metrics.profitFactor).toBe(0);
  });
});

// ── computeRanks ─────────────────────────────────────────────

describe("computeRanks", () => {
  it("ranks higher-is-better metrics correctly", () => {
    const entries = fakeEntries([
      { strategyId: "a", metrics: { totalReturn: 0.1 } },
      { strategyId: "b", metrics: { totalReturn: 0.2 } },
      { strategyId: "c", metrics: { totalReturn: 0.15 } },
    ]);
    const ranks = computeRanks(entries, ["totalReturn"]);
    expect(ranks.get("b")!.totalReturn).toBe(1); // best
    expect(ranks.get("c")!.totalReturn).toBe(2);
    expect(ranks.get("a")!.totalReturn).toBe(3); // worst
  });

  it("ranks lower-is-better metrics correctly (maxDrawdown)", () => {
    const entries = fakeEntries([
      { strategyId: "a", metrics: { maxDrawdown: 0.2 } },
      { strategyId: "b", metrics: { maxDrawdown: 0.05 } },
      { strategyId: "c", metrics: { maxDrawdown: 0.1 } },
    ]);
    const ranks = computeRanks(entries, ["maxDrawdown"]);
    expect(ranks.get("b")!.maxDrawdown).toBe(1); // best (lowest)
    expect(ranks.get("c")!.maxDrawdown).toBe(2);
    expect(ranks.get("a")!.maxDrawdown).toBe(3); // worst
  });

  it("handles ties with shared rank", () => {
    const entries = fakeEntries([
      { strategyId: "a", metrics: { sharpeRatio: 1.5 } },
      { strategyId: "b", metrics: { sharpeRatio: 1.5 } },
      { strategyId: "c", metrics: { sharpeRatio: 1.0 } },
    ]);
    const ranks = computeRanks(entries, ["sharpeRatio"]);
    expect(ranks.get("a")!.sharpeRatio).toBe(1);
    expect(ranks.get("b")!.sharpeRatio).toBe(1);
    expect(ranks.get("c")!.sharpeRatio).toBe(3);
  });

  it("handles single entry", () => {
    const entries = fakeEntries([{ strategyId: "a", metrics: { totalReturn: 0.1 } }]);
    const ranks = computeRanks(entries, ["totalReturn"]);
    expect(ranks.get("a")!.totalReturn).toBe(1);
  });
});

// ── computeMetricSummary ─────────────────────────────────────

describe("computeMetricSummary", () => {
  it("finds best/worst/avg (higher is better)", () => {
    const entries = [
      { strategyId: "a", value: 0.1 },
      { strategyId: "b", value: 0.3 },
      { strategyId: "c", value: 0.2 },
    ];
    const summary = computeMetricSummary(entries, true);
    expect(summary.best.strategyId).toBe("b");
    expect(summary.best.value).toBe(0.3);
    expect(summary.worst.strategyId).toBe("a");
    expect(summary.worst.value).toBe(0.1);
    expect(summary.avg).toBeCloseTo(0.2);
  });

  it("finds best/worst/avg (lower is better)", () => {
    const entries = [
      { strategyId: "a", value: 0.2 },
      { strategyId: "b", value: 0.05 },
      { strategyId: "c", value: 0.1 },
    ];
    const summary = computeMetricSummary(entries, false);
    expect(summary.best.strategyId).toBe("b"); // lowest
    expect(summary.worst.strategyId).toBe("a"); // highest
  });

  it("handles empty entries", () => {
    const summary = computeMetricSummary([], true);
    expect(summary.avg).toBe(0);
    expect(summary.values).toHaveLength(0);
  });
});

// ── StrategyComparisonService.buildComparison ────────────────

describe("StrategyComparisonService", () => {
  const config = {} as QuantTradingConfig;
  const service = new StrategyComparisonService(config);

  it("builds comparison from results", () => {
    const results = [
      {
        strategyId: "ma",
        result: fakeResult({ totalReturn: 0.1, sharpeRatio: 1.0, maxDrawdown: 0.1, winRate: 0.5 }),
      },
      {
        strategyId: "bb",
        result: fakeResult({ totalReturn: 0.2, sharpeRatio: 2.0, maxDrawdown: 0.05, winRate: 0.6 }),
      },
      {
        strategyId: "mo",
        result: fakeResult({
          totalReturn: 0.15,
          sharpeRatio: 1.5,
          maxDrawdown: 0.08,
          winRate: 0.55,
        }),
      },
    ];

    const comparison = service.buildComparison(results);

    expect(comparison.entries).toHaveLength(3);
    expect(comparison.summary.totalReturn.best.strategyId).toBe("bb");
    expect(comparison.summary.maxDrawdown.best.strategyId).toBe("bb"); // lowest drawdown
  });

  it("computes overall ranking by composite score", () => {
    const results = [
      {
        strategyId: "good",
        result: fakeResult({ totalReturn: 0.3, sharpeRatio: 2.5, maxDrawdown: 0.03, winRate: 0.7 }),
      },
      {
        strategyId: "bad",
        result: fakeResult({ totalReturn: 0.01, sharpeRatio: 0.2, maxDrawdown: 0.3, winRate: 0.3 }),
      },
    ];

    const comparison = service.buildComparison(results);
    expect(comparison.overallRanking[0]!.strategyId).toBe("good");
    expect(comparison.overallRanking[0]!.rank).toBe(1);
    expect(comparison.overallRanking[1]!.strategyId).toBe("bad");
    expect(comparison.overallRanking[1]!.rank).toBe(2);
  });

  it("includes ranks in entries", () => {
    const results = [
      { strategyId: "a", result: fakeResult({ totalReturn: 0.05 }) },
      { strategyId: "b", result: fakeResult({ totalReturn: 0.15 }) },
    ];

    const comparison = service.buildComparison(results);
    const entryA = comparison.entries.find((e) => e.strategyId === "a")!;
    const entryB = comparison.entries.find((e) => e.strategyId === "b")!;

    expect(entryB.ranks.totalReturn).toBe(1);
    expect(entryA.ranks.totalReturn).toBe(2);
  });

  it("handles single strategy", () => {
    const results = [{ strategyId: "only", result: fakeResult() }];
    const comparison = service.buildComparison(results);
    expect(comparison.entries).toHaveLength(1);
    expect(comparison.overallRanking[0]!.rank).toBe(1);
  });

  it("handles empty input", () => {
    const comparison = service.buildComparison([]);
    expect(comparison.entries).toHaveLength(0);
    expect(comparison.overallRanking).toHaveLength(0);
  });

  it("compareExisting returns null when no store", () => {
    expect(service.compareExisting(["a", "b"])).toBeNull();
  });
});
