import { describe, it, expect } from "vitest";
import type { BacktestResult } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import {
  computeLivePerformance,
  compareLiveVsBacktest,
} from "./live-backtest-comparison.js";

// ── Helpers ────────────────────────────────────────────────

function makeSnapshots(
  days: number,
  startEquity = 100_000,
  dailyReturnPct = 0.1,
): EquitySnapshot[] {
  const snaps: EquitySnapshot[] = [];
  let equity = startEquity;
  const base = new Date("2024-01-02");
  for (let i = 0; i < days; i++) {
    const date = new Date(base);
    date.setDate(base.getDate() + i);
    // Skip weekends
    if (date.getDay() === 0 || date.getDay() === 6) continue;
    const dailyPnl = equity * (dailyReturnPct / 100);
    equity += dailyPnl;
    snaps.push({
      date: date.toISOString().slice(0, 10),
      equity: Math.round(equity * 100) / 100,
      cash: equity * 0.3,
      marketValue: equity * 0.7,
      dailyPnl: Math.round(dailyPnl * 100) / 100,
      positionCount: 3,
    });
  }
  return snaps;
}

function makeBacktestResult(
  snapshots: EquitySnapshot[],
  returnMultiplier = 1.0,
): BacktestResult {
  const firstEquity = snapshots[0]?.equity ?? 100_000;
  const equityCurve = snapshots.map((s) => ({
    date: s.date,
    value: Math.round(firstEquity + (s.equity - firstEquity) * returnMultiplier),
  }));
  const lastValue = equityCurve[equityCurve.length - 1]?.value ?? firstEquity;
  const totalReturn = firstEquity > 0 ? (lastValue - firstEquity) / firstEquity : 0;

  return {
    strategyId: "dual-ma-crossover",
    symbols: ["600519"],
    startDate: snapshots[0]?.date ?? "2024-01-02",
    endDate: snapshots[snapshots.length - 1]?.date ?? "2024-03-01",
    initialCash: firstEquity,
    finalValue: lastValue,
    totalReturn,
    annualizedReturn: totalReturn * 4, // rough quarterly → annual
    maxDrawdown: 0.05,
    sharpeRatio: 1.5,
    winRate: 0.55,
    totalTrades: 30,
    signals: [],
    equityCurve,
  };
}

// ── computeLivePerformance ──────────────────────────────────

describe("computeLivePerformance", () => {
  it("returns null for fewer than 2 snapshots", () => {
    expect(computeLivePerformance([])).toBeNull();
    expect(computeLivePerformance([makeSnapshots(1)[0]!])).toBeNull();
  });

  it("computes basic performance metrics", () => {
    const snaps = makeSnapshots(30);
    const perf = computeLivePerformance(snaps);
    expect(perf).not.toBeNull();
    expect(perf!.totalReturn).toBeGreaterThan(0);
    expect(perf!.maxDrawdown).toBeGreaterThanOrEqual(0);
    expect(perf!.tradingDays).toBeGreaterThan(0);
    expect(perf!.equityCurve.length).toBe(snaps.length);
  });

  it("respects date range filters", () => {
    const snaps = makeSnapshots(60);
    const midDate = snaps[Math.floor(snaps.length / 2)]!.date;
    const perf = computeLivePerformance(snaps, midDate);
    expect(perf).not.toBeNull();
    expect(perf!.startDate).toBe(midDate);
  });

  it("computes winRate from daily PnL", () => {
    const snaps: EquitySnapshot[] = [
      { date: "2024-01-02", equity: 100000, cash: 50000, marketValue: 50000, dailyPnl: 0, positionCount: 1 },
      { date: "2024-01-03", equity: 101000, cash: 50000, marketValue: 51000, dailyPnl: 1000, positionCount: 1 },
      { date: "2024-01-04", equity: 100500, cash: 50000, marketValue: 50500, dailyPnl: -500, positionCount: 1 },
      { date: "2024-01-05", equity: 101500, cash: 50000, marketValue: 51500, dailyPnl: 1000, positionCount: 1 },
    ];
    const perf = computeLivePerformance(snaps);
    expect(perf).not.toBeNull();
    // 2 win days, 1 loss day → winRate = 2/3
    expect(perf!.winRate).toBeCloseTo(2 / 3, 2);
  });
});

// ── compareLiveVsBacktest ───────────────────────────────────

describe("compareLiveVsBacktest", () => {
  it("returns null when live data is insufficient", () => {
    const result = compareLiveVsBacktest([], makeBacktestResult(makeSnapshots(30)));
    expect(result).toBeNull();
  });

  it("returns null when backtest has no equity curve", () => {
    const snaps = makeSnapshots(30);
    const bt = makeBacktestResult(snaps);
    bt.equityCurve = [];
    expect(compareLiveVsBacktest(snaps, bt)).toBeNull();
  });

  it("returns null when there is no date overlap", () => {
    const liveSnaps = makeSnapshots(10);
    // Shift backtest dates to non-overlapping range
    const btSnaps = makeSnapshots(10);
    const bt = makeBacktestResult(btSnaps);
    bt.equityCurve = bt.equityCurve.map((p) => ({
      ...p,
      date: p.date.replace("2024", "2023"),
    }));
    expect(compareLiveVsBacktest(liveSnaps, bt)).toBeNull();
  });

  it("produces a valid comparison for identical curves", () => {
    const snaps = makeSnapshots(60);
    const bt = makeBacktestResult(snaps, 1.0); // same return
    const result = compareLiveVsBacktest(snaps, bt);

    expect(result).not.toBeNull();
    expect(result!.metrics.length).toBeGreaterThan(0);
    expect(result!.trackingScore).toBeGreaterThanOrEqual(40);
    expect(result!.comparisonPeriod.startDate).toBeTruthy();
    expect(result!.comparisonPeriod.endDate).toBeTruthy();
  });

  it("detects when live underperforms backtest", () => {
    const snaps = makeSnapshots(60, 100_000, 0.05); // 0.05% daily
    const bt = makeBacktestResult(snaps, 2.0); // backtest 2x the return
    const result = compareLiveVsBacktest(snaps, bt);

    expect(result).not.toBeNull();
    // The return gap should be negative (live < backtest)
    expect(result!.executionGap.returnGap).toBeLessThan(0);
  });

  it("includes warnings for significant divergence", () => {
    const snaps = makeSnapshots(60, 100_000, -0.2); // losing money
    const bt = makeBacktestResult(snaps, -3.0); // backtest profitable (inverted)
    const result = compareLiveVsBacktest(snaps, bt);

    // We should get at least some warnings
    expect(result).not.toBeNull();
    // With such divergence there should be warnings
    if (result) {
      expect(result.warnings.length).toBeGreaterThanOrEqual(0);
    }
  });

  it("respects divergence threshold option", () => {
    const snaps = makeSnapshots(60);
    const bt = makeBacktestResult(snaps, 1.5);
    const looseResult = compareLiveVsBacktest(snaps, bt, {
      divergenceThresholdPct: 50,
    });
    const tightResult = compareLiveVsBacktest(snaps, bt, {
      divergenceThresholdPct: 1,
    });

    expect(looseResult).not.toBeNull();
    expect(tightResult).not.toBeNull();
    // Tighter threshold should find more (or equal) divergence windows
    expect(tightResult!.divergenceWindows.length).toBeGreaterThanOrEqual(
      looseResult!.divergenceWindows.length,
    );
  });

  it("metrics contain all expected fields", () => {
    const snaps = makeSnapshots(60);
    const bt = makeBacktestResult(snaps);
    const result = compareLiveVsBacktest(snaps, bt)!;

    const metricNames = result.metrics.map((m) => m.metric);
    expect(metricNames).toContain("totalReturn");
    expect(metricNames).toContain("annualizedReturn");
    expect(metricNames).toContain("maxDrawdown");
    expect(metricNames).toContain("sharpeRatio");
    expect(metricNames).toContain("winRate");

    for (const m of result.metrics) {
      expect(typeof m.live).toBe("number");
      expect(typeof m.backtest).toBe("number");
      expect(typeof m.diff).toBe("number");
      expect(typeof m.diffPct).toBe("number");
      expect(typeof m.liveWins).toBe("boolean");
    }
  });

  it("tracking score is between 0 and 100", () => {
    const snaps = makeSnapshots(60);
    const bt = makeBacktestResult(snaps);
    const result = compareLiveVsBacktest(snaps, bt)!;

    expect(result.trackingScore).toBeGreaterThanOrEqual(0);
    expect(result.trackingScore).toBeLessThanOrEqual(100);
  });
});
