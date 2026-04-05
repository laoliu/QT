/**
 * LiveBacktestComparison — compare live (实盘) trading results against
 * backtest results for the same strategy/symbols/period.
 *
 * FR-8: 实盘 vs 回测对比
 *
 * Use cases:
 *   1. Run a backtest for the same period as the live equity history,
 *      then compare key metrics side by side.
 *   2. Detect over-fitting: backtest looks great but live under-performs.
 *   3. Analyze slippage & execution gap between theoretical and actual fills.
 *   4. Compare equity curves point-by-point to find divergence windows.
 *
 * Data sources:
 *   - Live: EquityHistory snapshots + TradeJournal entries
 *   - Backtest: BacktestResult (run on-demand or loaded from BacktestStore)
 */

import type { BacktestResult } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Types ────────────────────────────────────────────────────

export interface LivePerformance {
  /** Date range covered */
  startDate: string;
  endDate: string;
  /** Starting equity */
  startEquity: number;
  /** Ending equity */
  endEquity: number;
  /** Total return (fraction, e.g. 0.12 = 12%) */
  totalReturn: number;
  /** Maximum drawdown (fraction, e.g. 0.08 = 8%) */
  maxDrawdown: number;
  /** Number of trading days */
  tradingDays: number;
  /** Win days / total active days */
  winRate: number;
  /** Average daily P&L */
  avgDailyPnl: number;
  /** Annualized return */
  annualizedReturn: number;
  /** Sharpe ratio (annualized, 3% risk-free) */
  sharpeRatio: number;
  /** Daily equity curve for chart overlay */
  equityCurve: Array<{ date: string; value: number }>;
}

export interface ComparisonMetricDiff {
  metric: string;
  label: string;
  live: number;
  backtest: number;
  /** Absolute diff: live - backtest */
  diff: number;
  /** Percentage diff: (live - backtest) / |backtest| * 100 */
  diffPct: number;
  /** Whether live outperformed backtest for this metric */
  liveWins: boolean;
}

export interface DivergenceWindow {
  /** Start date of divergence */
  startDate: string;
  /** End date of divergence */
  endDate: string;
  /** Number of days */
  days: number;
  /** Max gap during this window (live equity - backtest equity) */
  maxGap: number;
  /** Max gap as percentage of backtest equity */
  maxGapPct: number;
  /** Whether live was above backtest (positive gap) */
  liveAbove: boolean;
}

export interface LiveBacktestComparisonResult {
  /** Live trading performance summary */
  live: LivePerformance;
  /** Backtest performance summary (from the overlapping period) */
  backtest: {
    totalReturn: number;
    annualizedReturn: number;
    maxDrawdown: number;
    sharpeRatio: number;
    winRate: number;
    totalTrades: number;
    equityCurve: Array<{ date: string; value: number }>;
  };
  /** Side-by-side metric comparisons */
  metrics: ComparisonMetricDiff[];
  /** Overall score: how closely live tracks backtest (0-100, 100 = perfect) */
  trackingScore: number;
  /** Significant periods where live and backtest diverged */
  divergenceWindows: DivergenceWindow[];
  /** Execution gap analysis */
  executionGap: {
    /** Avg slippage: (backtest return - live return) / backtest return * 100 */
    avgSlippagePct: number;
    /** If positive, backtest over-estimated returns */
    returnGap: number;
    /** Drawdown gap: live max DD - backtest max DD */
    drawdownGap: number;
  };
  /** Warnings / insights */
  warnings: string[];
  /** Overlapping date range used for comparison */
  comparisonPeriod: { startDate: string; endDate: string };
}

// ── Helpers ──────────────────────────────────────────────────

/**
 * Compute live performance metrics from equity snapshots.
 */
export function computeLivePerformance(
  snapshots: EquitySnapshot[],
  startDate?: string,
  endDate?: string,
): LivePerformance | null {
  if (snapshots.length < 2) return null;

  const sorted = [...snapshots].sort((a, b) => a.date.localeCompare(b.date));

  // Filter by date range if specified
  const filtered = sorted.filter(
    (s) =>
      (!startDate || s.date >= startDate) && (!endDate || s.date <= endDate),
  );
  if (filtered.length < 2) return null;

  const first = filtered[0]!;
  const last = filtered[filtered.length - 1]!;

  const totalReturn =
    first.equity > 0 ? (last.equity - first.equity) / first.equity : 0;

  // Max drawdown
  let peak = -Infinity;
  let maxDrawdown = 0;
  for (const s of filtered) {
    if (s.equity > peak) peak = s.equity;
    const dd = peak > 0 ? (peak - s.equity) / peak : 0;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  // Win rate (days with positive P&L)
  let winDays = 0;
  let activeDays = 0;
  let totalPnl = 0;
  for (const s of filtered) {
    if (s.dailyPnl > 0) winDays++;
    if (s.dailyPnl !== 0) activeDays++;
    totalPnl += s.dailyPnl;
  }
  const winRate = activeDays > 0 ? winDays / activeDays : 0;

  // Annualized return
  const tradingDays = filtered.length;
  const years = tradingDays / 252;
  const annualizedReturn =
    years > 0 ? Math.pow(1 + totalReturn, 1 / years) - 1 : 0;

  // Sharpe ratio
  const dailyReturns: number[] = [];
  for (let i = 1; i < filtered.length; i++) {
    const prev = filtered[i - 1]!.equity;
    if (prev > 0) {
      dailyReturns.push(filtered[i]!.equity / prev - 1);
    }
  }
  const sharpeRatio = calcSharpe(dailyReturns);

  // Equity curve
  const equityCurve = filtered.map((s) => ({
    date: s.date,
    value: s.equity,
  }));

  return {
    startDate: first.date,
    endDate: last.date,
    startEquity: first.equity,
    endEquity: last.equity,
    totalReturn,
    maxDrawdown,
    tradingDays,
    winRate,
    avgDailyPnl: filtered.length > 0 ? totalPnl / filtered.length : 0,
    annualizedReturn,
    sharpeRatio,
    equityCurve,
  };
}

function calcSharpe(
  dailyReturns: number[],
  riskFreeAnnual = 0.03,
): number {
  if (dailyReturns.length < 2) return 0;
  const rfDaily = riskFreeAnnual / 252;
  const excess = dailyReturns.map((r) => r - rfDaily);
  const mean = excess.reduce((a, b) => a + b, 0) / excess.length;
  const variance =
    excess.reduce((a, x) => a + (x - mean) ** 2, 0) / excess.length;
  const std = Math.sqrt(variance);
  return std === 0 ? 0 : (mean / std) * Math.sqrt(252);
}

// ── Core comparison logic ───────────────────────────────────

/**
 * Compare live equity snapshots against a backtest result.
 *
 * The comparison is performed over the overlapping date range.
 * The backtest equity curve is scaled to match the live starting equity
 * so that curves are directly comparable.
 */
export function compareLiveVsBacktest(
  liveSnapshots: EquitySnapshot[],
  backtestResult: BacktestResult,
  options?: {
    startDate?: string;
    endDate?: string;
    /** Gap threshold (%) to flag a divergence window. Default: 5 */
    divergenceThresholdPct?: number;
  },
): LiveBacktestComparisonResult | null {
  const live = computeLivePerformance(
    liveSnapshots,
    options?.startDate,
    options?.endDate,
  );
  if (!live) return null;

  const btCurve = backtestResult.equityCurve ?? [];
  if (btCurve.length === 0) return null;

  // Find overlapping date range
  const btDates = new Set(btCurve.map((p) => p.date));
  const overlapDates = live.equityCurve
    .filter((p) => btDates.has(p.date))
    .map((p) => p.date);

  if (overlapDates.length < 2) return null;

  const overlapStart = overlapDates[0]!;
  const overlapEnd = overlapDates[overlapDates.length - 1]!;

  // Build date-indexed maps for the overlap
  const liveMap = new Map(live.equityCurve.map((p) => [p.date, p.value]));
  const btMap = new Map(btCurve.map((p) => [p.date, p.value]));

  // Scale backtest curve to match live starting equity
  const btStartValue = btMap.get(overlapStart) ?? btCurve[0]!.value;
  const liveStartValue = liveMap.get(overlapStart) ?? live.startEquity;
  const scale = btStartValue > 0 ? liveStartValue / btStartValue : 1;

  const scaledBtCurve: Array<{ date: string; value: number }> = [];
  for (const date of overlapDates) {
    const raw = btMap.get(date);
    if (raw != null) {
      scaledBtCurve.push({ date, value: raw * scale });
    }
  }

  // Compute backtest metrics over the overlap period
  const btOverlapValues = overlapDates
    .map((d) => btMap.get(d))
    .filter((v): v is number => v != null);
  const btFirst = btOverlapValues[0]!;
  const btLast = btOverlapValues[btOverlapValues.length - 1]!;
  const btReturn = btFirst > 0 ? (btLast - btFirst) / btFirst : 0;
  const btYears = overlapDates.length / 252;
  const btAnnualized =
    btYears > 0 ? Math.pow(1 + btReturn, 1 / btYears) - 1 : 0;

  // Backtest max drawdown over overlap
  let btPeak = -Infinity;
  let btMaxDD = 0;
  for (const v of btOverlapValues) {
    if (v > btPeak) btPeak = v;
    const dd = btPeak > 0 ? (btPeak - v) / btPeak : 0;
    if (dd > btMaxDD) btMaxDD = dd;
  }

  // Backtest daily returns for Sharpe
  const btDailyReturns: number[] = [];
  for (let i = 1; i < btOverlapValues.length; i++) {
    const prev = btOverlapValues[i - 1]!;
    if (prev > 0) btDailyReturns.push(btOverlapValues[i]! / prev - 1);
  }
  const btSharpe = calcSharpe(btDailyReturns);

  // Live metrics scoped to overlap
  const liveOverlap = computeLivePerformance(
    liveSnapshots,
    overlapStart,
    overlapEnd,
  );
  const liveFinal = liveOverlap ?? live;

  // Build metric diffs
  const metrics = buildMetricDiffs(liveFinal, {
    totalReturn: btReturn,
    annualizedReturn: btAnnualized,
    maxDrawdown: btMaxDD,
    sharpeRatio: btSharpe,
    winRate: backtestResult.winRate,
  });

  // Divergence windows
  const threshold = options?.divergenceThresholdPct ?? 5;
  const divergenceWindows = findDivergenceWindows(
    overlapDates,
    liveMap,
    btMap,
    scale,
    threshold,
  );

  // Tracking score: correlation-based
  const trackingScore = computeTrackingScore(
    overlapDates,
    liveMap,
    btMap,
    scale,
  );

  // Execution gap
  const returnGap = liveFinal.totalReturn - btReturn;
  const avgSlippagePct =
    btReturn !== 0 ? ((btReturn - liveFinal.totalReturn) / Math.abs(btReturn)) * 100 : 0;
  const drawdownGap = liveFinal.maxDrawdown - btMaxDD;

  // Warnings
  const warnings: string[] = [];
  if (returnGap < -0.05) {
    warnings.push(
      `实盘收益率比回测低 ${(Math.abs(returnGap) * 100).toFixed(1)}%，可能存在执行滑点或过拟合。`,
    );
  }
  if (drawdownGap > 0.03) {
    warnings.push(
      `实盘最大回撤比回测大 ${(drawdownGap * 100).toFixed(1)}%，需关注风控执行。`,
    );
  }
  if (btReturn > 0 && liveFinal.totalReturn < 0) {
    warnings.push("回测盈利但实盘亏损，策略可能严重过拟合。");
  }
  if (trackingScore < 50) {
    warnings.push("跟踪得分低于50，实盘与回测走势差异较大。");
  }
  if (overlapDates.length < 20) {
    warnings.push(
      `重叠交易日仅 ${overlapDates.length} 天，数据量偏少，结论可靠性有限。`,
    );
  }

  return {
    live: liveFinal,
    backtest: {
      totalReturn: btReturn,
      annualizedReturn: btAnnualized,
      maxDrawdown: btMaxDD,
      sharpeRatio: btSharpe,
      winRate: backtestResult.winRate,
      totalTrades: backtestResult.totalTrades,
      equityCurve: scaledBtCurve,
    },
    metrics,
    trackingScore,
    divergenceWindows,
    executionGap: {
      avgSlippagePct: Math.round(avgSlippagePct * 100) / 100,
      returnGap: Math.round(returnGap * 10000) / 10000,
      drawdownGap: Math.round(drawdownGap * 10000) / 10000,
    },
    warnings,
    comparisonPeriod: { startDate: overlapStart, endDate: overlapEnd },
  };
}

// ── Internal helpers ────────────────────────────────────────

function buildMetricDiffs(
  live: LivePerformance,
  bt: {
    totalReturn: number;
    annualizedReturn: number;
    maxDrawdown: number;
    sharpeRatio: number;
    winRate: number;
  },
): ComparisonMetricDiff[] {
  const defs: Array<{
    metric: string;
    label: string;
    liveVal: number;
    btVal: number;
    higherBetter: boolean;
  }> = [
    {
      metric: "totalReturn",
      label: "总收益率",
      liveVal: live.totalReturn,
      btVal: bt.totalReturn,
      higherBetter: true,
    },
    {
      metric: "annualizedReturn",
      label: "年化收益率",
      liveVal: live.annualizedReturn,
      btVal: bt.annualizedReturn,
      higherBetter: true,
    },
    {
      metric: "maxDrawdown",
      label: "最大回撤",
      liveVal: live.maxDrawdown,
      btVal: bt.maxDrawdown,
      higherBetter: false,
    },
    {
      metric: "sharpeRatio",
      label: "夏普比率",
      liveVal: live.sharpeRatio,
      btVal: bt.sharpeRatio,
      higherBetter: true,
    },
    {
      metric: "winRate",
      label: "胜率",
      liveVal: live.winRate,
      btVal: bt.winRate,
      higherBetter: true,
    },
  ];

  return defs.map(({ metric, label, liveVal, btVal, higherBetter }) => {
    const diff = liveVal - btVal;
    const diffPct = btVal !== 0 ? (diff / Math.abs(btVal)) * 100 : 0;
    const liveWins = higherBetter ? liveVal >= btVal : liveVal <= btVal;
    return {
      metric,
      label,
      live: Math.round(liveVal * 10000) / 10000,
      backtest: Math.round(btVal * 10000) / 10000,
      diff: Math.round(diff * 10000) / 10000,
      diffPct: Math.round(diffPct * 100) / 100,
      liveWins,
    };
  });
}

function findDivergenceWindows(
  dates: string[],
  liveMap: Map<string, number>,
  btMap: Map<string, number>,
  scale: number,
  thresholdPct: number,
): DivergenceWindow[] {
  const windows: DivergenceWindow[] = [];
  let windowStart: string | null = null;
  let maxGap = 0;
  let maxGapPct = 0;

  for (const date of dates) {
    const liveVal = liveMap.get(date);
    const btVal = btMap.get(date);
    if (liveVal == null || btVal == null) continue;

    const scaledBt = btVal * scale;
    const gap = liveVal - scaledBt;
    const gapPct = scaledBt > 0 ? (Math.abs(gap) / scaledBt) * 100 : 0;

    if (gapPct >= thresholdPct) {
      if (!windowStart) windowStart = date;
      if (Math.abs(gap) > Math.abs(maxGap)) {
        maxGap = gap;
        maxGapPct = gapPct;
      }
    } else if (windowStart) {
      // Close the window
      const startIdx = dates.indexOf(windowStart);
      const endIdx = dates.indexOf(date) - 1;
      if (endIdx > startIdx) {
        windows.push({
          startDate: windowStart,
          endDate: dates[endIdx]!,
          days: endIdx - startIdx + 1,
          maxGap: Math.round(maxGap * 100) / 100,
          maxGapPct: Math.round(maxGapPct * 100) / 100,
          liveAbove: maxGap > 0,
        });
      }
      windowStart = null;
      maxGap = 0;
      maxGapPct = 0;
    }
  }

  // Close trailing window
  if (windowStart) {
    const startIdx = dates.indexOf(windowStart);
    windows.push({
      startDate: windowStart,
      endDate: dates[dates.length - 1]!,
      days: dates.length - startIdx,
      maxGap: Math.round(maxGap * 100) / 100,
      maxGapPct: Math.round(maxGapPct * 100) / 100,
      liveAbove: maxGap > 0,
    });
  }

  return windows;
}

/**
 * Compute a tracking score (0–100) based on daily return correlation
 * between live and backtest.
 */
function computeTrackingScore(
  dates: string[],
  liveMap: Map<string, number>,
  btMap: Map<string, number>,
  scale: number,
): number {
  if (dates.length < 3) return 0;

  const liveReturns: number[] = [];
  const btReturns: number[] = [];

  for (let i = 1; i < dates.length; i++) {
    const livePrev = liveMap.get(dates[i - 1]!);
    const liveCurr = liveMap.get(dates[i]!);
    const btPrev = btMap.get(dates[i - 1]!);
    const btCurr = btMap.get(dates[i]!);

    if (
      livePrev != null &&
      liveCurr != null &&
      btPrev != null &&
      btCurr != null &&
      livePrev > 0 &&
      btPrev > 0
    ) {
      liveReturns.push(liveCurr / livePrev - 1);
      btReturns.push((btCurr * scale) / (btPrev * scale) - 1);
    }
  }

  if (liveReturns.length < 2) return 0;

  // Pearson correlation
  const n = liveReturns.length;
  const meanL = liveReturns.reduce((a, b) => a + b, 0) / n;
  const meanB = btReturns.reduce((a, b) => a + b, 0) / n;

  let covSum = 0;
  let varL = 0;
  let varB = 0;
  for (let i = 0; i < n; i++) {
    const dl = liveReturns[i]! - meanL;
    const db = btReturns[i]! - meanB;
    covSum += dl * db;
    varL += dl * dl;
    varB += db * db;
  }

  const denom = Math.sqrt(varL * varB);
  const corr = denom > 0 ? covSum / denom : 0;

  // Map correlation [-1, 1] to score [0, 100]
  // corr=1 → 100, corr=0 → 50, corr=-1 → 0
  return Math.round(Math.max(0, Math.min(100, (corr + 1) * 50)));
}
