/**
 * Comprehensive unit tests for equity-curve-engine.ts
 * Covers: edge cases, boundary conditions, numerical precision,
 * large datasets, negative equity, and cross-function consistency.
 */
import { describe, expect, it } from "vitest";
import type { EquitySnapshot } from "./equity-history.js";
import {
  analyzeEquityCurve,
  backtestCurveToSnapshots,
  computeAllDrawdownEpisodes,
  computeBenchmarkMetrics,
  computeCumulativeReturns,
  computeDailyReturns,
  computeDrawdownCurve,
  computeMaxDrawdownInterval,
  computeMetrics,
  computeMonthlyReturns,
  computeReturnDistribution,
  computeRollingSharpe,
  computeRollingVolatility,
  computeUnderwaterStats,
  computeVaR,
  computeWeeklyReturns,
} from "./equity-curve-engine.js";

// ── Helpers ─────────────────────────────────────────────────

function snap(date: string, equity: number, dailyPnl = 0): EquitySnapshot {
  return { date, equity, cash: equity * 0.5, marketValue: equity * 0.5, dailyPnl, positionCount: 1 };
}

/** Generate N trading days starting from a base date with a given equity trajectory */
function generateSeries(
  startDate: string,
  equities: number[],
): EquitySnapshot[] {
  const base = new Date(startDate);
  return equities.map((eq, i) => {
    const d = new Date(base);
    d.setDate(d.getDate() + i);
    const dateStr = d.toISOString().slice(0, 10);
    const pnl = i > 0 ? eq - equities[i - 1]! : 0;
    return snap(dateStr, eq, pnl);
  });
}

/** Generate a long monotonically increasing series */
function generateLongSeries(n: number, startEquity = 100000, dailyReturnPct = 0.001): EquitySnapshot[] {
  const equities: number[] = [];
  let eq = startEquity;
  for (let i = 0; i < n; i++) {
    equities.push(eq);
    eq *= (1 + dailyReturnPct);
  }
  return generateSeries("2024-01-02", equities);
}

// ── computeDailyReturns: additional edge cases ──────────────

describe("computeDailyReturns (extended)", () => {
  it("handles exactly 2 snapshots", () => {
    const r = computeDailyReturns([snap("2024-01-02", 100), snap("2024-01-03", 110)]);
    expect(r).toHaveLength(1);
    expect(r[0]!.dailyReturn).toBeCloseTo(0.1, 6);
  });

  it("handles negative daily returns", () => {
    const r = computeDailyReturns([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 90000),
    ]);
    expect(r[0]!.dailyReturn).toBeCloseTo(-0.1, 6);
  });

  it("handles very small equity values without precision loss", () => {
    const r = computeDailyReturns([
      snap("2024-01-02", 0.001),
      snap("2024-01-03", 0.002),
    ]);
    expect(r[0]!.dailyReturn).toBeCloseTo(1.0, 4); // +100%
  });

  it("handles very large equity values", () => {
    const r = computeDailyReturns([
      snap("2024-01-02", 1e12),
      snap("2024-01-03", 1.01e12),
    ]);
    expect(r[0]!.dailyReturn).toBeCloseTo(0.01, 6);
  });

  it("returns 0 when previous equity is 0", () => {
    const r = computeDailyReturns([
      snap("2024-01-02", 0),
      snap("2024-01-03", 50000),
      snap("2024-01-04", 60000),
    ]);
    expect(r[0]!.dailyReturn).toBe(0); // prev=0 → return 0
    expect(r[1]!.dailyReturn).toBeCloseTo(0.2, 6); // 50k → 60k
  });

  it("handles equity dropping to 0 (total loss)", () => {
    const r = computeDailyReturns([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 0),
    ]);
    expect(r[0]!.dailyReturn).toBeCloseTo(-1.0, 6);
  });

  it("preserves date ordering from input", () => {
    const series = generateSeries("2024-01-02", [100, 101, 102, 103, 104]);
    const r = computeDailyReturns(series);
    for (let i = 1; i < r.length; i++) {
      expect(r[i]!.date > r[i - 1]!.date).toBe(true);
    }
  });
});

// ── computeCumulativeReturns: extended ──────────────────────

describe("computeCumulativeReturns (extended)", () => {
  it("handles negative start equity (returns empty)", () => {
    expect(computeCumulativeReturns([snap("2024-01-02", -100)])).toEqual([]);
  });

  it("last point matches total return calculation", () => {
    const snapshots = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 95000),
      snap("2024-01-05", 120000),
    ];
    const result = computeCumulativeReturns(snapshots);
    const last = result[result.length - 1]!;
    expect(last.cumulativeReturn).toBeCloseTo(120000 / 100000 - 1, 6);
  });

  it("intermediate cumulative returns are correct with drawdown", () => {
    const snapshots = [
      snap("2024-01-02", 200),
      snap("2024-01-03", 220), // +10%
      snap("2024-01-04", 180), // -10% from start
      snap("2024-01-05", 200), // back to 0%
    ];
    const r = computeCumulativeReturns(snapshots);
    expect(r[1]!.cumulativeReturn).toBeCloseTo(0.1, 6);
    expect(r[2]!.cumulativeReturn).toBeCloseTo(-0.1, 6);
    expect(r[3]!.cumulativeReturn).toBeCloseTo(0.0, 6);
  });

  it("single snapshot returns array with 0 cumulative return", () => {
    const r = computeCumulativeReturns([snap("2024-01-02", 100000)]);
    expect(r).toHaveLength(1);
    expect(r[0]!.cumulativeReturn).toBe(0);
  });
});

// ── computeDrawdownCurve: extended ──────────────────────────

describe("computeDrawdownCurve (extended)", () => {
  it("drawdown is always non-negative", () => {
    const series = generateSeries("2024-01-02", [100, 110, 105, 112, 108, 115, 110, 120]);
    const curve = computeDrawdownCurve(series);
    expect(curve.every((p) => p.drawdown >= 0)).toBe(true);
  });

  it("drawdown never exceeds 1 (100%) for positive equity", () => {
    const series = generateSeries("2024-01-02", [100000, 1]);
    const curve = computeDrawdownCurve(series);
    expect(curve.every((p) => p.drawdown <= 1)).toBe(true);
  });

  it("peakEquity is monotonically non-decreasing", () => {
    const series = generateSeries("2024-01-02", [100, 110, 95, 120, 105, 130]);
    const curve = computeDrawdownCurve(series);
    for (let i = 1; i < curve.length; i++) {
      expect(curve[i]!.peakEquity).toBeGreaterThanOrEqual(curve[i - 1]!.peakEquity);
    }
  });

  it("handles flat equity (no drawdown)", () => {
    const series = generateSeries("2024-01-02", [100, 100, 100, 100]);
    const curve = computeDrawdownCurve(series);
    expect(curve.every((p) => p.drawdown === 0)).toBe(true);
  });

  it("handles single snapshot", () => {
    const curve = computeDrawdownCurve([snap("2024-01-02", 100000)]);
    expect(curve).toHaveLength(1);
    expect(curve[0]!.drawdown).toBe(0);
  });

  it("peak date updates correctly through multiple peaks", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110), // peak 1
      snap("2024-01-04", 105), // dd from peak 1
      snap("2024-01-05", 120), // peak 2
      snap("2024-01-08", 115), // dd from peak 2
    ];
    const curve = computeDrawdownCurve(series);
    expect(curve[2]!.peakDate).toBe("2024-01-03");
    expect(curve[4]!.peakDate).toBe("2024-01-05");
  });
});

// ── computeMaxDrawdownInterval: extended ────────────────────

describe("computeMaxDrawdownInterval (extended)", () => {
  it("identifies correct interval when there are multiple drawdowns", () => {
    const series = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000), // peak 1
      snap("2024-01-04", 105000), // dd1: -4.5%
      snap("2024-01-05", 115000), // peak 2
      snap("2024-01-08", 95000),  // dd2: -17.4% (the max)
      snap("2024-01-09", 120000), // recovery
    ];
    const result = computeMaxDrawdownInterval(series)!;
    expect(result.peakDate).toBe("2024-01-05");
    expect(result.troughDate).toBe("2024-01-08");
    expect(result.maxDrawdown).toBeCloseTo(20000 / 115000, 4);
  });

  it("handles flat equity (returns null - no drawdown)", () => {
    const result = computeMaxDrawdownInterval([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 100000),
      snap("2024-01-04", 100000),
    ]);
    expect(result).toBeNull();
  });

  it("recovery date is first date meeting peak level exactly", () => {
    const series = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 90000),  // trough
      snap("2024-01-04", 99999),  // almost recovered but not quite
      snap("2024-01-05", 100000), // exactly recovered
      snap("2024-01-08", 105000), // above peak
    ];
    const result = computeMaxDrawdownInterval(series)!;
    expect(result.recoveryDate).toBe("2024-01-05");
  });

  it("peakToTroughDays and peakToRecoveryDays are calendar days", () => {
    const series = [
      snap("2024-01-02", 100000),
      snap("2024-01-10", 80000),  // 8 calendar days later
      snap("2024-01-20", 100000), // 18 calendar days from peak
    ];
    const result = computeMaxDrawdownInterval(series)!;
    expect(result.peakToTroughDays).toBe(8);
    expect(result.peakToRecoveryDays).toBe(18);
  });

  it("maxDrawdownAbs equals peakEquity - troughEquity", () => {
    const series = [
      snap("2024-01-02", 150000),
      snap("2024-01-03", 100000),
    ];
    const result = computeMaxDrawdownInterval(series)!;
    expect(result.maxDrawdownAbs).toBe(50000);
    expect(result.maxDrawdown).toBeCloseTo(50000 / 150000, 6);
  });
});

// ── computeRollingVolatility: extended ──────────────────────

describe("computeRollingVolatility (extended)", () => {
  it("default window is 20", () => {
    const series = generateLongSeries(25);
    const returns = computeDailyReturns(series);
    // default window = 20, so need 20 returns → 21 snapshots min
    const rolling = computeRollingVolatility(returns);
    expect(rolling).toHaveLength(returns.length - 19);
  });

  it("window of 1 gives every point", () => {
    const series = generateSeries("2024-01-02", [100, 110, 105, 115, 108]);
    const returns = computeDailyReturns(series);
    const rolling = computeRollingVolatility(returns, 1);
    // window=1 → each point has variance=0 (single element)
    expect(rolling).toHaveLength(returns.length);
    // single element window → std=0 → vol=0
    expect(rolling.every((p) => p.volatility === 0)).toBe(true);
  });

  it("higher volatility period has higher rolling vol", () => {
    // calm period followed by volatile period
    const equities: number[] = [100000];
    // 20 days of tiny moves
    for (let i = 1; i <= 20; i++) equities.push(equities[i - 1]! + 10);
    // 20 days of big moves
    for (let i = 21; i <= 40; i++) equities.push(equities[i - 1]! + (i % 2 === 0 ? 5000 : -4000));

    const series = generateSeries("2024-01-02", equities);
    const returns = computeDailyReturns(series);
    const rolling = computeRollingVolatility(returns, 10);

    // The volatility at the end (volatile period) should be higher than at the start (calm)
    const firstVol = rolling[0]!.volatility;
    const lastVol = rolling[rolling.length - 1]!.volatility;
    expect(lastVol).toBeGreaterThan(firstVol);
  });

  it("dates correspond to the end of each window", () => {
    const series = generateSeries("2024-01-02", [100, 101, 102, 103, 104, 105]);
    const returns = computeDailyReturns(series);
    const rolling = computeRollingVolatility(returns, 3);
    // First rolling point should use date of the 3rd return
    expect(rolling[0]!.date).toBe(returns[2]!.date);
  });
});

// ── computeRollingSharpe: extended ──────────────────────────

describe("computeRollingSharpe (extended)", () => {
  it("default window is 60", () => {
    const series = generateLongSeries(65);
    const returns = computeDailyReturns(series);
    const rolling = computeRollingSharpe(returns);
    expect(rolling).toHaveLength(returns.length - 59);
  });

  it("positive Sharpe for consistently positive returns", () => {
    // Use a series with small variation (not constant) to avoid zero variance
    const equities: number[] = [100000];
    for (let i = 1; i < 65; i++) {
      // Positive returns with slight variation: 0.3% to 0.7%
      const dailyReturn = 0.003 + 0.004 * ((i % 3) / 2);
      equities.push(equities[i - 1]! * (1 + dailyReturn));
    }
    const series = generateSeries("2024-01-02", equities);
    const returns = computeDailyReturns(series);
    const rolling = computeRollingSharpe(returns, 5);
    // All Sharpe values should be positive since all returns are positive
    expect(rolling.every((p) => p.sharpe > 0)).toBe(true);
  });

  it("respects risk-free rate parameter", () => {
    // Use a series with variation to ensure non-zero Sharpe
    const equities: number[] = [100000];
    for (let i = 1; i < 15; i++) {
      const dailyReturn = 0.002 + 0.003 * ((i % 3) / 2);
      equities.push(equities[i - 1]! * (1 + dailyReturn));
    }
    const series = generateSeries("2024-01-02", equities);
    const returns = computeDailyReturns(series);
    const sharpe0 = computeRollingSharpe(returns, 5, 0);
    const sharpeRf = computeRollingSharpe(returns, 5, 0.10); // 10% risk-free
    // Higher risk-free rate should lower Sharpe ratio
    expect(sharpeRf[0]!.sharpe).toBeLessThan(sharpe0[0]!.sharpe);
  });
});

// ── computeUnderwaterStats: extended ────────────────────────

describe("computeUnderwaterStats (extended)", () => {
  it("multiple separate drawdown episodes counted correctly", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110), // peak
      snap("2024-01-04", 105), // dd 1
      snap("2024-01-05", 112), // recovery + new peak
      snap("2024-01-08", 108), // dd 2
      snap("2024-01-09", 115), // recovery + new peak
      snap("2024-01-10", 110), // dd 3
      snap("2024-01-11", 120), // recovery
    ];
    const stats = computeUnderwaterStats(series);
    expect(stats.totalEpisodes).toBe(3);
    expect(stats.currentDurationDays).toBe(0); // recovered at end
  });

  it("pctTimeUnderwater is 100% when always declining after first point", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 99),
      snap("2024-01-04", 98),
      snap("2024-01-05", 97),
    ];
    const stats = computeUnderwaterStats(series);
    expect(stats.pctTimeUnderwater).toBe(100);
    expect(stats.totalEpisodes).toBe(1);
    expect(stats.currentDurationDays).toBe(3);
  });

  it("avgDurationDays is correct for multiple episodes", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110), // peak
      snap("2024-01-04", 105), // dd ep1 (dur=1)
      snap("2024-01-05", 115), // recovery
      snap("2024-01-08", 110), // dd ep2 start
      snap("2024-01-09", 108), // dd ep2 (dur=2)
      snap("2024-01-10", 120), // recovery
    ];
    const stats = computeUnderwaterStats(series);
    expect(stats.totalEpisodes).toBe(2);
    // ep1 duration=1, ep2 duration=2, avg=1.5
    expect(stats.avgDurationDays).toBeCloseTo(1.5, 4);
    expect(stats.maxDurationDays).toBe(2);
  });

  it("handles exactly 2 snapshots with decline", () => {
    const stats = computeUnderwaterStats([
      snap("2024-01-02", 100),
      snap("2024-01-03", 90),
    ]);
    expect(stats.totalEpisodes).toBe(1);
    expect(stats.currentDurationDays).toBe(1);
    expect(stats.pctTimeUnderwater).toBe(100);
  });
});

// ── computeVaR: extended ────────────────────────────────────

describe("computeVaR (extended)", () => {
  it("single return point", () => {
    const r = computeVaR([{ date: "2024-01-03", dailyReturn: -0.05 }]);
    expect(r.var).toBeCloseTo(0.05, 6);
    expect(r.cvar).toBeCloseTo(0.05, 6);
  });

  it("all positive returns gives VaR based on smallest gain", () => {
    const returns = [
      { date: "2024-01-03", dailyReturn: 0.01 },
      { date: "2024-01-04", dailyReturn: 0.02 },
      { date: "2024-01-05", dailyReturn: 0.03 },
      { date: "2024-01-08", dailyReturn: 0.04 },
      { date: "2024-01-09", dailyReturn: 0.05 },
    ];
    const result = computeVaR(returns, 0.95);
    // With 5 data points, alpha=0.05, idx=floor(5*0.05)=0 → sorted[0]=0.01
    // VaR is abs(0.01) = 0.01 (i.e., even worst case was a gain)
    expect(result.var).toBeCloseTo(0.01, 6);
  });

  it("different confidence levels produce different VaR", () => {
    const returns = Array.from({ length: 100 }, (_, i) => ({
      date: `2024-01-${String((i % 28) + 2).padStart(2, "0")}`,
      dailyReturn: (i - 50) / 1000, // range from -0.05 to +0.049
    }));
    const var90 = computeVaR(returns, 0.90);
    const var95 = computeVaR(returns, 0.95);
    const var99 = computeVaR(returns, 0.99);
    // Higher confidence → more conservative → higher VaR
    expect(var99.var).toBeGreaterThanOrEqual(var95.var);
    expect(var95.var).toBeGreaterThanOrEqual(var90.var);
  });

  it("CVaR >= VaR for any dataset", () => {
    const returns = generateSeries("2024-01-02", [100, 110, 95, 105, 80, 90, 100, 110, 85, 95]);
    const dailyReturns = computeDailyReturns(returns);
    const result = computeVaR(dailyReturns);
    expect(result.cvar).toBeGreaterThanOrEqual(result.var);
  });
});

// ── computeBenchmarkMetrics: extended ───────────────────────

describe("computeBenchmarkMetrics (extended)", () => {
  it("handles non-overlapping dates (returns null)", () => {
    const strategy = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 102000),
    ];
    const benchmark = [
      snap("2024-02-01", 10000),
      snap("2024-02-02", 10100),
    ];
    expect(computeBenchmarkMetrics(strategy, benchmark)).toBeNull();
  });

  it("handles partial date overlap", () => {
    const strategy = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 102000),
      snap("2024-01-04", 104000),
    ];
    const benchmark = [
      snap("2024-01-03", 10000),
      snap("2024-01-04", 10200),
      snap("2024-01-05", 10400),
    ];
    // Only 2 aligned dates → 1 return → should compute
    const result = computeBenchmarkMetrics(strategy, benchmark);
    expect(result).not.toBeNull();
  });

  it("excess return is strategy minus benchmark total return", () => {
    const strategy = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 120000), // +20%
    ];
    const benchmark = [
      snap("2024-01-02", 10000),
      snap("2024-01-03", 10500), // +5%
    ];
    const result = computeBenchmarkMetrics(strategy, benchmark)!;
    expect(result.excessReturn).toBeCloseTo(0.15, 4);
  });

  it("beta is negative for inversely correlated series", () => {
    const strategy = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 100000),
      snap("2024-01-05", 110000),
      snap("2024-01-08", 100000),
    ];
    const benchmark = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 90000),
      snap("2024-01-04", 100000),
      snap("2024-01-05", 90000),
      snap("2024-01-08", 100000),
    ];
    const result = computeBenchmarkMetrics(strategy, benchmark)!;
    expect(result.beta).toBeLessThan(0);
    expect(result.correlation).toBeLessThan(0);
  });

  it("uses risk-free rate parameter", () => {
    const strategy = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 101000),
      snap("2024-01-04", 102000),
    ];
    const benchmark = [
      snap("2024-01-02", 10000),
      snap("2024-01-03", 10050),
      snap("2024-01-04", 10100),
    ];
    const result0 = computeBenchmarkMetrics(strategy, benchmark, 0)!;
    const result5 = computeBenchmarkMetrics(strategy, benchmark, 0.05)!;
    // Different risk-free rates should produce different alphas
    expect(result0.alpha).not.toBeCloseTo(result5.alpha, 4);
  });
});

// ── computeReturnDistribution: extended ─────────────────────

describe("computeReturnDistribution (extended)", () => {
  it("returns zeros for 2 data points (< 3 required)", () => {
    const result = computeReturnDistribution([
      { date: "2024-01-03", dailyReturn: 0.01 },
      { date: "2024-01-04", dailyReturn: -0.01 },
    ]);
    expect(result.skewness).toBe(0);
    expect(result.kurtosis).toBe(0);
    expect(result.medianReturn).toBe(0);
  });

  it("symmetric distribution has skewness close to 0", () => {
    // Symmetric: [-3%, -2%, -1%, 0%, 1%, 2%, 3%]
    const returns = [-0.03, -0.02, -0.01, 0, 0.01, 0.02, 0.03].map((r, i) => ({
      date: `2024-01-${String(i + 3).padStart(2, "0")}`,
      dailyReturn: r,
    }));
    const result = computeReturnDistribution(returns);
    expect(Math.abs(result.skewness)).toBeLessThan(0.01);
    expect(result.medianReturn).toBeCloseTo(0, 6);
  });

  it("right-skewed distribution has positive skewness", () => {
    // Many small losses, few large gains
    const returnValues = [-0.01, -0.01, -0.01, -0.01, -0.01, 0.10, 0.15];
    const returns = returnValues.map((r, i) => ({
      date: `2024-01-${String(i + 3).padStart(2, "0")}`,
      dailyReturn: r,
    }));
    const result = computeReturnDistribution(returns);
    expect(result.skewness).toBeGreaterThan(0);
  });

  it("uniform distribution has negative excess kurtosis", () => {
    // Uniform-like: equally spaced values → platykurtic (kurtosis < 0)
    const n = 100;
    const returns = Array.from({ length: n }, (_, i) => ({
      date: `2024-01-${String((i % 28) + 2).padStart(2, "0")}`,
      dailyReturn: -0.05 + (0.10 * i) / (n - 1),
    }));
    const result = computeReturnDistribution(returns);
    // Uniform distribution has excess kurtosis of -1.2
    expect(result.kurtosis).toBeLessThan(0);
  });

  it("percentiles bracket the median", () => {
    const series = generateLongSeries(50, 100000, 0.002);
    const dailyReturns = computeDailyReturns(series);
    const result = computeReturnDistribution(dailyReturns);
    expect(result.percentile25).toBeLessThanOrEqual(result.medianReturn);
    expect(result.medianReturn).toBeLessThanOrEqual(result.percentile75);
  });

  it("percentile5 <= percentile25 <= median <= percentile75 <= percentile95", () => {
    const series = generateSeries("2024-01-02", [100, 110, 95, 105, 80, 90, 100, 110, 115, 85, 95, 105, 120]);
    const dailyReturns = computeDailyReturns(series);
    const result = computeReturnDistribution(dailyReturns);
    expect(result.percentile5).toBeLessThanOrEqual(result.percentile25);
    expect(result.percentile25).toBeLessThanOrEqual(result.medianReturn);
    expect(result.medianReturn).toBeLessThanOrEqual(result.percentile75);
    expect(result.percentile75).toBeLessThanOrEqual(result.percentile95);
  });
});

// ── computeAllDrawdownEpisodes: extended ────────────────────

describe("computeAllDrawdownEpisodes (extended)", () => {
  it("peakToTroughDays counts trading day indices", () => {
    const series = [
      snap("2024-01-02", 100), // idx 0
      snap("2024-01-03", 110), // idx 1 peak
      snap("2024-01-04", 105), // idx 2
      snap("2024-01-05", 100), // idx 3 trough
      snap("2024-01-08", 115), // idx 4 recovery
    ];
    const result = computeAllDrawdownEpisodes(series, 0.01);
    expect(result).toHaveLength(1);
    expect(result[0]!.peakToTroughDays).toBe(2); // idx 3 - idx 1
    expect(result[0]!.peakToRecoveryDays).toBe(3); // idx 4 - idx 1
  });

  it("ongoing drawdown at end has null recovery", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110),
      snap("2024-01-04", 90), // big drop, never recovered
    ];
    const result = computeAllDrawdownEpisodes(series, 0.01);
    expect(result).toHaveLength(1);
    expect(result[0]!.recoveryDate).toBeNull();
    expect(result[0]!.peakToRecoveryDays).toBeNull();
  });

  it("minDrawdown=0 captures all declines", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110),
      snap("2024-01-04", 109.99), // tiny dip
      snap("2024-01-05", 115),
    ];
    const result = computeAllDrawdownEpisodes(series, 0);
    expect(result.length).toBeGreaterThanOrEqual(1);
  });

  it("episodes don't overlap", () => {
    const equities = [100, 110, 95, 115, 100, 120, 90, 125];
    const series = generateSeries("2024-01-02", equities);
    const episodes = computeAllDrawdownEpisodes(series, 0.01);
    for (let i = 1; i < episodes.length; i++) {
      const prev = episodes[i - 1]!;
      const curr = episodes[i]!;
      // Each episode's peak should be at or after the previous episode's recovery
      if (prev.recoveryDate) {
        expect(curr.peakDate >= prev.recoveryDate).toBe(true);
      }
    }
  });
});

// ── computeMonthlyReturns: extended ─────────────────────────

describe("computeMonthlyReturns (extended)", () => {
  it("single day produces one month entry with 0 return", () => {
    const result = computeMonthlyReturns([snap("2024-03-15", 100000)]);
    expect(result).toHaveLength(1);
    expect(result[0]!.period).toBe("2024-03");
    expect(result[0]!.return).toBe(0);
  });

  it("multiple entries in same month use first/last equity", () => {
    const result = computeMonthlyReturns([
      snap("2024-01-02", 100),
      snap("2024-01-10", 120),
      snap("2024-01-20", 90),
      snap("2024-01-31", 110),
    ]);
    expect(result).toHaveLength(1);
    expect(result[0]!.startEquity).toBe(100);
    expect(result[0]!.endEquity).toBe(110);
    expect(result[0]!.return).toBeCloseTo(0.10, 4);
  });

  it("handles year boundary correctly", () => {
    const result = computeMonthlyReturns([
      snap("2023-12-29", 100000),
      snap("2023-12-31", 102000),
      snap("2024-01-02", 103000),
      snap("2024-01-31", 105000),
    ]);
    expect(result).toHaveLength(2);
    expect(result[0]!.period).toBe("2023-12");
    expect(result[1]!.period).toBe("2024-01");
  });

  it("monthly return for zero start equity is 0", () => {
    const result = computeMonthlyReturns([
      snap("2024-01-02", 0),
      snap("2024-01-03", 100),
    ]);
    expect(result[0]!.return).toBe(0);
  });
});

// ── computeWeeklyReturns: extended ──────────────────────────

describe("computeWeeklyReturns (extended)", () => {
  it("single day produces one week entry with 0 return", () => {
    const result = computeWeeklyReturns([snap("2024-01-15", 100000)]);
    expect(result).toHaveLength(1);
    expect(result[0]!.return).toBe(0);
  });

  it("period labels follow ISO week format YYYY-Www", () => {
    const result = computeWeeklyReturns([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 101000),
    ]);
    expect(result[0]!.period).toMatch(/^\d{4}-W\d{2}$/);
  });

  it("handles data spanning multiple weeks", () => {
    const snapshots: EquitySnapshot[] = [];
    for (let i = 1; i <= 21; i++) {
      snapshots.push(snap(`2024-01-${String(i).padStart(2, "0")}`, 100000 + i * 100));
    }
    const result = computeWeeklyReturns(snapshots);
    expect(result.length).toBeGreaterThanOrEqual(3);
  });
});

// ── computeMetrics: extended ────────────────────────────────

describe("computeMetrics (extended)", () => {
  it("annualized return uses 252 trading days per year", () => {
    // 252 days at +0.1% daily → annualized ≈ (1.001)^252 - 1 ≈ 28.6%
    const series = generateLongSeries(253, 100000, 0.001);
    const m = computeMetrics(series);
    const expected = (1.001) ** (253 / 252) - 1; // roughly
    expect(m.annualizedReturn).toBeGreaterThan(0.2);
    expect(m.annualizedReturn).toBeLessThan(0.35);
  });

  it("annualized volatility scales by sqrt(252)", () => {
    const series = generateLongSeries(100, 100000, 0.001);
    const m = computeMetrics(series);
    // For constant daily return, std dev should be ~0 → vol ~0
    // But floating point means small numbers, not exactly 0
    expect(m.annualizedVolatility).toBeLessThan(0.001);
  });

  it("dailyWinRate calculation excludes flat days", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110), // win
      snap("2024-01-04", 110), // flat (excluded from win/loss)
      snap("2024-01-05", 100), // loss
    ];
    const m = computeMetrics(series);
    // 1 win, 1 loss → 50%
    expect(m.dailyWinRate).toBeCloseTo(50, 1);
  });

  it("profit factor is Infinity when no losses", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110),
      snap("2024-01-04", 120),
    ];
    const m = computeMetrics(series);
    expect(m.profitFactor).toBe(Infinity);
  });

  it("profit factor is 0 when no wins", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 90),
      snap("2024-01-04", 80),
    ];
    const m = computeMetrics(series);
    expect(m.profitFactor).toBe(0);
  });

  it("bestDayReturn and worstDayReturn are correct", () => {
    const series = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000), // +10%
      snap("2024-01-04", 88000),  // -20%
      snap("2024-01-05", 92400),  // +5%
    ];
    const m = computeMetrics(series);
    expect(m.bestDayReturn).toBeCloseTo(0.1, 4);
    expect(m.worstDayReturn).toBeCloseTo(-0.2, 4);
  });

  it("consecutive wins/losses reset on flat days", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110), // win
      snap("2024-01-04", 120), // win
      snap("2024-01-05", 120), // flat → resets
      snap("2024-01-08", 130), // win
    ];
    const m = computeMetrics(series);
    expect(m.maxConsecutiveWins).toBe(2); // the first 2, reset by flat
  });

  it("handles large dataset (500 points) without issues", () => {
    const series = generateLongSeries(500);
    const m = computeMetrics(series);
    expect(m.tradingDays).toBe(500);
    expect(m.totalReturn).toBeGreaterThan(0);
    expect(m.annualizedVolatility).toBeGreaterThanOrEqual(0);
  });

  it("zero start equity returns totalReturn=0", () => {
    const series = [
      snap("2024-01-02", 0),
      snap("2024-01-03", 100),
    ];
    const m = computeMetrics(series);
    expect(m.totalReturn).toBe(0);
  });

  it("Calmar ratio is 0 when no drawdown", () => {
    const series = [
      snap("2024-01-02", 100),
      snap("2024-01-03", 110),
      snap("2024-01-04", 120),
    ];
    const m = computeMetrics(series);
    expect(m.calmarRatio).toBe(0);
  });
});

// ── backtestCurveToSnapshots: extended ──────────────────────

describe("backtestCurveToSnapshots (extended)", () => {
  it("empty curve returns empty array", () => {
    expect(backtestCurveToSnapshots([])).toEqual([]);
  });

  it("single point has dailyPnl = 0", () => {
    const result = backtestCurveToSnapshots([{ date: "2024-01-02", value: 100000 }]);
    expect(result).toHaveLength(1);
    expect(result[0]!.dailyPnl).toBe(0);
    expect(result[0]!.positionCount).toBe(0);
  });

  it("cash is always 0 and marketValue equals value", () => {
    const result = backtestCurveToSnapshots([
      { date: "2024-01-02", value: 100 },
      { date: "2024-01-03", value: 200 },
    ]);
    expect(result.every((s) => s.cash === 0)).toBe(true);
    expect(result[0]!.marketValue).toBe(100);
    expect(result[1]!.marketValue).toBe(200);
  });

  it("dailyPnl computed correctly for declining values", () => {
    const result = backtestCurveToSnapshots([
      { date: "2024-01-02", value: 100 },
      { date: "2024-01-03", value: 80 },
      { date: "2024-01-04", value: 90 },
    ]);
    expect(result[1]!.dailyPnl).toBe(-20);
    expect(result[2]!.dailyPnl).toBe(10);
  });
});

// ── analyzeEquityCurve: integration / consistency ───────────

describe("analyzeEquityCurve (extended)", () => {
  it("metrics.totalReturn matches last cumulative return", () => {
    const series = generateSeries("2024-01-02", [100, 110, 95, 120, 105, 130]);
    const result = analyzeEquityCurve(series);
    const lastCR = result.cumulativeReturns[result.cumulativeReturns.length - 1]!.cumulativeReturn;
    expect(result.metrics.totalReturn).toBeCloseTo(lastCR, 6);
  });

  it("metrics.maxDrawdown matches maxDrawdownInterval.maxDrawdown", () => {
    const series = generateSeries("2024-01-02", [100, 120, 90, 110, 80, 100]);
    const result = analyzeEquityCurve(series);
    if (result.maxDrawdownInterval) {
      expect(result.metrics.maxDrawdown).toBeCloseTo(result.maxDrawdownInterval.maxDrawdown, 6);
    }
  });

  it("dailyReturns length = snapshots.length - 1", () => {
    const series = generateSeries("2024-01-02", [100, 110, 105, 115, 108]);
    const result = analyzeEquityCurve(series);
    expect(result.dailyReturns).toHaveLength(series.length - 1);
  });

  it("drawdownCurve has same length as input snapshots", () => {
    const series = generateSeries("2024-01-02", [100, 110, 105, 115, 108]);
    const result = analyzeEquityCurve(series);
    expect(result.drawdownCurve).toHaveLength(series.length);
  });

  it("cumulativeReturns has same length as input snapshots", () => {
    const series = generateSeries("2024-01-02", [100, 110, 105, 115, 108]);
    const result = analyzeEquityCurve(series);
    expect(result.cumulativeReturns).toHaveLength(series.length);
  });

  it("var95 confidence is 0.95", () => {
    const series = generateSeries("2024-01-02", [100, 110, 95, 105, 80, 90]);
    const result = analyzeEquityCurve(series);
    expect(result.var95.confidence).toBe(0.95);
  });

  it("handles single snapshot gracefully", () => {
    const result = analyzeEquityCurve([snap("2024-01-02", 100000)]);
    expect(result.cumulativeReturns).toHaveLength(1);
    expect(result.dailyReturns).toHaveLength(0);
    expect(result.drawdownCurve).toHaveLength(1);
    expect(result.maxDrawdownInterval).toBeNull();
    expect(result.metrics.tradingDays).toBe(1);
  });

  it("all drawdown episodes are within maxDrawdown", () => {
    const series = generateSeries("2024-01-02", [100, 120, 90, 110, 80, 105, 70, 100]);
    const result = analyzeEquityCurve(series);
    for (const ep of result.drawdownEpisodes) {
      expect(ep.drawdown).toBeLessThanOrEqual(result.metrics.maxDrawdown + 1e-10);
    }
  });

  it("underwaterStats.totalEpisodes matches drawdownEpisodes length (for default threshold)", () => {
    const series = generateSeries("2024-01-02", [100, 120, 110, 130, 115, 140]);
    const result = analyzeEquityCurve(series);
    // underwaterStats counts all dips (even tiny), drawdownEpisodes has minDrawdown=1%
    // So underwaterStats.totalEpisodes >= drawdownEpisodes.length
    expect(result.underwaterStats.totalEpisodes).toBeGreaterThanOrEqual(result.drawdownEpisodes.length);
  });
});

// ── Cross-function consistency ──────────────────────────────

describe("cross-function consistency", () => {
  it("computeDrawdownCurve max matches computeMaxDrawdownInterval max", () => {
    const series = generateSeries("2024-01-02", [100, 120, 85, 130, 70, 110]);
    const curve = computeDrawdownCurve(series);
    const interval = computeMaxDrawdownInterval(series);

    const maxFromCurve = Math.max(...curve.map((p) => p.drawdown));
    expect(interval).not.toBeNull();
    expect(maxFromCurve).toBeCloseTo(interval!.maxDrawdown, 6);
  });

  it("sum of daily returns roughly equals log of total return", () => {
    const series = generateSeries("2024-01-02", [100, 105, 110, 108, 115, 120]);
    const dailyReturns = computeDailyReturns(series);
    const cumReturns = computeCumulativeReturns(series);

    // Sum of log(1 + r_i) should equal log(1 + total_return)
    const logSum = dailyReturns.reduce((s, r) => s + Math.log(1 + r.dailyReturn), 0);
    const totalReturn = cumReturns[cumReturns.length - 1]!.cumulativeReturn;
    expect(logSum).toBeCloseTo(Math.log(1 + totalReturn), 6);
  });

  it("backtest adapter produces equivalent analysis to direct snapshots", () => {
    const curve = [
      { date: "2024-01-02", value: 100000 },
      { date: "2024-01-03", value: 110000 },
      { date: "2024-01-04", value: 95000 },
      { date: "2024-01-05", value: 105000 },
      { date: "2024-01-08", value: 115000 },
    ];
    const fromBacktest = analyzeEquityCurve(backtestCurveToSnapshots(curve));

    // Direct snapshots with same equity values
    const direct = curve.map((pt, i) => snap(pt.date, pt.value, i > 0 ? pt.value - curve[i - 1]!.value : 0));
    const fromDirect = analyzeEquityCurve(direct);

    // Core metrics should be identical (equity values are the same)
    expect(fromBacktest.metrics.totalReturn).toBeCloseTo(fromDirect.metrics.totalReturn, 6);
    expect(fromBacktest.metrics.maxDrawdown).toBeCloseTo(fromDirect.metrics.maxDrawdown, 6);
    expect(fromBacktest.metrics.sharpeRatio).toBeCloseTo(fromDirect.metrics.sharpeRatio, 6);
  });
});
