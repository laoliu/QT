/**
 * EquityCurveEngine — core computation engine for equity curve analysis.
 *
 * Given a time-series of daily equity snapshots, computes:
 *  - Cumulative return series (%)
 *  - Daily return series
 *  - Drawdown sequence (% from peak, with peak/trough dates)
 *  - Max drawdown interval (start, trough, recovery dates)
 *  - Rolling volatility (annualized)
 *  - Sharpe / Sortino / Calmar ratios
 *  - Monthly and weekly return aggregation
 *  - Underwater duration statistics
 *
 * All functions are pure — no I/O, no side effects. Designed to work with
 * both live EquitySnapshot[] and backtest equityCurve arrays.
 */
import type { EquitySnapshot } from "./equity-history.js";

// ── Types ────────────────────────────────────────────────────

/** A point on the cumulative return curve */
export interface CumulativeReturnPoint {
  date: string;
  /** Cumulative return from the start (0 = no change, 0.05 = +5%) */
  cumulativeReturn: number;
}

/** A point on the daily return series */
export interface DailyReturnPoint {
  date: string;
  /** Daily return as decimal (0.01 = +1%) */
  dailyReturn: number;
}

/** A point on the drawdown curve */
export interface DrawdownPoint {
  date: string;
  /** Drawdown from peak as decimal (0.05 = -5% from peak) */
  drawdown: number;
  /** Equity at peak before this drawdown */
  peakEquity: number;
  /** Date of the peak */
  peakDate: string;
}

/** Max drawdown interval: from peak to trough, and optional recovery */
export interface MaxDrawdownInterval {
  /** Date when equity peaked before the worst drawdown */
  peakDate: string;
  /** Equity at peak */
  peakEquity: number;
  /** Date when equity hit the trough */
  troughDate: string;
  /** Equity at trough */
  troughEquity: number;
  /** Max drawdown as decimal (e.g. 0.15 = 15%) */
  maxDrawdown: number;
  /** Max drawdown in absolute value (CNY) */
  maxDrawdownAbs: number;
  /** Date when equity recovered to peak level (null if not yet recovered) */
  recoveryDate: string | null;
  /** Duration from peak to trough in calendar days */
  peakToTroughDays: number;
  /** Duration from peak to recovery in calendar days (null if not recovered) */
  peakToRecoveryDays: number | null;
}

/** Aggregated return for a period (week or month) */
export interface PeriodReturn {
  /** Period label: "YYYY-MM" for monthly, "YYYY-Www" for weekly */
  period: string;
  /** Return for this period as decimal */
  return: number;
  /** Start equity of the period */
  startEquity: number;
  /** End equity of the period */
  endEquity: number;
}

/** Full analysis result from the equity curve engine */
export interface EquityCurveAnalysis {
  /** Cumulative return time series */
  cumulativeReturns: CumulativeReturnPoint[];
  /** Daily return time series */
  dailyReturns: DailyReturnPoint[];
  /** Drawdown sequence (% from peak at each date) */
  drawdownCurve: DrawdownPoint[];
  /** Max drawdown interval details */
  maxDrawdownInterval: MaxDrawdownInterval | null;
  /** Monthly returns */
  monthlyReturns: PeriodReturn[];
  /** Weekly returns */
  weeklyReturns: PeriodReturn[];
  /** Summary risk/return metrics */
  metrics: EquityCurveMetrics;
}

/** Summary risk/return metrics computed from the equity curve */
export interface EquityCurveMetrics {
  /** Total return as decimal (0.20 = +20%) */
  totalReturn: number;
  /** Annualized return as decimal */
  annualizedReturn: number;
  /** Annualized volatility (std dev of daily returns * sqrt(252)) */
  annualizedVolatility: number;
  /** Sharpe ratio (daily returns annualized, risk-free rate = 0) */
  sharpeRatio: number;
  /** Sortino ratio (downside deviation only) */
  sortinoRatio: number;
  /** Calmar ratio (annualized return / max drawdown) */
  calmarRatio: number;
  /** Max drawdown as decimal */
  maxDrawdown: number;
  /** Max drawdown in absolute value */
  maxDrawdownAbs: number;
  /** Number of trading days */
  tradingDays: number;
  /** Win rate of daily returns (%) */
  dailyWinRate: number;
  /** Best single-day return as decimal */
  bestDayReturn: number;
  /** Worst single-day return as decimal */
  worstDayReturn: number;
  /** Average daily return as decimal */
  avgDailyReturn: number;
  /** Max consecutive win days */
  maxConsecutiveWins: number;
  /** Max consecutive loss days */
  maxConsecutiveLosses: number;
  /** Profit factor (sum of positive daily returns / |sum of negative daily returns|) */
  profitFactor: number;
}

/** Rolling volatility point */
export interface RollingVolatilityPoint {
  date: string;
  /** Annualized volatility computed over the trailing window */
  volatility: number;
}

// ── Helpers ──────────────────────────────────────────────────

/** Parse "YYYY-MM-DD" to Date (UTC) */
function parseDate(dateStr: string): Date {
  const [y, m, d] = dateStr.split("-").map(Number) as [number, number, number];
  return new Date(Date.UTC(y, m - 1, d));
}

/** Calendar days between two date strings */
function daysBetween(dateA: string, dateB: string): number {
  const msPerDay = 86_400_000;
  return Math.round((parseDate(dateB).getTime() - parseDate(dateA).getTime()) / msPerDay);
}

/** ISO week string "YYYY-Www" for a date string */
function isoWeek(dateStr: string): string {
  const d = parseDate(dateStr);
  // ISO week number calculation
  const dayOfYear = Math.floor(
    (d.getTime() - Date.UTC(d.getUTCFullYear(), 0, 1)) / 86_400_000,
  );
  const dayOfWeek = d.getUTCDay() || 7; // ISO: Monday=1, Sunday=7
  const weekNum = Math.ceil((dayOfYear + (8 - dayOfWeek)) / 7);
  const wk = Math.max(1, weekNum);
  return `${d.getUTCFullYear()}-W${String(wk).padStart(2, "0")}`;
}

// ── Core Computation Functions ───────────────────────────────

/**
 * Compute daily return series from equity snapshots.
 * The first snapshot has no return (excluded from output).
 */
export function computeDailyReturns(snapshots: EquitySnapshot[]): DailyReturnPoint[] {
  if (snapshots.length < 2) return [];
  const returns: DailyReturnPoint[] = [];
  for (let i = 1; i < snapshots.length; i++) {
    const prev = snapshots[i - 1]!;
    const curr = snapshots[i]!;
    const ret = prev.equity > 0 ? curr.equity / prev.equity - 1 : 0;
    returns.push({ date: curr.date, dailyReturn: ret });
  }
  return returns;
}

/**
 * Compute cumulative return series from equity snapshots.
 * First point is always 0 (the starting baseline).
 */
export function computeCumulativeReturns(snapshots: EquitySnapshot[]): CumulativeReturnPoint[] {
  if (snapshots.length === 0) return [];
  const startEquity = snapshots[0]!.equity;
  if (startEquity <= 0) return [];
  return snapshots.map((s) => ({
    date: s.date,
    cumulativeReturn: s.equity / startEquity - 1,
  }));
}

/**
 * Compute drawdown curve: at each date, the percentage decline from
 * the running peak equity.
 */
export function computeDrawdownCurve(snapshots: EquitySnapshot[]): DrawdownPoint[] {
  if (snapshots.length === 0) return [];
  const curve: DrawdownPoint[] = [];
  let peakEquity = -Infinity;
  let peakDate = snapshots[0]!.date;

  for (const s of snapshots) {
    if (s.equity > peakEquity) {
      peakEquity = s.equity;
      peakDate = s.date;
    }
    const dd = peakEquity > 0 ? (peakEquity - s.equity) / peakEquity : 0;
    curve.push({
      date: s.date,
      drawdown: dd,
      peakEquity,
      peakDate,
    });
  }
  return curve;
}

/**
 * Find the maximum drawdown interval: the period with the largest
 * peak-to-trough decline, including recovery date if applicable.
 */
export function computeMaxDrawdownInterval(snapshots: EquitySnapshot[]): MaxDrawdownInterval | null {
  if (snapshots.length < 2) return null;

  let peakEquity = snapshots[0]!.equity;
  let peakDate = snapshots[0]!.date;
  let maxDd = 0;
  let bestPeakDate = peakDate;
  let bestPeakEquity = peakEquity;
  let bestTroughDate = snapshots[0]!.date;
  let bestTroughEquity = snapshots[0]!.equity;

  for (const s of snapshots) {
    if (s.equity > peakEquity) {
      peakEquity = s.equity;
      peakDate = s.date;
    }
    const dd = peakEquity > 0 ? (peakEquity - s.equity) / peakEquity : 0;
    if (dd > maxDd) {
      maxDd = dd;
      bestPeakDate = peakDate;
      bestPeakEquity = peakEquity;
      bestTroughDate = s.date;
      bestTroughEquity = s.equity;
    }
  }

  if (maxDd === 0) return null;

  // Find recovery date: first date after trough where equity >= peak
  let recoveryDate: string | null = null;
  let pastTrough = false;
  for (const s of snapshots) {
    if (s.date === bestTroughDate) pastTrough = true;
    if (pastTrough && s.date > bestTroughDate && s.equity >= bestPeakEquity) {
      recoveryDate = s.date;
      break;
    }
  }

  return {
    peakDate: bestPeakDate,
    peakEquity: bestPeakEquity,
    troughDate: bestTroughDate,
    troughEquity: bestTroughEquity,
    maxDrawdown: maxDd,
    maxDrawdownAbs: bestPeakEquity - bestTroughEquity,
    recoveryDate,
    peakToTroughDays: daysBetween(bestPeakDate, bestTroughDate),
    peakToRecoveryDays: recoveryDate ? daysBetween(bestPeakDate, recoveryDate) : null,
  };
}

/**
 * Compute rolling annualized volatility over a trailing window.
 * @param dailyReturns - array of daily returns
 * @param windowSize - number of trading days in the window (default: 20 ≈ 1 month)
 */
export function computeRollingVolatility(
  dailyReturns: DailyReturnPoint[],
  windowSize = 20,
): RollingVolatilityPoint[] {
  if (dailyReturns.length < windowSize) return [];
  const result: RollingVolatilityPoint[] = [];
  for (let i = windowSize - 1; i < dailyReturns.length; i++) {
    const window = dailyReturns.slice(i - windowSize + 1, i + 1);
    const mean = window.reduce((sum, r) => sum + r.dailyReturn, 0) / windowSize;
    const variance =
      window.reduce((sum, r) => sum + (r.dailyReturn - mean) ** 2, 0) / windowSize;
    const annualizedVol = Math.sqrt(variance) * Math.sqrt(252);
    result.push({ date: dailyReturns[i]!.date, volatility: annualizedVol });
  }
  return result;
}

/**
 * Aggregate returns by month.
 */
export function computeMonthlyReturns(snapshots: EquitySnapshot[]): PeriodReturn[] {
  if (snapshots.length === 0) return [];
  const monthMap = new Map<string, { startEquity: number; endEquity: number }>();
  for (const s of snapshots) {
    const month = s.date.slice(0, 7); // "YYYY-MM"
    const entry = monthMap.get(month);
    if (!entry) {
      monthMap.set(month, { startEquity: s.equity, endEquity: s.equity });
    } else {
      entry.endEquity = s.equity;
    }
  }
  return [...monthMap.entries()].map(([period, { startEquity, endEquity }]) => ({
    period,
    return: startEquity > 0 ? (endEquity - startEquity) / startEquity : 0,
    startEquity,
    endEquity,
  }));
}

/**
 * Aggregate returns by ISO week.
 */
export function computeWeeklyReturns(snapshots: EquitySnapshot[]): PeriodReturn[] {
  if (snapshots.length === 0) return [];
  const weekMap = new Map<string, { startEquity: number; endEquity: number }>();
  for (const s of snapshots) {
    const week = isoWeek(s.date);
    const entry = weekMap.get(week);
    if (!entry) {
      weekMap.set(week, { startEquity: s.equity, endEquity: s.equity });
    } else {
      entry.endEquity = s.equity;
    }
  }
  return [...weekMap.entries()].map(([period, { startEquity, endEquity }]) => ({
    period,
    return: startEquity > 0 ? (endEquity - startEquity) / startEquity : 0,
    startEquity,
    endEquity,
  }));
}

/**
 * Compute summary risk/return metrics from daily equity snapshots.
 */
export function computeMetrics(snapshots: EquitySnapshot[]): EquityCurveMetrics {
  const empty: EquityCurveMetrics = {
    totalReturn: 0,
    annualizedReturn: 0,
    annualizedVolatility: 0,
    sharpeRatio: 0,
    sortinoRatio: 0,
    calmarRatio: 0,
    maxDrawdown: 0,
    maxDrawdownAbs: 0,
    tradingDays: 0,
    dailyWinRate: 0,
    bestDayReturn: 0,
    worstDayReturn: 0,
    avgDailyReturn: 0,
    maxConsecutiveWins: 0,
    maxConsecutiveLosses: 0,
    profitFactor: 0,
  };

  if (snapshots.length < 2) return { ...empty, tradingDays: snapshots.length };

  const startEquity = snapshots[0]!.equity;
  const endEquity = snapshots[snapshots.length - 1]!.equity;
  const totalReturn = startEquity > 0 ? endEquity / startEquity - 1 : 0;

  const dailyReturns = computeDailyReturns(snapshots);
  const n = dailyReturns.length;
  if (n === 0) return { ...empty, tradingDays: snapshots.length, totalReturn };

  // Annualized return
  const tradingDays = snapshots.length;
  const years = tradingDays / 252;
  const annualizedReturn =
    years > 0 && startEquity > 0
      ? (endEquity / startEquity) ** (1 / years) - 1
      : 0;

  // Volatility
  const returns = dailyReturns.map((r) => r.dailyReturn);
  const avgReturn = returns.reduce((a, b) => a + b, 0) / n;
  const variance = returns.reduce((sum, r) => sum + (r - avgReturn) ** 2, 0) / n;
  const dailyStdDev = Math.sqrt(variance);
  const annualizedVolatility = dailyStdDev * Math.sqrt(252);

  // Sharpe ratio (risk-free = 0)
  const sharpeRatio = dailyStdDev > 0 ? (avgReturn / dailyStdDev) * Math.sqrt(252) : 0;

  // Sortino ratio (downside deviation)
  const downsideSquares = returns.reduce(
    (sum, r) => sum + (r < 0 ? r ** 2 : 0),
    0,
  );
  const downsideDev = Math.sqrt(downsideSquares / n);
  const sortinoRatio = downsideDev > 0 ? (avgReturn / downsideDev) * Math.sqrt(252) : 0;

  // Max drawdown
  let peakEquity = -Infinity;
  let maxDd = 0;
  let maxDdAbs = 0;
  for (const s of snapshots) {
    if (s.equity > peakEquity) peakEquity = s.equity;
    const dd = peakEquity > 0 ? (peakEquity - s.equity) / peakEquity : 0;
    const ddAbs = peakEquity - s.equity;
    if (dd > maxDd) {
      maxDd = dd;
      maxDdAbs = ddAbs;
    }
  }

  // Calmar ratio
  const calmarRatio = maxDd > 0 ? annualizedReturn / maxDd : 0;

  // Win/loss stats
  let winDays = 0;
  let lossDays = 0;
  let bestDayReturn = -Infinity;
  let worstDayReturn = Infinity;
  let grossProfit = 0;
  let grossLoss = 0;
  let consWins = 0;
  let consLosses = 0;
  let maxConsWins = 0;
  let maxConsLosses = 0;

  for (const r of returns) {
    if (r > 0) {
      winDays++;
      grossProfit += r;
      consWins++;
      consLosses = 0;
    } else if (r < 0) {
      lossDays++;
      grossLoss += Math.abs(r);
      consLosses++;
      consWins = 0;
    } else {
      consWins = 0;
      consLosses = 0;
    }
    if (consWins > maxConsWins) maxConsWins = consWins;
    if (consLosses > maxConsLosses) maxConsLosses = consLosses;
    if (r > bestDayReturn) bestDayReturn = r;
    if (r < worstDayReturn) worstDayReturn = r;
  }

  const dailyWinRate = winDays + lossDays > 0 ? (winDays / (winDays + lossDays)) * 100 : 0;
  const profitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

  return {
    totalReturn,
    annualizedReturn,
    annualizedVolatility,
    sharpeRatio,
    sortinoRatio,
    calmarRatio,
    maxDrawdown: maxDd,
    maxDrawdownAbs: maxDdAbs,
    tradingDays,
    dailyWinRate,
    bestDayReturn: bestDayReturn === -Infinity ? 0 : bestDayReturn,
    worstDayReturn: worstDayReturn === Infinity ? 0 : worstDayReturn,
    avgDailyReturn: avgReturn,
    maxConsecutiveWins: maxConsWins,
    maxConsecutiveLosses: maxConsLosses,
    profitFactor,
  };
}

// ── Full Analysis ────────────────────────────────────────────

/**
 * Run the full equity curve analysis on a sorted array of equity snapshots.
 * Snapshots must be sorted by date ascending.
 */
export function analyzeEquityCurve(snapshots: EquitySnapshot[]): EquityCurveAnalysis {
  return {
    cumulativeReturns: computeCumulativeReturns(snapshots),
    dailyReturns: computeDailyReturns(snapshots),
    drawdownCurve: computeDrawdownCurve(snapshots),
    maxDrawdownInterval: computeMaxDrawdownInterval(snapshots),
    monthlyReturns: computeMonthlyReturns(snapshots),
    weeklyReturns: computeWeeklyReturns(snapshots),
    metrics: computeMetrics(snapshots),
  };
}

// ── Backtest equityCurve adapter ─────────────────────────────

/**
 * Convert a backtest-style equity curve `Array<{ date, value }>` to
 * EquitySnapshot[] so it can be fed to the analysis engine.
 */
export function backtestCurveToSnapshots(
  curve: Array<{ date: string; value: number }>,
): EquitySnapshot[] {
  return curve.map((pt, i) => {
    const prevValue = i > 0 ? curve[i - 1]!.value : pt.value;
    return {
      date: pt.date,
      equity: pt.value,
      cash: 0,
      marketValue: pt.value,
      dailyPnl: pt.value - prevValue,
      positionCount: 0,
    };
  });
}
