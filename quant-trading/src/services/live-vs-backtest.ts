/**
 * LiveVsBacktestService — compares live trading performance against backtest projections.
 *
 * Aligns live equity snapshots with backtest equity curves on overlapping dates,
 * computes divergence metrics, and identifies significant deviations.
 *
 * Features:
 *   - Date-aligned comparison between live and backtest equity series
 *   - Handles missing dates and time misalignment gracefully
 *   - Divergence metrics: return gap, drawdown gap, correlation
 *   - Drift detection with configurable thresholds
 *   - Per-day deviation breakdown
 */

import type { EquitySnapshot } from "./equity-history.js";
import type { BacktestResult } from "../types.js";

// ── Types ────────────────────────────────────────────────────

export interface LiveVsBacktestRequest {
  /** Live equity snapshots */
  liveSnapshots: EquitySnapshot[];
  /** Backtest result to compare against */
  backtestResult: BacktestResult;
  /** Drift detection threshold (percentage points, default: 5) */
  driftThreshold?: number;
}

/** Per-date aligned data point */
export interface AlignedDataPoint {
  date: string;
  liveEquity: number;
  backtestValue: number;
  /** Normalized live return from start (1.0 = no change) */
  liveNormalized: number;
  /** Normalized backtest return from start (1.0 = no change) */
  backtestNormalized: number;
  /** Difference in normalized returns (live - backtest) */
  deviation: number;
}

export interface DriftAlert {
  date: string;
  /** Deviation in percentage points (live return% - backtest return%) */
  deviationPct: number;
  /** Direction of drift */
  direction: "outperform" | "underperform";
}

export interface LiveVsBacktestResult {
  /** Aligned data points for charting */
  alignedSeries: AlignedDataPoint[];
  /** Dates present in live but not in backtest */
  liveMissingDates: string[];
  /** Dates present in backtest but not in live */
  backtestMissingDates: string[];
  /** Summary statistics */
  stats: {
    /** Number of overlapping dates */
    overlapDays: number;
    /** Live total return percentage */
    liveReturnPct: number;
    /** Backtest total return percentage */
    backtestReturnPct: number;
    /** Return gap: live - backtest (percentage points) */
    returnGapPct: number;
    /** Live max drawdown percentage */
    liveMaxDrawdownPct: number;
    /** Backtest max drawdown percentage */
    backtestMaxDrawdownPct: number;
    /** Drawdown gap: live - backtest (percentage points) */
    drawdownGapPct: number;
    /** Pearson correlation between daily returns (-1 to 1) */
    correlation: number;
    /** Mean absolute deviation in normalized returns */
    meanAbsDeviation: number;
    /** Max absolute deviation in normalized returns */
    maxAbsDeviation: number;
    /** Tracking error (annualized std dev of return differences) */
    trackingError: number;
  };
  /** Dates where drift exceeds threshold */
  driftAlerts: DriftAlert[];
}

// ── Service ──────────────────────────────────────────────────

export class LiveVsBacktestService {
  /**
   * Compare live equity snapshots against a backtest result.
   * Aligns on common dates, computes divergence metrics and drift alerts.
   */
  compare(req: LiveVsBacktestRequest): LiveVsBacktestResult {
    const { liveSnapshots, backtestResult, driftThreshold = 5 } = req;

    // Build date-indexed maps
    const liveByDate = new Map<string, EquitySnapshot>();
    for (const snap of liveSnapshots) {
      liveByDate.set(snap.date, snap);
    }

    const backtestByDate = new Map<string, { date: string; value: number }>();
    for (const pt of backtestResult.equityCurve) {
      backtestByDate.set(pt.date, pt);
    }

    // Find all unique dates sorted
    const allLiveDates = [...liveByDate.keys()].sort();
    const allBacktestDates = [...backtestByDate.keys()].sort();

    // Find common dates
    const commonDates = allLiveDates.filter((d) => backtestByDate.has(d));

    // Find missing dates
    const liveMissingDates = allLiveDates.filter((d) => !backtestByDate.has(d));
    const backtestMissingDates = allBacktestDates.filter((d) => !liveByDate.has(d));

    // Handle insufficient overlap
    if (commonDates.length < 2) {
      return {
        alignedSeries: commonDates.length === 1
          ? [this.buildSinglePoint(commonDates[0]!, liveByDate, backtestByDate)]
          : [],
        liveMissingDates,
        backtestMissingDates,
        stats: {
          overlapDays: commonDates.length,
          liveReturnPct: 0,
          backtestReturnPct: 0,
          returnGapPct: 0,
          liveMaxDrawdownPct: 0,
          backtestMaxDrawdownPct: 0,
          drawdownGapPct: 0,
          correlation: 0,
          meanAbsDeviation: 0,
          maxAbsDeviation: 0,
          trackingError: 0,
        },
        driftAlerts: [],
      };
    }

    // Build aligned series
    const firstLiveEquity = liveByDate.get(commonDates[0]!)!.equity;
    const firstBacktestValue = backtestByDate.get(commonDates[0]!)!.value;

    const alignedSeries: AlignedDataPoint[] = commonDates.map((date) => {
      const liveEquity = liveByDate.get(date)!.equity;
      const backtestValue = backtestByDate.get(date)!.value;
      const liveNormalized = firstLiveEquity > 0 ? liveEquity / firstLiveEquity : 1;
      const backtestNormalized = firstBacktestValue > 0 ? backtestValue / firstBacktestValue : 1;
      return {
        date,
        liveEquity,
        backtestValue,
        liveNormalized,
        backtestNormalized,
        deviation: liveNormalized - backtestNormalized,
      };
    });

    // Compute stats
    const lastAligned = alignedSeries[alignedSeries.length - 1]!;
    const liveReturnPct = (lastAligned.liveNormalized - 1) * 100;
    const backtestReturnPct = (lastAligned.backtestNormalized - 1) * 100;

    // Max drawdowns
    const liveMaxDrawdownPct = this.computeMaxDrawdownPct(
      commonDates.map((d) => liveByDate.get(d)!.equity),
    );
    const backtestMaxDrawdownPct = this.computeMaxDrawdownPct(
      commonDates.map((d) => backtestByDate.get(d)!.value),
    );

    // Daily returns for correlation
    const liveReturns: number[] = [];
    const backtestReturns: number[] = [];
    for (let i = 1; i < commonDates.length; i++) {
      const prevLive = liveByDate.get(commonDates[i - 1]!)!.equity;
      const currLive = liveByDate.get(commonDates[i]!)!.equity;
      const prevBt = backtestByDate.get(commonDates[i - 1]!)!.value;
      const currBt = backtestByDate.get(commonDates[i]!)!.value;
      liveReturns.push(prevLive > 0 ? (currLive - prevLive) / prevLive : 0);
      backtestReturns.push(prevBt > 0 ? (currBt - prevBt) / prevBt : 0);
    }

    const correlation = this.pearsonCorrelation(liveReturns, backtestReturns);

    // Deviation stats
    const deviations = alignedSeries.map((pt) => Math.abs(pt.deviation));
    const meanAbsDeviation = deviations.reduce((a, b) => a + b, 0) / deviations.length;
    const maxAbsDeviation = Math.max(...deviations);

    // Tracking error
    const returnDiffs = liveReturns.map((lr, i) => lr - backtestReturns[i]!);
    const trackingError = this.annualizedStdDev(returnDiffs);

    // Drift alerts
    const driftAlerts: DriftAlert[] = [];
    for (const pt of alignedSeries) {
      const deviationPct = pt.deviation * 100;
      if (Math.abs(deviationPct) >= driftThreshold) {
        driftAlerts.push({
          date: pt.date,
          deviationPct,
          direction: deviationPct > 0 ? "outperform" : "underperform",
        });
      }
    }

    return {
      alignedSeries,
      liveMissingDates,
      backtestMissingDates,
      stats: {
        overlapDays: commonDates.length,
        liveReturnPct,
        backtestReturnPct,
        returnGapPct: liveReturnPct - backtestReturnPct,
        liveMaxDrawdownPct,
        backtestMaxDrawdownPct,
        drawdownGapPct: liveMaxDrawdownPct - backtestMaxDrawdownPct,
        correlation,
        meanAbsDeviation,
        maxAbsDeviation,
        trackingError,
      },
      driftAlerts,
    };
  }

  // ── Private helpers ────────────────────────────────────────

  private buildSinglePoint(
    date: string,
    liveByDate: Map<string, EquitySnapshot>,
    backtestByDate: Map<string, { date: string; value: number }>,
  ): AlignedDataPoint {
    return {
      date,
      liveEquity: liveByDate.get(date)!.equity,
      backtestValue: backtestByDate.get(date)!.value,
      liveNormalized: 1,
      backtestNormalized: 1,
      deviation: 0,
    };
  }

  private computeMaxDrawdownPct(values: number[]): number {
    let peak = -Infinity;
    let maxDd = 0;
    for (const v of values) {
      if (v > peak) peak = v;
      const dd = peak > 0 ? ((peak - v) / peak) * 100 : 0;
      if (dd > maxDd) maxDd = dd;
    }
    return maxDd;
  }

  private pearsonCorrelation(xs: number[], ys: number[]): number {
    if (xs.length < 2) return 0;
    const n = xs.length;
    const meanX = xs.reduce((a, b) => a + b, 0) / n;
    const meanY = ys.reduce((a, b) => a + b, 0) / n;
    let sumXY = 0;
    let sumX2 = 0;
    let sumY2 = 0;
    for (let i = 0; i < n; i++) {
      const dx = xs[i]! - meanX;
      const dy = ys[i]! - meanY;
      sumXY += dx * dy;
      sumX2 += dx * dx;
      sumY2 += dy * dy;
    }
    const denom = Math.sqrt(sumX2 * sumY2);
    return denom > 0 ? sumXY / denom : 0;
  }

  private annualizedStdDev(dailyReturns: number[]): number {
    if (dailyReturns.length < 2) return 0;
    const n = dailyReturns.length;
    const mean = dailyReturns.reduce((a, b) => a + b, 0) / n;
    const variance = dailyReturns.reduce((sum, r) => sum + (r - mean) ** 2, 0) / (n - 1);
    return Math.sqrt(variance) * Math.sqrt(252);
  }
}
