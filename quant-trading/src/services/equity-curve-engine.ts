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
  /** All significant drawdown episodes */
  drawdownEpisodes: DrawdownEpisode[];
  /** Monthly returns */
  monthlyReturns: PeriodReturn[];
  /** Weekly returns */
  weeklyReturns: PeriodReturn[];
  /** Summary risk/return metrics */
  metrics: EquityCurveMetrics;
  /** Underwater (time in drawdown) statistics */
  underwaterStats: UnderwaterStats;
  /** VaR and CVaR at 95% confidence */
  var95: VaRResult;
  /** Return distribution statistics */
  returnDistribution: ReturnDistribution;
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

/** Rolling Sharpe ratio point */
export interface RollingSharpePoint {
  date: string;
  /** Annualized Sharpe ratio computed over the trailing window */
  sharpe: number;
}

/** Underwater (time spent in drawdown) statistics */
export interface UnderwaterStats {
  /** Total number of drawdown episodes */
  totalEpisodes: number;
  /** Average drawdown duration in trading days */
  avgDurationDays: number;
  /** Maximum drawdown duration in trading days */
  maxDurationDays: number;
  /** Percentage of time spent underwater (0-100) */
  pctTimeUnderwater: number;
  /** Current drawdown duration (0 if at peak) */
  currentDurationDays: number;
}

/** Value at Risk / Conditional VaR result */
export interface VaRResult {
  /** Historical VaR at the given confidence level (positive number = loss) */
  var: number;
  /** Conditional VaR (Expected Shortfall) at the given confidence level */
  cvar: number;
  /** Confidence level used (e.g. 0.95) */
  confidence: number;
}

/** Benchmark-relative performance metrics */
export interface BenchmarkMetrics {
  /** CAPM Alpha (annualized) */
  alpha: number;
  /** CAPM Beta */
  beta: number;
  /** Annualized tracking error */
  trackingError: number;
  /** Information ratio */
  informationRatio: number;
  /** Pearson correlation */
  correlation: number;
  /** Benchmark total return */
  benchmarkTotalReturn: number;
  /** Benchmark annualized return */
  benchmarkAnnualizedReturn: number;
  /** Excess return over benchmark */
  excessReturn: number;
}

/** Return distribution statistics */
export interface ReturnDistribution {
  /** Skewness of daily returns (negative = left tail, positive = right tail) */
  skewness: number;
  /** Excess kurtosis of daily returns (>0 = fat tails) */
  kurtosis: number;
  /** Median daily return */
  medianReturn: number;
  /** 5th percentile daily return */
  percentile5: number;
  /** 25th percentile daily return */
  percentile25: number;
  /** 75th percentile daily return */
  percentile75: number;
  /** 95th percentile daily return */
  percentile95: number;
}

/** A single drawdown episode */
export interface DrawdownEpisode {
  /** Date when equity peaked before this drawdown */
  peakDate: string;
  /** Peak equity value */
  peakEquity: number;
  /** Date of the trough (deepest point) */
  troughDate: string;
  /** Trough equity value */
  troughEquity: number;
  /** Maximum drawdown in this episode as decimal */
  drawdown: number;
  /** Date of recovery to peak level (null if not yet recovered) */
  recoveryDate: string | null;
  /** Duration from peak to trough in trading days */
  peakToTroughDays: number;
  /** Duration from peak to recovery in trading days (null if not recovered) */
  peakToRecoveryDays: number | null;
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
 * Compute rolling annualized Sharpe ratio over a trailing window.
 * @param dailyReturns - array of daily returns
 * @param windowSize - number of trading days in the window (default: 60 ≈ 3 months)
 * @param riskFreeAnnual - annual risk-free rate (default: 0 for CN A-shares)
 */
export function computeRollingSharpe(
  dailyReturns: DailyReturnPoint[],
  windowSize = 60,
  riskFreeAnnual = 0,
): RollingSharpePoint[] {
  if (dailyReturns.length < windowSize) return [];
  const rfDaily = riskFreeAnnual / 252;
  const result: RollingSharpePoint[] = [];
  for (let i = windowSize - 1; i < dailyReturns.length; i++) {
    const window = dailyReturns.slice(i - windowSize + 1, i + 1);
    const excess = window.map((r) => r.dailyReturn - rfDaily);
    const mean = excess.reduce((a, b) => a + b, 0) / windowSize;
    const variance = excess.reduce((sum, r) => sum + (r - mean) ** 2, 0) / windowSize;
    const std = Math.sqrt(variance);
    const sharpe = std > 0 ? (mean / std) * Math.sqrt(252) : 0;
    result.push({ date: dailyReturns[i]!.date, sharpe });
  }
  return result;
}

/**
 * Compute underwater (time spent in drawdown) statistics.
 */
export function computeUnderwaterStats(snapshots: EquitySnapshot[]): UnderwaterStats {
  const empty: UnderwaterStats = {
    totalEpisodes: 0,
    avgDurationDays: 0,
    maxDurationDays: 0,
    pctTimeUnderwater: 0,
    currentDurationDays: 0,
  };
  if (snapshots.length < 2) return empty;

  let peakEquity = -Infinity;
  let episodes = 0;
  let inDrawdown = false;
  let currentDuration = 0;
  let maxDuration = 0;
  let totalUnderwaterDays = 0;
  const durations: number[] = [];

  for (const s of snapshots) {
    if (s.equity >= peakEquity) {
      peakEquity = s.equity;
      if (inDrawdown) {
        durations.push(currentDuration);
        if (currentDuration > maxDuration) maxDuration = currentDuration;
        currentDuration = 0;
        inDrawdown = false;
      }
    } else {
      if (!inDrawdown) {
        inDrawdown = true;
        episodes++;
        currentDuration = 0;
      }
      currentDuration++;
      totalUnderwaterDays++;
    }
  }

  // Handle ongoing drawdown at end of series
  if (inDrawdown) {
    durations.push(currentDuration);
    if (currentDuration > maxDuration) maxDuration = currentDuration;
  }

  const avgDuration =
    durations.length > 0
      ? durations.reduce((a, b) => a + b, 0) / durations.length
      : 0;

  return {
    totalEpisodes: episodes,
    avgDurationDays: avgDuration,
    maxDurationDays: maxDuration,
    pctTimeUnderwater:
      snapshots.length > 1 ? (totalUnderwaterDays / (snapshots.length - 1)) * 100 : 0,
    currentDurationDays: inDrawdown ? currentDuration : 0,
  };
}

/**
 * Compute Value at Risk (VaR) and Conditional VaR (Expected Shortfall)
 * using the historical simulation method.
 * @param dailyReturns - daily return series
 * @param confidence - confidence level (default: 0.95 → 95%)
 */
export function computeVaR(
  dailyReturns: DailyReturnPoint[],
  confidence = 0.95,
): VaRResult {
  if (dailyReturns.length === 0) {
    return { var: 0, cvar: 0, confidence };
  }

  const sorted = dailyReturns.map((r) => r.dailyReturn).sort((a, b) => a - b);
  const alpha = 1 - confidence;
  const idx = Math.max(Math.floor(sorted.length * alpha), 0);

  const varValue = Math.abs(sorted[idx]!);

  // CVaR = average of all returns below VaR threshold
  const tailCount = Math.max(idx, 1);
  const tail = sorted.slice(0, tailCount);
  const cvar = Math.abs(tail.reduce((a, b) => a + b, 0) / tail.length);

  return { var: varValue, cvar, confidence };
}

/**
 * Compute benchmark-relative metrics (Alpha, Beta, Tracking Error, IR, Correlation).
 * @param snapshots - strategy equity snapshots
 * @param benchmarkSnapshots - benchmark equity snapshots (must share dates with strategy)
 * @param riskFreeAnnual - annual risk-free rate (default: 0.03)
 */
export function computeBenchmarkMetrics(
  snapshots: EquitySnapshot[],
  benchmarkSnapshots: EquitySnapshot[],
  riskFreeAnnual = 0.03,
): BenchmarkMetrics | null {
  if (snapshots.length < 2 || benchmarkSnapshots.length < 2) return null;

  // Align by date
  const benchMap = new Map(benchmarkSnapshots.map((s) => [s.date, s]));
  const aligned: Array<{ date: string; equity: number; bench: number }> = [];
  for (const s of snapshots) {
    const b = benchMap.get(s.date);
    if (b) aligned.push({ date: s.date, equity: s.equity, bench: b.equity });
  }
  if (aligned.length < 2) return null;

  const stratReturns: number[] = [];
  const benchReturns: number[] = [];
  for (let i = 1; i < aligned.length; i++) {
    const prev = aligned[i - 1]!;
    const curr = aligned[i]!;
    stratReturns.push(prev.equity > 0 ? curr.equity / prev.equity - 1 : 0);
    benchReturns.push(prev.bench > 0 ? curr.bench / prev.bench - 1 : 0);
  }

  const n = stratReturns.length;
  const rfDaily = riskFreeAnnual / 252;

  // Means
  const meanS = stratReturns.reduce((a, b) => a + b, 0) / n;
  const meanB = benchReturns.reduce((a, b) => a + b, 0) / n;

  // Excess returns
  const excessS = stratReturns.map((r) => r - rfDaily);
  const excessB = benchReturns.map((r) => r - rfDaily);
  const meanExS = excessS.reduce((a, b) => a + b, 0) / n;
  const meanExB = excessB.reduce((a, b) => a + b, 0) / n;

  // Beta = Cov(strategy, bench) / Var(bench)
  let cov = 0;
  let varB = 0;
  for (let i = 0; i < n; i++) {
    cov += (excessS[i]! - meanExS) * (excessB[i]! - meanExB);
    varB += (excessB[i]! - meanExB) ** 2;
  }
  cov /= n;
  varB /= n;

  const beta = varB > 0 ? cov / varB : 1;
  const alpha = (meanExS - beta * meanExB) * 252;

  // Tracking error and information ratio
  const diffs = stratReturns.map((r, i) => r - benchReturns[i]!);
  const meanDiff = diffs.reduce((a, b) => a + b, 0) / n;
  const diffVar = diffs.reduce((sum, d) => sum + (d - meanDiff) ** 2, 0) / (n - 1);
  const trackingError = Math.sqrt(diffVar) * Math.sqrt(252);
  const informationRatio = trackingError > 0 ? (meanDiff * 252) / trackingError : 0;

  // Correlation
  let varS = 0;
  let covSB = 0;
  for (let i = 0; i < n; i++) {
    varS += (stratReturns[i]! - meanS) ** 2;
    covSB += (stratReturns[i]! - meanS) * (benchReturns[i]! - meanB);
  }
  const stdS = Math.sqrt(varS / n);
  const stdB = Math.sqrt(varB);
  const correlation = stdS > 0 && stdB > 0 ? covSB / n / (stdS * stdB) : 0;

  // Benchmark total/annualized return
  const benchStart = aligned[0]!.bench;
  const benchEnd = aligned[aligned.length - 1]!.bench;
  const benchTotal = benchStart > 0 ? benchEnd / benchStart - 1 : 0;
  const years = aligned.length / 252;
  const benchAnn = years > 0 && benchStart > 0 ? (benchEnd / benchStart) ** (1 / years) - 1 : 0;

  // Strategy total return
  const stratStart = aligned[0]!.equity;
  const stratEnd = aligned[aligned.length - 1]!.equity;
  const stratTotal = stratStart > 0 ? stratEnd / stratStart - 1 : 0;

  return {
    alpha,
    beta,
    trackingError,
    informationRatio,
    correlation,
    benchmarkTotalReturn: benchTotal,
    benchmarkAnnualizedReturn: benchAnn,
    excessReturn: stratTotal - benchTotal,
  };
}

/**
 * Compute return distribution statistics (skewness, kurtosis, percentiles).
 */
export function computeReturnDistribution(dailyReturns: DailyReturnPoint[]): ReturnDistribution {
  const empty: ReturnDistribution = {
    skewness: 0,
    kurtosis: 0,
    medianReturn: 0,
    percentile5: 0,
    percentile25: 0,
    percentile75: 0,
    percentile95: 0,
  };
  if (dailyReturns.length < 3) return empty;

  const returns = dailyReturns.map((r) => r.dailyReturn);
  const sorted = [...returns].sort((a, b) => a - b);
  const n = returns.length;

  const mean = returns.reduce((a, b) => a + b, 0) / n;
  const variance = returns.reduce((sum, r) => sum + (r - mean) ** 2, 0) / n;
  const stdDev = Math.sqrt(variance);

  // Skewness: E[(X-μ)^3] / σ^3
  let skewness = 0;
  if (stdDev > 0) {
    const m3 = returns.reduce((sum, r) => sum + ((r - mean) / stdDev) ** 3, 0) / n;
    skewness = m3;
  }

  // Excess kurtosis: E[(X-μ)^4] / σ^4 - 3
  let kurtosis = 0;
  if (stdDev > 0) {
    const m4 = returns.reduce((sum, r) => sum + ((r - mean) / stdDev) ** 4, 0) / n;
    kurtosis = m4 - 3;
  }

  // Percentile helper
  const percentile = (p: number): number => {
    const rank = (p / 100) * (sorted.length - 1);
    const lo = Math.floor(rank);
    const hi = Math.ceil(rank);
    if (lo === hi) return sorted[lo]!;
    return sorted[lo]! + (sorted[hi]! - sorted[lo]!) * (rank - lo);
  };

  return {
    skewness,
    kurtosis,
    medianReturn: percentile(50),
    percentile5: percentile(5),
    percentile25: percentile(25),
    percentile75: percentile(75),
    percentile95: percentile(95),
  };
}

/**
 * Find all drawdown episodes (peak → trough → recovery).
 * Useful for analyzing the full drawdown history, not just the maximum.
 * @param snapshots - equity snapshots sorted by date ascending
 * @param minDrawdown - minimum drawdown threshold to report (default: 0.01 = 1%)
 */
export function computeAllDrawdownEpisodes(
  snapshots: EquitySnapshot[],
  minDrawdown = 0.01,
): DrawdownEpisode[] {
  if (snapshots.length < 2) return [];

  const episodes: DrawdownEpisode[] = [];
  let peakEquity = snapshots[0]!.equity;
  let peakDate = snapshots[0]!.date;
  let peakIdx = 0;
  let troughEquity = peakEquity;
  let troughDate = peakDate;
  let troughIdx = 0;
  let inDrawdown = false;
  let maxDdInEpisode = 0;

  for (let i = 0; i < snapshots.length; i++) {
    const s = snapshots[i]!;
    if (s.equity >= peakEquity) {
      // New peak — close any open episode
      if (inDrawdown && maxDdInEpisode >= minDrawdown) {
        episodes.push({
          peakDate,
          peakEquity,
          troughDate,
          troughEquity,
          drawdown: maxDdInEpisode,
          recoveryDate: s.date,
          peakToTroughDays: troughIdx - peakIdx,
          peakToRecoveryDays: i - peakIdx,
        });
      }
      peakEquity = s.equity;
      peakDate = s.date;
      peakIdx = i;
      troughEquity = s.equity;
      troughDate = s.date;
      troughIdx = i;
      inDrawdown = false;
      maxDdInEpisode = 0;
    } else {
      inDrawdown = true;
      const dd = peakEquity > 0 ? (peakEquity - s.equity) / peakEquity : 0;
      if (dd > maxDdInEpisode) {
        maxDdInEpisode = dd;
        troughEquity = s.equity;
        troughDate = s.date;
        troughIdx = i;
      }
    }
  }

  // Handle ongoing (unrecovered) drawdown at end
  if (inDrawdown && maxDdInEpisode >= minDrawdown) {
    episodes.push({
      peakDate,
      peakEquity,
      troughDate,
      troughEquity,
      drawdown: maxDdInEpisode,
      recoveryDate: null,
      peakToTroughDays: troughIdx - peakIdx,
      peakToRecoveryDays: null,
    });
  }

  return episodes;
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
  const dailyReturns = computeDailyReturns(snapshots);
  return {
    cumulativeReturns: computeCumulativeReturns(snapshots),
    dailyReturns,
    drawdownCurve: computeDrawdownCurve(snapshots),
    maxDrawdownInterval: computeMaxDrawdownInterval(snapshots),
    drawdownEpisodes: computeAllDrawdownEpisodes(snapshots),
    monthlyReturns: computeMonthlyReturns(snapshots),
    weeklyReturns: computeWeeklyReturns(snapshots),
    metrics: computeMetrics(snapshots),
    underwaterStats: computeUnderwaterStats(snapshots),
    var95: computeVaR(dailyReturns),
    returnDistribution: computeReturnDistribution(dailyReturns),
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
