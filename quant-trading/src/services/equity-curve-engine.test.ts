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

// ── Test data factories ──────────────────────────────────────

function snap(date: string, equity: number, dailyPnl = 0): EquitySnapshot {
  return { date, equity, cash: equity * 0.5, marketValue: equity * 0.5, dailyPnl, positionCount: 1 };
}

/** Generate a simple upward-trending equity series */
function trendingUp(): EquitySnapshot[] {
  return [
    snap("2024-01-02", 100000, 0),
    snap("2024-01-03", 101000, 1000),
    snap("2024-01-04", 102500, 1500),
    snap("2024-01-05", 101500, -1000),
    snap("2024-01-08", 103000, 1500),
    snap("2024-01-09", 104000, 1000),
    snap("2024-01-10", 103500, -500),
    snap("2024-01-11", 105000, 1500),
    snap("2024-01-12", 106000, 1000),
    snap("2024-01-15", 107000, 1000),
  ];
}

/** Generate a series with a significant drawdown and recovery */
function drawdownAndRecovery(): EquitySnapshot[] {
  return [
    snap("2024-01-02", 100000),
    snap("2024-01-03", 105000),
    snap("2024-01-04", 110000),  // peak
    snap("2024-01-05", 100000),  // -9.09%
    snap("2024-01-08", 95000),   // trough: -13.64%
    snap("2024-01-09", 100000),
    snap("2024-01-10", 105000),
    snap("2024-01-11", 110000),  // recovered to peak
    snap("2024-01-12", 115000),  // new peak
  ];
}

/** Generate multi-month data for monthly return testing */
function multiMonth(): EquitySnapshot[] {
  return [
    snap("2024-01-02", 100000),
    snap("2024-01-15", 102000),
    snap("2024-01-31", 105000),
    snap("2024-02-01", 104000),
    snap("2024-02-15", 106000),
    snap("2024-02-28", 108000),
    snap("2024-03-01", 107000),
    snap("2024-03-15", 110000),
    snap("2024-03-28", 112000),
  ];
}

// ── Tests ────────────────────────────────────────────────────

describe("computeDailyReturns", () => {
  it("returns empty for 0 or 1 snapshots", () => {
    expect(computeDailyReturns([])).toEqual([]);
    expect(computeDailyReturns([snap("2024-01-02", 100000)])).toEqual([]);
  });

  it("computes correct daily returns", () => {
    const snapshots = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 102000),
      snap("2024-01-04", 99960),
    ];
    const returns = computeDailyReturns(snapshots);
    expect(returns).toHaveLength(2);
    expect(returns[0]!.date).toBe("2024-01-03");
    expect(returns[0]!.dailyReturn).toBeCloseTo(0.02, 6);
    expect(returns[1]!.date).toBe("2024-01-04");
    expect(returns[1]!.dailyReturn).toBeCloseTo(-0.02, 2);
  });

  it("handles zero equity gracefully", () => {
    const returns = computeDailyReturns([snap("2024-01-02", 0), snap("2024-01-03", 100)]);
    expect(returns[0]!.dailyReturn).toBe(0);
  });
});

describe("computeCumulativeReturns", () => {
  it("returns empty for empty input", () => {
    expect(computeCumulativeReturns([])).toEqual([]);
  });

  it("first point is always 0", () => {
    const result = computeCumulativeReturns(trendingUp());
    expect(result[0]!.cumulativeReturn).toBe(0);
  });

  it("computes correct cumulative returns", () => {
    const snapshots = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 120000),
    ];
    const result = computeCumulativeReturns(snapshots);
    expect(result).toHaveLength(3);
    expect(result[0]!.cumulativeReturn).toBe(0);
    expect(result[1]!.cumulativeReturn).toBeCloseTo(0.10, 6);
    expect(result[2]!.cumulativeReturn).toBeCloseTo(0.20, 6);
  });

  it("handles start equity of zero", () => {
    expect(computeCumulativeReturns([snap("2024-01-02", 0)])).toEqual([]);
  });
});

describe("computeDrawdownCurve", () => {
  it("returns empty for empty input", () => {
    expect(computeDrawdownCurve([])).toEqual([]);
  });

  it("drawdown is 0 at all-time highs", () => {
    const curve = computeDrawdownCurve([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 120000),
    ]);
    expect(curve.every((p) => p.drawdown === 0)).toBe(true);
  });

  it("computes correct drawdown from peak", () => {
    const curve = computeDrawdownCurve(drawdownAndRecovery());
    // At index 4 (2024-01-08, equity=95000), peak was 110000
    const point = curve.find((p) => p.date === "2024-01-08")!;
    expect(point.drawdown).toBeCloseTo(15000 / 110000, 6);
    expect(point.peakEquity).toBe(110000);
    expect(point.peakDate).toBe("2024-01-04");
  });

  it("drawdown returns to 0 after recovery to new high", () => {
    const curve = computeDrawdownCurve(drawdownAndRecovery());
    const recovered = curve.find((p) => p.date === "2024-01-12")!;
    expect(recovered.drawdown).toBe(0);
  });
});

describe("computeMaxDrawdownInterval", () => {
  it("returns null for fewer than 2 snapshots", () => {
    expect(computeMaxDrawdownInterval([])).toBeNull();
    expect(computeMaxDrawdownInterval([snap("2024-01-02", 100000)])).toBeNull();
  });

  it("returns null for monotonically increasing equity", () => {
    const result = computeMaxDrawdownInterval([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 120000),
    ]);
    expect(result).toBeNull();
  });

  it("identifies correct max drawdown interval with recovery", () => {
    const result = computeMaxDrawdownInterval(drawdownAndRecovery())!;
    expect(result).not.toBeNull();
    expect(result.peakDate).toBe("2024-01-04");
    expect(result.peakEquity).toBe(110000);
    expect(result.troughDate).toBe("2024-01-08");
    expect(result.troughEquity).toBe(95000);
    expect(result.maxDrawdown).toBeCloseTo(15000 / 110000, 6);
    expect(result.maxDrawdownAbs).toBe(15000);
    expect(result.recoveryDate).toBe("2024-01-11");
    expect(result.peakToTroughDays).toBe(4); // Jan 4 → Jan 8
    expect(result.peakToRecoveryDays).toBe(7); // Jan 4 → Jan 11
  });

  it("marks recovery as null when equity never recovers", () => {
    const result = computeMaxDrawdownInterval([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 90000),
      snap("2024-01-05", 95000),
    ])!;
    expect(result.recoveryDate).toBeNull();
    expect(result.peakToRecoveryDays).toBeNull();
  });
});

describe("computeRollingVolatility", () => {
  it("returns empty when fewer data points than window", () => {
    const returns = computeDailyReturns(trendingUp());
    expect(computeRollingVolatility(returns, 100)).toEqual([]);
  });

  it("computes rolling volatility with correct length", () => {
    const returns = computeDailyReturns(trendingUp());
    const rolling = computeRollingVolatility(returns, 3);
    expect(rolling).toHaveLength(returns.length - 2); // n - window + 1
    // All volatility values should be non-negative
    expect(rolling.every((p) => p.volatility >= 0)).toBe(true);
  });

  it("volatility is 0 for constant returns", () => {
    const flat: EquitySnapshot[] = [];
    for (let i = 0; i < 10; i++) {
      flat.push(snap(`2024-01-${String(i + 2).padStart(2, "0")}`, 100000));
    }
    const returns = computeDailyReturns(flat);
    const rolling = computeRollingVolatility(returns, 3);
    expect(rolling.every((p) => p.volatility === 0)).toBe(true);
  });
});

describe("computeMonthlyReturns", () => {
  it("returns empty for empty input", () => {
    expect(computeMonthlyReturns([])).toEqual([]);
  });

  it("computes correct monthly returns", () => {
    const result = computeMonthlyReturns(multiMonth());
    expect(result).toHaveLength(3);
    // Jan: 100000 → 105000 = +5%
    expect(result[0]!.period).toBe("2024-01");
    expect(result[0]!.return).toBeCloseTo(0.05, 4);
    // Feb: 104000 → 108000 = +3.85%
    expect(result[1]!.period).toBe("2024-02");
    expect(result[1]!.return).toBeCloseTo(4000 / 104000, 4);
    // Mar: 107000 → 112000 = +4.67%
    expect(result[2]!.period).toBe("2024-03");
    expect(result[2]!.return).toBeCloseTo(5000 / 107000, 4);
  });
});

describe("computeWeeklyReturns", () => {
  it("returns empty for empty input", () => {
    expect(computeWeeklyReturns([])).toEqual([]);
  });

  it("aggregates by ISO week", () => {
    const snapshots = [
      snap("2024-01-01", 100000), // Mon W01
      snap("2024-01-03", 101000), // Wed W01
      snap("2024-01-08", 102000), // Mon W02
      snap("2024-01-10", 103000), // Wed W02
    ];
    const result = computeWeeklyReturns(snapshots);
    expect(result.length).toBeGreaterThanOrEqual(2);
  });
});

describe("computeMetrics", () => {
  it("returns zeros for empty input", () => {
    const m = computeMetrics([]);
    expect(m.totalReturn).toBe(0);
    expect(m.tradingDays).toBe(0);
  });

  it("returns zeros for single snapshot", () => {
    const m = computeMetrics([snap("2024-01-02", 100000)]);
    expect(m.totalReturn).toBe(0);
    expect(m.tradingDays).toBe(1);
  });

  it("computes correct total return", () => {
    const m = computeMetrics(trendingUp());
    // 100000 → 107000 = +7%
    expect(m.totalReturn).toBeCloseTo(0.07, 4);
  });

  it("computes positive Sharpe for upward trend", () => {
    const m = computeMetrics(trendingUp());
    expect(m.sharpeRatio).toBeGreaterThan(0);
  });

  it("computes correct max drawdown", () => {
    const m = computeMetrics(drawdownAndRecovery());
    // Peak 110000, trough 95000 → 13.64% drawdown
    expect(m.maxDrawdown).toBeCloseTo(15000 / 110000, 4);
    expect(m.maxDrawdownAbs).toBe(15000);
  });

  it("computes consecutive win/loss streaks", () => {
    const snapshots = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 101000), // win
      snap("2024-01-04", 102000), // win
      snap("2024-01-05", 103000), // win
      snap("2024-01-08", 102000), // loss
      snap("2024-01-09", 101000), // loss
    ];
    const m = computeMetrics(snapshots);
    expect(m.maxConsecutiveWins).toBe(3);
    expect(m.maxConsecutiveLosses).toBe(2);
  });

  it("computes profit factor", () => {
    const snapshots = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 103000), // +3%
      snap("2024-01-04", 101000), // ≈-1.94%
      snap("2024-01-05", 104000), // ≈+2.97%
    ];
    const m = computeMetrics(snapshots);
    expect(m.profitFactor).toBeGreaterThan(1); // more gain than loss
  });

  it("Sortino > Sharpe when there are few down days", () => {
    const m = computeMetrics(trendingUp());
    // With mostly positive returns, Sortino should be >= Sharpe
    expect(m.sortinoRatio).toBeGreaterThanOrEqual(m.sharpeRatio);
  });

  it("Calmar ratio is positive for profitable series with drawdown", () => {
    const m = computeMetrics(drawdownAndRecovery());
    expect(m.calmarRatio).toBeGreaterThan(0);
  });
});

describe("analyzeEquityCurve", () => {
  it("returns all analysis components", () => {
    const result = analyzeEquityCurve(trendingUp());
    expect(result.cumulativeReturns.length).toBeGreaterThan(0);
    expect(result.dailyReturns.length).toBeGreaterThan(0);
    expect(result.drawdownCurve.length).toBeGreaterThan(0);
    expect(result.monthlyReturns.length).toBeGreaterThan(0);
    expect(result.weeklyReturns.length).toBeGreaterThan(0);
    expect(result.metrics.tradingDays).toBe(10);
  });

  it("handles empty snapshots gracefully", () => {
    const result = analyzeEquityCurve([]);
    expect(result.cumulativeReturns).toEqual([]);
    expect(result.dailyReturns).toEqual([]);
    expect(result.drawdownCurve).toEqual([]);
    expect(result.maxDrawdownInterval).toBeNull();
    expect(result.drawdownEpisodes).toEqual([]);
    expect(result.monthlyReturns).toEqual([]);
    expect(result.weeklyReturns).toEqual([]);
    expect(result.metrics.totalReturn).toBe(0);
    expect(result.underwaterStats.totalEpisodes).toBe(0);
    expect(result.var95.var).toBe(0);
    expect(result.returnDistribution.skewness).toBe(0);
  });

  it("includes new analysis fields", () => {
    const result = analyzeEquityCurve(drawdownAndRecovery());
    expect(result.drawdownEpisodes.length).toBeGreaterThan(0);
    expect(result.underwaterStats.totalEpisodes).toBeGreaterThan(0);
    expect(result.var95.confidence).toBe(0.95);
    expect(result.returnDistribution).toBeDefined();
  });
});

describe("backtestCurveToSnapshots", () => {
  it("converts backtest equityCurve to EquitySnapshot format", () => {
    const curve = [
      { date: "2024-01-02", value: 100000 },
      { date: "2024-01-03", value: 105000 },
      { date: "2024-01-04", value: 102000 },
    ];
    const snapshots = backtestCurveToSnapshots(curve);
    expect(snapshots).toHaveLength(3);
    expect(snapshots[0]!.equity).toBe(100000);
    expect(snapshots[0]!.dailyPnl).toBe(0); // first day, no previous
    expect(snapshots[1]!.equity).toBe(105000);
    expect(snapshots[1]!.dailyPnl).toBe(5000);
    expect(snapshots[2]!.dailyPnl).toBe(-3000);
  });

  it("converted snapshots work with analyzeEquityCurve", () => {
    const curve = [
      { date: "2024-01-02", value: 100000 },
      { date: "2024-01-03", value: 110000 },
      { date: "2024-01-04", value: 105000 },
      { date: "2024-01-05", value: 115000 },
    ];
    const snapshots = backtestCurveToSnapshots(curve);
    const analysis = analyzeEquityCurve(snapshots);
    expect(analysis.metrics.totalReturn).toBeCloseTo(0.15, 4);
    expect(analysis.metrics.maxDrawdown).toBeGreaterThan(0);
  });
});

// ── New feature tests ───────────────────────────────────────

describe("computeRollingSharpe", () => {
  it("returns empty when fewer data points than window", () => {
    const returns = computeDailyReturns(trendingUp());
    expect(computeRollingSharpe(returns, 100)).toEqual([]);
  });

  it("computes rolling Sharpe with correct length", () => {
    const returns = computeDailyReturns(trendingUp());
    const rolling = computeRollingSharpe(returns, 3);
    expect(rolling).toHaveLength(returns.length - 2);
  });

  it("Sharpe is 0 for constant returns", () => {
    const flat: EquitySnapshot[] = [];
    for (let i = 0; i < 10; i++) {
      flat.push(snap(`2024-01-${String(i + 2).padStart(2, "0")}`, 100000));
    }
    const returns = computeDailyReturns(flat);
    const rolling = computeRollingSharpe(returns, 3);
    expect(rolling.every((p) => p.sharpe === 0)).toBe(true);
  });
});

describe("computeUnderwaterStats", () => {
  it("returns zeros for empty/single snapshots", () => {
    expect(computeUnderwaterStats([]).totalEpisodes).toBe(0);
    expect(computeUnderwaterStats([snap("2024-01-02", 100000)]).totalEpisodes).toBe(0);
  });

  it("reports no episodes for monotonically increasing equity", () => {
    const stats = computeUnderwaterStats([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 120000),
    ]);
    expect(stats.totalEpisodes).toBe(0);
    expect(stats.pctTimeUnderwater).toBe(0);
    expect(stats.currentDurationDays).toBe(0);
  });

  it("detects drawdown episodes correctly", () => {
    const stats = computeUnderwaterStats(drawdownAndRecovery());
    expect(stats.totalEpisodes).toBe(1);
    expect(stats.maxDurationDays).toBeGreaterThan(0);
    expect(stats.pctTimeUnderwater).toBeGreaterThan(0);
    expect(stats.currentDurationDays).toBe(0); // recovered by end
  });

  it("reports ongoing drawdown at end of series", () => {
    const stats = computeUnderwaterStats([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 105000),
      snap("2024-01-05", 100000),
    ]);
    expect(stats.currentDurationDays).toBeGreaterThan(0);
  });
});

describe("computeVaR", () => {
  it("returns zeros for empty returns", () => {
    const result = computeVaR([]);
    expect(result.var).toBe(0);
    expect(result.cvar).toBe(0);
  });

  it("VaR and CVaR are non-negative", () => {
    const returns = computeDailyReturns(drawdownAndRecovery());
    const result = computeVaR(returns);
    expect(result.var).toBeGreaterThanOrEqual(0);
    expect(result.cvar).toBeGreaterThanOrEqual(0);
    expect(result.confidence).toBe(0.95);
  });

  it("CVaR >= VaR (expected shortfall is at least as large)", () => {
    const returns = computeDailyReturns(drawdownAndRecovery());
    const result = computeVaR(returns);
    expect(result.cvar).toBeGreaterThanOrEqual(result.var);
  });
});

describe("computeBenchmarkMetrics", () => {
  it("returns null for insufficient data", () => {
    expect(computeBenchmarkMetrics([], [])).toBeNull();
    expect(computeBenchmarkMetrics([snap("2024-01-02", 100000)], [])).toBeNull();
  });

  it("computes benchmark metrics for aligned data", () => {
    const strategy = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 102000),
      snap("2024-01-04", 104000),
      snap("2024-01-05", 103000),
      snap("2024-01-08", 106000),
    ];
    const benchmark = [
      snap("2024-01-02", 10000),
      snap("2024-01-03", 10100),
      snap("2024-01-04", 10200),
      snap("2024-01-05", 10150),
      snap("2024-01-08", 10300),
    ];
    const result = computeBenchmarkMetrics(strategy, benchmark)!;
    expect(result).not.toBeNull();
    expect(typeof result.alpha).toBe("number");
    expect(typeof result.beta).toBe("number");
    expect(typeof result.trackingError).toBe("number");
    expect(typeof result.informationRatio).toBe("number");
    expect(typeof result.correlation).toBe("number");
    // Strategy outperforms benchmark
    expect(result.excessReturn).toBeGreaterThan(0);
  });

  it("beta is ~1 for identical series", () => {
    const data = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 102000),
      snap("2024-01-04", 101000),
      snap("2024-01-05", 103000),
    ];
    const result = computeBenchmarkMetrics(data, data)!;
    expect(result.beta).toBeCloseTo(1, 2);
    expect(result.correlation).toBeCloseTo(1, 2);
    expect(result.trackingError).toBeCloseTo(0, 4);
  });
});

describe("computeReturnDistribution", () => {
  it("returns zeros for fewer than 3 data points", () => {
    const result = computeReturnDistribution([]);
    expect(result.skewness).toBe(0);
    expect(result.kurtosis).toBe(0);
  });

  it("computes percentiles correctly", () => {
    const returns = computeDailyReturns(trendingUp());
    const result = computeReturnDistribution(returns);
    // Percentiles should be ordered
    expect(result.percentile5).toBeLessThanOrEqual(result.percentile25);
    expect(result.percentile25).toBeLessThanOrEqual(result.medianReturn);
    expect(result.medianReturn).toBeLessThanOrEqual(result.percentile75);
    expect(result.percentile75).toBeLessThanOrEqual(result.percentile95);
  });

  it("skewness is negative for left-skewed returns", () => {
    // Create a series with a big drop followed by small gains
    const snapshots = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 101000),
      snap("2024-01-04", 102000),
      snap("2024-01-05", 103000),
      snap("2024-01-08", 104000),
      snap("2024-01-09", 90000),  // big drop
      snap("2024-01-10", 91000),
      snap("2024-01-11", 92000),
    ];
    const returns = computeDailyReturns(snapshots);
    const result = computeReturnDistribution(returns);
    expect(result.skewness).toBeLessThan(0);
  });
});

describe("computeAllDrawdownEpisodes", () => {
  it("returns empty for fewer than 2 snapshots", () => {
    expect(computeAllDrawdownEpisodes([])).toEqual([]);
    expect(computeAllDrawdownEpisodes([snap("2024-01-02", 100000)])).toEqual([]);
  });

  it("returns empty for monotonically increasing equity", () => {
    const result = computeAllDrawdownEpisodes([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 120000),
    ]);
    expect(result).toEqual([]);
  });

  it("detects a single recovered drawdown episode", () => {
    const result = computeAllDrawdownEpisodes(drawdownAndRecovery());
    expect(result.length).toBe(1);
    expect(result[0]!.peakDate).toBe("2024-01-04");
    expect(result[0]!.troughDate).toBe("2024-01-08");
    expect(result[0]!.recoveryDate).toBe("2024-01-11");
    expect(result[0]!.drawdown).toBeCloseTo(15000 / 110000, 4);
  });

  it("detects unrecovered drawdown", () => {
    const result = computeAllDrawdownEpisodes([
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 95000),
      snap("2024-01-05", 98000),
    ]);
    expect(result.length).toBe(1);
    expect(result[0]!.recoveryDate).toBeNull();
    expect(result[0]!.peakToRecoveryDays).toBeNull();
  });

  it("detects multiple drawdown episodes", () => {
    const snapshots = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000), // peak 1
      snap("2024-01-04", 95000),  // trough 1
      snap("2024-01-05", 112000), // recovery 1 + new peak 2
      snap("2024-01-08", 100000), // trough 2
      snap("2024-01-09", 115000), // recovery 2
    ];
    const result = computeAllDrawdownEpisodes(snapshots);
    expect(result.length).toBe(2);
  });

  it("respects minDrawdown threshold", () => {
    const snapshots = [
      snap("2024-01-02", 100000),
      snap("2024-01-03", 110000),
      snap("2024-01-04", 109000), // tiny dip: 0.9% < 1%
      snap("2024-01-05", 115000), // recovery
    ];
    // With default 1% threshold, this tiny dip should be filtered
    const result = computeAllDrawdownEpisodes(snapshots, 0.01);
    expect(result.length).toBe(0);
    // With a lower threshold, it should be captured
    const result2 = computeAllDrawdownEpisodes(snapshots, 0.005);
    expect(result2.length).toBe(1);
  });
});
