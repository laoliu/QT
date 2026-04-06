import { describe, it, expect } from "vitest";
import type { BacktestResult } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import { LiveVsBacktestService, type LiveVsBacktestRequest } from "./live-vs-backtest.js";

// ── Helpers ──────────────────────────────────────────────────

function makeSnap(date: string, equity: number, dailyPnl = 0): EquitySnapshot {
  return {
    date,
    equity,
    cash: equity * 0.5,
    marketValue: equity * 0.5,
    dailyPnl,
    positionCount: 2,
  };
}

function makeBacktest(
  equityCurve: Array<{ date: string; value: number }>,
  overrides: Partial<BacktestResult> = {},
): BacktestResult {
  const first = equityCurve[0]?.value ?? 100_000;
  const last = equityCurve[equityCurve.length - 1]?.value ?? 100_000;
  return {
    strategyId: "test-strategy",
    symbols: ["000300"],
    startDate: equityCurve[0]?.date ?? "2024-01-01",
    endDate: equityCurve[equityCurve.length - 1]?.date ?? "2024-12-31",
    initialCash: first,
    finalValue: last,
    totalReturn: first > 0 ? (last - first) / first : 0,
    annualizedReturn: 0.1,
    maxDrawdown: 0.05,
    sharpeRatio: 1.5,
    winRate: 0.6,
    totalTrades: 50,
    signals: [],
    equityCurve,
    ...overrides,
  };
}

// ── Unit Tests ──────────────────────────────────────────────

describe("LiveVsBacktestService", () => {
  const service = new LiveVsBacktestService();

  // ── Happy path ──────────────────────────────────────────

  describe("compare — happy path", () => {
    it("aligns perfectly matching dates", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 102_000, 2000),
        makeSnap("2024-01-04", 101_000, -1000),
        makeSnap("2024-01-05", 105_000, 4000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 },
        { date: "2024-01-04", value: 103_000 },
        { date: "2024-01-05", value: 104_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(4);
      expect(result.liveMissingDates).toHaveLength(0);
      expect(result.backtestMissingDates).toHaveLength(0);
      expect(result.stats.overlapDays).toBe(4);

      // Live: 100k → 105k = 5%
      expect(result.stats.liveReturnPct).toBeCloseTo(5.0, 1);
      // Backtest: 100k → 104k = 4%
      expect(result.stats.backtestReturnPct).toBeCloseTo(4.0, 1);
      // Gap: 1%
      expect(result.stats.returnGapPct).toBeCloseTo(1.0, 1);
    });

    it("normalized returns start at 1.0", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 110_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 200_000 },
        { date: "2024-01-03", value: 210_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries[0]!.liveNormalized).toBe(1);
      expect(result.alignedSeries[0]!.backtestNormalized).toBe(1);
      // Live: 10% return, Backtest: 5% return
      expect(result.alignedSeries[1]!.liveNormalized).toBeCloseTo(1.1, 2);
      expect(result.alignedSeries[1]!.backtestNormalized).toBeCloseTo(1.05, 2);
    });

    it("computes deviation as live - backtest normalized", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 110_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 105_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      // Deviation: 1.10 - 1.05 = 0.05
      expect(result.alignedSeries[1]!.deviation).toBeCloseTo(0.05, 4);
    });
  });

  // ── Date misalignment ─────────────────────────────────────

  describe("compare — date misalignment", () => {
    it("handles live dates missing from backtest", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 101_000),
        makeSnap("2024-01-04", 102_000), // extra live day
        makeSnap("2024-01-05", 103_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_500 },
        // no 2024-01-04 in backtest
        { date: "2024-01-05", value: 103_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(3); // only common dates
      expect(result.liveMissingDates).toEqual(["2024-01-04"]);
      expect(result.backtestMissingDates).toHaveLength(0);
    });

    it("handles backtest dates missing from live", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-05", 103_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 },
        { date: "2024-01-04", value: 102_000 },
        { date: "2024-01-05", value: 103_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(2);
      expect(result.backtestMissingDates).toEqual(["2024-01-03", "2024-01-04"]);
      expect(result.liveMissingDates).toHaveLength(0);
    });

    it("handles partial overlap in the middle", () => {
      const live = [
        makeSnap("2024-01-01", 100_000), // only live
        makeSnap("2024-01-02", 101_000), // common
        makeSnap("2024-01-03", 102_000), // common
        makeSnap("2024-01-04", 103_000), // only live
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 },
        { date: "2024-01-05", value: 102_000 }, // only backtest
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(2);
      expect(result.liveMissingDates).toEqual(["2024-01-01", "2024-01-04"]);
      expect(result.backtestMissingDates).toEqual(["2024-01-05"]);
    });
  });

  // ── Data missing / empty ──────────────────────────────────

  describe("compare — data missing / empty", () => {
    it("returns empty result when no live snapshots", () => {
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 },
      ]);

      const result = service.compare({ liveSnapshots: [], backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(0);
      expect(result.stats.overlapDays).toBe(0);
      expect(result.stats.correlation).toBe(0);
      expect(result.driftAlerts).toHaveLength(0);
    });

    it("returns empty result when backtest has empty equity curve", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 101_000),
      ];
      const bt = makeBacktest([]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(0);
      expect(result.stats.overlapDays).toBe(0);
    });

    it("returns zero stats when only 1 common date", () => {
      const live = [makeSnap("2024-01-02", 100_000)];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(1);
      expect(result.stats.overlapDays).toBe(1);
      expect(result.stats.liveReturnPct).toBe(0);
      expect(result.stats.backtestReturnPct).toBe(0);
      expect(result.stats.correlation).toBe(0);
    });

    it("returns empty result when no date overlap", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 101_000),
      ];
      const bt = makeBacktest([
        { date: "2024-02-02", value: 100_000 },
        { date: "2024-02-03", value: 101_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(0);
      expect(result.stats.overlapDays).toBe(0);
      expect(result.liveMissingDates).toEqual(["2024-01-02", "2024-01-03"]);
      expect(result.backtestMissingDates).toEqual(["2024-02-02", "2024-02-03"]);
    });
  });

  // ── Drawdown computation ──────────────────────────────────

  describe("compare — drawdown metrics", () => {
    it("computes max drawdown for both series", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 110_000),
        makeSnap("2024-01-04", 99_000),  // 10% dd from 110k
        makeSnap("2024-01-05", 105_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 105_000 },
        { date: "2024-01-04", value: 100_000 }, // 4.76% dd from 105k
        { date: "2024-01-05", value: 108_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      // Live: peak 110k, trough 99k → dd = 11000/110000 = 10%
      expect(result.stats.liveMaxDrawdownPct).toBeCloseTo(10.0, 1);
      // Backtest: peak 105k, trough 100k → dd = 5000/105000 = 4.76%
      expect(result.stats.backtestMaxDrawdownPct).toBeCloseTo(4.76, 1);
      // Gap: 10 - 4.76 = 5.24
      expect(result.stats.drawdownGapPct).toBeCloseTo(5.24, 1);
    });

    it("returns zero drawdown for monotonically increasing series", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 101_000),
        makeSnap("2024-01-04", 102_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 },
        { date: "2024-01-04", value: 102_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.stats.liveMaxDrawdownPct).toBe(0);
      expect(result.stats.backtestMaxDrawdownPct).toBe(0);
    });
  });

  // ── Correlation ───────────────────────────────────────────

  describe("compare — correlation", () => {
    it("returns perfect correlation for identical series", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 102_000),
        makeSnap("2024-01-04", 101_000),
        makeSnap("2024-01-05", 105_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 102_000 },
        { date: "2024-01-04", value: 101_000 },
        { date: "2024-01-05", value: 105_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.stats.correlation).toBeCloseTo(1.0, 4);
    });

    it("returns negative correlation for inversely moving series", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 105_000), // +5%
        makeSnap("2024-01-04", 100_000), // -4.76%
        makeSnap("2024-01-05", 106_000), // +6%
        makeSnap("2024-01-06", 101_000), // -4.72%
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 95_000 },  // -5%
        { date: "2024-01-04", value: 100_000 }, // +5.26%
        { date: "2024-01-05", value: 94_000 },  // -6%
        { date: "2024-01-06", value: 99_000 },  // +5.32%
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.stats.correlation).toBeLessThan(-0.9);
    });

    it("returns zero correlation when only 2 common dates (1 return)", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 105_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 95_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      // Only 1 return → not enough for meaningful correlation
      expect(result.stats.correlation).toBe(0);
    });
  });

  // ── Drift alerts ──────────────────────────────────────────

  describe("compare — drift alerts", () => {
    it("detects outperformance drift", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 110_000), // +10%
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 }, // +1%
      ]);

      const result = service.compare({
        liveSnapshots: live,
        backtestResult: bt,
        driftThreshold: 5,
      });

      expect(result.driftAlerts).toHaveLength(1);
      expect(result.driftAlerts[0]!.date).toBe("2024-01-03");
      expect(result.driftAlerts[0]!.direction).toBe("outperform");
      expect(result.driftAlerts[0]!.deviationPct).toBeCloseTo(9.0, 0); // ~9pp
    });

    it("detects underperformance drift", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 92_000), // -8%
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 100_000 }, // 0%
      ]);

      const result = service.compare({
        liveSnapshots: live,
        backtestResult: bt,
        driftThreshold: 5,
      });

      expect(result.driftAlerts).toHaveLength(1);
      expect(result.driftAlerts[0]!.direction).toBe("underperform");
      expect(result.driftAlerts[0]!.deviationPct).toBeLessThan(-5);
    });

    it("does not alert when within threshold", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 103_000), // +3%
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 }, // +1%
      ]);

      const result = service.compare({
        liveSnapshots: live,
        backtestResult: bt,
        driftThreshold: 5,
      });

      expect(result.driftAlerts).toHaveLength(0);
    });

    it("respects custom drift threshold", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 103_000), // +3%
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 }, // +1%
      ]);

      // Threshold = 1% → should alert
      const result = service.compare({
        liveSnapshots: live,
        backtestResult: bt,
        driftThreshold: 1,
      });

      expect(result.driftAlerts).toHaveLength(1);
    });

    it("uses default threshold of 5 when not specified", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 104_000), // +4%
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 100_000 }, // 0%
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      // 4pp < 5pp default → no alert
      expect(result.driftAlerts).toHaveLength(0);
    });
  });

  // ── Tracking error ────────────────────────────────────────

  describe("compare — tracking error", () => {
    it("returns zero tracking error for identical series", () => {
      const curve = [
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 102_000 },
        { date: "2024-01-04", value: 101_000 },
        { date: "2024-01-05", value: 104_000 },
      ];
      const live = curve.map((p) => makeSnap(p.date, p.value));
      const bt = makeBacktest(curve);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.stats.trackingError).toBeCloseTo(0, 4);
    });

    it("returns positive tracking error for diverging series", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 105_000),
        makeSnap("2024-01-04", 98_000),
        makeSnap("2024-01-05", 108_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 },
        { date: "2024-01-04", value: 102_000 },
        { date: "2024-01-05", value: 103_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.stats.trackingError).toBeGreaterThan(0);
    });
  });

  // ── Edge cases ────────────────────────────────────────────

  describe("compare — edge cases", () => {
    it("handles zero equity (division safety)", () => {
      const live = [
        makeSnap("2024-01-02", 0),
        makeSnap("2024-01-03", 0),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 0 },
        { date: "2024-01-03", value: 0 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.stats.overlapDays).toBe(2);
      // Should not throw or produce NaN
      expect(Number.isFinite(result.stats.liveReturnPct)).toBe(true);
      expect(Number.isFinite(result.stats.backtestReturnPct)).toBe(true);
      expect(Number.isFinite(result.stats.correlation)).toBe(true);
    });

    it("handles unsorted live snapshots", () => {
      const live = [
        makeSnap("2024-01-05", 105_000),
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 102_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 101_000 },
        { date: "2024-01-05", value: 103_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      // Dates should be sorted in output
      expect(result.alignedSeries.map((p) => p.date)).toEqual([
        "2024-01-02",
        "2024-01-03",
        "2024-01-05",
      ]);
    });

    it("handles duplicate dates in live snapshots (last wins from map)", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-02", 105_000), // duplicate → overwrites
        makeSnap("2024-01-03", 110_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 102_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(2);
      expect(result.alignedSeries[0]!.liveEquity).toBe(105_000);
    });

    it("handles very large number of data points", () => {
      const n = 500;
      const live: EquitySnapshot[] = [];
      const curve: Array<{ date: string; value: number }> = [];

      for (let i = 0; i < n; i++) {
        const day = String(Math.floor(i / 28) + 1).padStart(2, "0");
        const month = String((i % 12) + 1).padStart(2, "0");
        const date = `2024-${month}-${day}`;
        live.push(makeSnap(date, 100_000 + i * 100));
        curve.push({ date, value: 100_000 + i * 50 });
      }
      const bt = makeBacktest(curve);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.stats.overlapDays).toBeGreaterThan(0);
      expect(result.stats.returnGapPct).toBeGreaterThan(0);
    });

    it("handles single-day backtest equity curve", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 102_000),
      ];
      const bt = makeBacktest([{ date: "2024-01-02", value: 100_000 }]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      expect(result.alignedSeries).toHaveLength(1);
      expect(result.stats.overlapDays).toBe(1);
    });

    it("handles live equity starting much higher than backtest", () => {
      const live = [
        makeSnap("2024-01-02", 1_000_000),
        makeSnap("2024-01-03", 1_050_000),
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 105_000 },
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      // Both 5% return → normalized returns should match
      expect(result.alignedSeries[1]!.liveNormalized).toBeCloseTo(1.05, 4);
      expect(result.alignedSeries[1]!.backtestNormalized).toBeCloseTo(1.05, 4);
      expect(result.stats.returnGapPct).toBeCloseTo(0, 1);
    });
  });

  // ── Deviation stats ───────────────────────────────────────

  describe("compare — deviation stats", () => {
    it("computes mean and max absolute deviation", () => {
      const live = [
        makeSnap("2024-01-02", 100_000),
        makeSnap("2024-01-03", 106_000), // norm: 1.06
        makeSnap("2024-01-04", 104_000), // norm: 1.04
        makeSnap("2024-01-05", 108_000), // norm: 1.08
      ];
      const bt = makeBacktest([
        { date: "2024-01-02", value: 100_000 },
        { date: "2024-01-03", value: 103_000 }, // norm: 1.03
        { date: "2024-01-04", value: 102_000 }, // norm: 1.02
        { date: "2024-01-05", value: 101_000 }, // norm: 1.01
      ]);

      const result = service.compare({ liveSnapshots: live, backtestResult: bt });

      // Deviations: 0, 0.03, 0.02, 0.07
      // Abs deviations: 0, 0.03, 0.02, 0.07
      expect(result.stats.meanAbsDeviation).toBeCloseTo(0.03, 2);
      expect(result.stats.maxAbsDeviation).toBeCloseTo(0.07, 2);
    });
  });
});

// ── Integration tests ───────────────────────────────────────

describe("LiveVsBacktestService — integration scenarios", () => {
  const service = new LiveVsBacktestService();

  it("simulates a real scenario: strategy outperforms backtest projection", () => {
    // Backtest predicted modest gains
    const bt = makeBacktest([
      { date: "2024-03-01", value: 500_000 },
      { date: "2024-03-04", value: 502_000 },
      { date: "2024-03-05", value: 504_000 },
      { date: "2024-03-06", value: 503_000 },
      { date: "2024-03-07", value: 505_000 },
      { date: "2024-03-08", value: 508_000 },
      { date: "2024-03-11", value: 507_000 },
      { date: "2024-03-12", value: 510_000 },
    ]);

    // Live trading did better
    const live = [
      makeSnap("2024-03-01", 500_000),
      makeSnap("2024-03-04", 505_000, 5000),
      makeSnap("2024-03-05", 510_000, 5000),
      makeSnap("2024-03-06", 508_000, -2000),
      makeSnap("2024-03-07", 515_000, 7000),
      makeSnap("2024-03-08", 520_000, 5000),
      makeSnap("2024-03-11", 518_000, -2000),
      makeSnap("2024-03-12", 525_000, 7000),
    ];

    const result = service.compare({
      liveSnapshots: live,
      backtestResult: bt,
      driftThreshold: 2,
    });

    // Live: 500k → 525k = 5%
    expect(result.stats.liveReturnPct).toBeCloseTo(5.0, 1);
    // Backtest: 500k → 510k = 2%
    expect(result.stats.backtestReturnPct).toBeCloseTo(2.0, 1);
    // Outperformance
    expect(result.stats.returnGapPct).toBeGreaterThan(0);
    // Correlation should be positive (both generally go up)
    expect(result.stats.correlation).toBeGreaterThan(0);
    // Should have drift alerts for outperformance
    expect(result.driftAlerts.length).toBeGreaterThan(0);
    expect(result.driftAlerts.every((a) => a.direction === "outperform")).toBe(true);
  });

  it("simulates a real scenario: live trading underperforms due to slippage/fees", () => {
    const bt = makeBacktest([
      { date: "2024-06-03", value: 200_000 },
      { date: "2024-06-04", value: 204_000 },
      { date: "2024-06-05", value: 208_000 },
      { date: "2024-06-06", value: 206_000 },
      { date: "2024-06-07", value: 210_000 },
    ]);

    // Live has lower gains due to slippage
    const live = [
      makeSnap("2024-06-03", 200_000),
      makeSnap("2024-06-04", 202_000, 2000), // less than backtest
      makeSnap("2024-06-05", 204_000, 2000),
      makeSnap("2024-06-06", 203_000, -1000),
      makeSnap("2024-06-07", 205_000, 2000),
    ];

    const result = service.compare({ liveSnapshots: live, backtestResult: bt });

    expect(result.stats.returnGapPct).toBeLessThan(0); // underperformance
    expect(result.stats.overlapDays).toBe(5);
  });

  it("simulates weekend/holiday gaps between live and backtest", () => {
    // Backtest runs on all calendar days (hypothetical)
    const bt = makeBacktest([
      { date: "2024-01-08", value: 100_000 }, // Mon
      { date: "2024-01-09", value: 101_000 }, // Tue
      { date: "2024-01-10", value: 102_000 }, // Wed
      { date: "2024-01-11", value: 101_500 }, // Thu
      { date: "2024-01-12", value: 103_000 }, // Fri
      { date: "2024-01-13", value: 103_500 }, // Sat (no live)
      { date: "2024-01-14", value: 104_000 }, // Sun (no live)
      { date: "2024-01-15", value: 104_500 }, // Mon
    ]);

    // Live only has trading days
    const live = [
      makeSnap("2024-01-08", 100_000),
      makeSnap("2024-01-09", 101_200),
      makeSnap("2024-01-10", 102_500),
      makeSnap("2024-01-11", 101_800),
      makeSnap("2024-01-12", 103_500),
      // No Sat/Sun
      makeSnap("2024-01-15", 105_000),
    ];

    const result = service.compare({ liveSnapshots: live, backtestResult: bt });

    expect(result.alignedSeries).toHaveLength(6); // 6 common dates (Mon-Fri + next Mon)
    expect(result.backtestMissingDates).toEqual(["2024-01-13", "2024-01-14"]);
    expect(result.liveMissingDates).toHaveLength(0);
  });

  it("end-to-end: uses EquityHistory-compatible snapshots with BacktestResult format", () => {
    // Realistic EquitySnapshot with all fields
    const liveSnapshots: EquitySnapshot[] = [
      {
        date: "2024-04-01",
        equity: 300_000,
        cash: 150_000,
        marketValue: 150_000,
        dailyPnl: 0,
        positionCount: 5,
        benchmark: 3500,
      },
      {
        date: "2024-04-02",
        equity: 305_000,
        cash: 155_000,
        marketValue: 150_000,
        dailyPnl: 5000,
        positionCount: 5,
        benchmark: 3520,
      },
      {
        date: "2024-04-03",
        equity: 298_000,
        cash: 148_000,
        marketValue: 150_000,
        dailyPnl: -7000,
        positionCount: 5,
        benchmark: 3480,
      },
      {
        date: "2024-04-04",
        equity: 310_000,
        cash: 160_000,
        marketValue: 150_000,
        dailyPnl: 12000,
        positionCount: 5,
        benchmark: 3550,
      },
    ];

    // Full BacktestResult object
    const backtestResult: BacktestResult = {
      strategyId: "momentum-alpha",
      symbols: ["600519", "000858", "002594"],
      startDate: "2024-04-01",
      endDate: "2024-04-04",
      initialCash: 300_000,
      finalValue: 312_000,
      totalReturn: 0.04,
      annualizedReturn: 0.15,
      maxDrawdown: 0.02,
      sharpeRatio: 2.1,
      winRate: 0.65,
      totalTrades: 12,
      signals: [],
      equityCurve: [
        { date: "2024-04-01", value: 300_000 },
        { date: "2024-04-02", value: 303_000 },
        { date: "2024-04-03", value: 301_000 },
        { date: "2024-04-04", value: 312_000 },
      ],
    };

    const result = service.compare({
      liveSnapshots,
      backtestResult,
      driftThreshold: 3,
    });

    expect(result.stats.overlapDays).toBe(4);
    expect(result.alignedSeries).toHaveLength(4);

    // Verify live return: 300k → 310k = 3.33%
    expect(result.stats.liveReturnPct).toBeCloseTo(3.33, 1);
    // Verify backtest return: 300k → 312k = 4%
    expect(result.stats.backtestReturnPct).toBeCloseTo(4.0, 1);
    // Live slightly underperforms
    expect(result.stats.returnGapPct).toBeLessThan(0);

    // Both series have valid drawdown
    expect(result.stats.liveMaxDrawdownPct).toBeGreaterThan(0);

    // All stats should be finite
    for (const [, value] of Object.entries(result.stats)) {
      expect(Number.isFinite(value)).toBe(true);
    }
  });
});
