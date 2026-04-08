/**
 * Tests for LiveBacktestCompareService 66live vs backtest performance comparison.
 *
 * Covers:
 *  - FR-8.1: Live execution performance metrics (slippage, signal delay)
 *  - FR-8.2: Automatic alignment of live and backtest equity curves
 *  - FR-8.3: Strategy decay indicator (return deviation)
 *  - FR-8.4: Decay alert when deviation exceeds threshold (default 20%)
 *  - AC-6: Overlaid curves, deviation threshold alert, slippage stats
 *  - B6: Partial time overlap handling
 */
import { describe, expect, it } from "vitest";
import {
  LiveBacktestCompareService,
  alignCurves,
  calcCorrelation,
  type CompareDataSource,
  type CurvePoint,
} from "./live-backtest-compare.js";

// ── Mock data ───────────────────────────────────────────────

function makeCurve(start: number, dailyReturn: number, days: number, startDate = "2026-01-01"): CurvePoint[] {
  const curve: CurvePoint[] = [];
  let equity = start;
  const d = new Date(startDate);
  for (let i = 0; i < days; i++) {
    curve.push({ date: d.toISOString().slice(0, 10), equity });
    equity *= 1 + dailyReturn;
    d.setDate(d.getDate() + 1);
  }
  return curve;
}

function mockDataSource(opts?: {
  liveReturn?: number;
  btReturn?: number;
  liveDays?: number;
  btDays?: number;
  liveStart?: string;
  btStart?: string;
  slippages?: number[];
  delays?: number[];
}): CompareDataSource {
  const {
    liveReturn = 0.001,
    btReturn = 0.002,
    liveDays = 60,
    btDays = 60,
    liveStart = "2026-01-01",
    btStart = "2026-01-01",
    slippages = [5, 10, 8, 3, 7],
    delays = [100, 200, 150, 50, 300],
  } = opts ?? {};

  return {
    getLiveEquityCurve: async () => makeCurve(100000, liveReturn, liveDays, liveStart),
    getBacktestEquityCurve: async () => makeCurve(100000, btReturn, btDays, btStart),
    getLiveSlippages: async () => slippages,
    getLiveSignalDelays: async () => delays,
  };
}

// ── alignCurves tests ───────────────────────────────────────

describe("alignCurves()", () => {
  it("aligns two identical-date curves", () => {
    const live = makeCurve(100, 0.01, 5);
    const bt = makeCurve(100, 0.02, 5);
    const { alignedLive, alignedBacktest } = alignCurves(live, bt);
    expect(alignedLive).toHaveLength(5);
    expect(alignedBacktest).toHaveLength(5);
  });

  it("takes intersection when dates partially overlap (B6)", () => {
    const live = makeCurve(100, 0.01, 5, "2026-01-03"); // Jan 3-7
    const bt = makeCurve(100, 0.02, 5, "2026-01-01");   // Jan 1-5
    const { alignedLive, alignedBacktest } = alignCurves(live, bt);
    // Overlap: Jan 3, 4, 5
    expect(alignedLive).toHaveLength(3);
    expect(alignedBacktest).toHaveLength(3);
    expect(alignedLive[0].date).toBe("2026-01-03");
  });

  it("returns empty for no overlap", () => {
    const live = makeCurve(100, 0.01, 5, "2026-01-10");
    const bt = makeCurve(100, 0.02, 5, "2026-01-01");
    const { alignedLive, alignedBacktest } = alignCurves(live, bt);
    expect(alignedLive).toHaveLength(0);
    expect(alignedBacktest).toHaveLength(0);
  });
});

// ── calcCorrelation tests ───────────────────────────────────

describe("calcCorrelation()", () => {
  it("returns 1 for perfectly correlated series", () => {
    const xs = [1, 2, 3, 4, 5];
    const ys = [10, 20, 30, 40, 50];
    expect(calcCorrelation(xs, ys)).toBeCloseTo(1, 4);
  });

  it("returns -1 for perfectly inverse correlated series", () => {
    const xs = [1, 2, 3, 4, 5];
    const ys = [50, 40, 30, 20, 10];
    expect(calcCorrelation(xs, ys)).toBeCloseTo(-1, 4);
  });

  it("returns 0 for insufficient data", () => {
    expect(calcCorrelation([1], [2])).toBe(0);
    expect(calcCorrelation([], [])).toBe(0);
  });
});

// ── LiveBacktestCompareService tests ────────────────────────

describe("LiveBacktestCompareService", () => {
  it("compares live and backtest with all metrics", async () => {
    const svc = new LiveBacktestCompareService(mockDataSource());
    const result = await svc.compare({
      strategyId: "macd",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-01",
    });

    expect(result.strategyId).toBe("macd");
    expect(result.period.from).toBe("2026-01-01");

    // Live metrics
    expect(result.live.totalReturn).toBeDefined();
    expect(result.live.sharpeRatio).toBeDefined();
    expect(result.live.maxDrawdown).toBeGreaterThanOrEqual(0);
    expect(result.live.avgSlippage).toBeGreaterThan(0);
    expect(result.live.avgSignalDelay).toBeGreaterThan(0);
    expect(result.live.equityCurve.length).toBeGreaterThan(0);

    // Backtest metrics
    expect(result.backtest.totalReturn).toBeDefined();
    expect(result.backtest.equityCurve.length).toBeGreaterThan(0);

    // Deviation
    expect(result.deviation.returnDeviation).toBeDefined();
    expect(result.deviation.curveCorrelation).toBeDefined();
  });

  it("detects positive curve correlation when trends align", async () => {
    const svc = new LiveBacktestCompareService(
      mockDataSource({ liveReturn: 0.001, btReturn: 0.001 }),
    );
    const result = await svc.compare({
      strategyId: "trend",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-01",
    });

    // Similar returns should have high positive correlation
    expect(result.deviation.curveCorrelation).toBeGreaterThan(0.9);
  });

  it("calculates slippage statistics correctly (FR-8.1, AC-6)", async () => {
    const svc = new LiveBacktestCompareService(
      mockDataSource({ slippages: [5, 10, 15, 20, 25] }),
    );
    const result = await svc.compare({
      strategyId: "macd",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-01",
    });

    expect(result.live.avgSlippage).toBe(15); // mean of [5,10,15,20,25]
  });

  it("calculates signal delay correctly (FR-8.1)", async () => {
    const svc = new LiveBacktestCompareService(
      mockDataSource({ delays: [100, 200, 300] }),
    );
    const result = await svc.compare({
      strategyId: "macd",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-01",
    });

    expect(result.live.avgSignalDelay).toBe(200);
  });

  it("triggers decay alert when deviation exceeds threshold (FR-8.4, AC-6)", async () => {
    // Live: 0.001/day →~6.2% over 60 days
    // Backtest: 0.005/day →~34.9% over 60 days
    // Deviation = (6.2/34.9) - 1 ≈-82% →exceeds 20%
    const svc = new LiveBacktestCompareService(
      mockDataSource({ liveReturn: 0.001, btReturn: 0.005 }),
      { returnDeviationThreshold: 0.2 },
    );

    const result = await svc.compare({
      strategyId: "decaying-strat",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-01",
    });

    expect(result.deviation.decayAlertTriggered).toBe(true);
    const alerts = svc.getDecayAlerts();
    expect(alerts).toHaveLength(1);
    expect(alerts[0].strategyId).toBe("decaying-strat");
    expect(alerts[0].message).toContain("deviates");
  });

  it("does not trigger decay alert when deviation is within threshold", async () => {
    // Similar returns →small deviation
    const svc = new LiveBacktestCompareService(
      mockDataSource({ liveReturn: 0.001, btReturn: 0.0012 }),
      { returnDeviationThreshold: 0.5 }, // generous threshold
    );

    const result = await svc.compare({
      strategyId: "stable-strat",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-01",
    });

    expect(result.deviation.decayAlertTriggered).toBe(false);
    expect(svc.getDecayAlerts()).toHaveLength(0);
  });

  it("supports per-strategy threshold overrides", async () => {
    const svc = new LiveBacktestCompareService(
      mockDataSource({ liveReturn: 0.0019, btReturn: 0.002 }),
      {
        returnDeviationThreshold: 0.05,
        perStrategyOverrides: { "trend-follow": 0.95 }, // very lenient
      },
    );

    const result = await svc.compare({
      strategyId: "trend-follow",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-01",
    });

    // With lenient threshold for this strategy, should not alert
    expect(result.deviation.decayAlertTriggered).toBe(false);
  });

  it("handles empty slippage/delay data gracefully", async () => {
    const svc = new LiveBacktestCompareService(
      mockDataSource({ slippages: [], delays: [] }),
    );

    const result = await svc.compare({
      strategyId: "macd",
      dateFrom: "2026-01-01",
      dateTo: "2026-03-01",
    });

    expect(result.live.avgSlippage).toBe(0);
    expect(result.live.avgSignalDelay).toBe(0);
  });

  it("clearAlerts() resets accumulated alerts", async () => {
    const svc = new LiveBacktestCompareService(
      mockDataSource({ liveReturn: 0.001, btReturn: 0.01 }),
      { returnDeviationThreshold: 0.05 },
    );

    await svc.compare({ strategyId: "s1", dateFrom: "2026-01-01", dateTo: "2026-03-01" });
    expect(svc.getDecayAlerts()).toHaveLength(1);

    svc.clearAlerts();
    expect(svc.getDecayAlerts()).toHaveLength(0);
  });

  it("accumulates multiple alerts across comparisons", async () => {
    const svc = new LiveBacktestCompareService(
      mockDataSource({ liveReturn: 0.001, btReturn: 0.01 }),
      { returnDeviationThreshold: 0.05 },
    );

    await svc.compare({ strategyId: "s1", dateFrom: "2026-01-01", dateTo: "2026-03-01" });
    await svc.compare({ strategyId: "s2", dateFrom: "2026-01-01", dateTo: "2026-03-01" });
    expect(svc.getDecayAlerts()).toHaveLength(2);
    expect(svc.getDecayAlerts()[0].strategyId).toBe("s1");
    expect(svc.getDecayAlerts()[1].strategyId).toBe("s2");
  });
});
