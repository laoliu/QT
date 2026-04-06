/**
 * LiveBacktestCompare — compares live/paper trading performance against
 * backtest results for the same strategy.
 *
 * Detects drift between what the backtest promised and what actually
 * happened in live trading. Provides:
 *   - Return comparison (backtest vs live)
 *   - Equity curve overlay data
 *   - Drawdown comparison
 *   - Execution quality metrics (slippage, fill rate)
 *   - Signal fidelity (how many backtest signals were actually executed)
 *   - Drift score (composite measure of live-vs-backtest divergence)
 */

import type { BacktestResult, TradingSignal, TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Decay alert configuration ────────────────────────────────

export interface DecayAlertConfig {
  /** Default return deviation threshold (0-1). Default 0.20 (20%). */
  returnDeviationThreshold: number;
  /** Per-strategy threshold overrides. */
  perStrategyOverrides?: Record<string, number>;
}

export const DEFAULT_DECAY_ALERT_CONFIG: DecayAlertConfig = {
  returnDeviationThreshold: 0.20,
};

// ── Types ────────────────────────────────────────────────────

export interface LiveBacktestCompareRequest {
  /** Strategy ID to compare */
  strategyId: string;
  /** Date range for comparison (YYYY-MM-DD) */
  startDate: string;
  endDate: string;
  /** Backtest result to compare against */
  backtestResult: BacktestResult;
  /** Live equity snapshots from EquityHistory */
  liveEquity: EquitySnapshot[];
  /** Live trade records */
  liveTrades: TradeRecord[];
  /** Live signals emitted */
  liveSignals: TradingSignal[];
}

export interface ReturnComparison {
  /** Backtest total return (%) */
  backtestReturn: number;
  /** Live total return (%) */
  liveReturn: number;
  /** Difference: live - backtest (%) */
  returnDiff: number;
  /** Backtest annualized return (%) */
  backtestAnnualizedReturn: number;
  /** Live annualized return (%) — estimated from snapshot period */
  liveAnnualizedReturn: number;
}

export interface DrawdownComparison {
  /** Backtest max drawdown (%) */
  backtestMaxDrawdown: number;
  /** Live max drawdown (%) */
  liveMaxDrawdown: number;
  /** Difference: live - backtest (%). Positive means live had worse drawdown. */
  drawdownDiff: number;
}

export interface RiskComparison {
  /** Backtest Sharpe ratio */
  backtestSharpe: number;
  /** Live Sharpe ratio (estimated from daily returns) */
  liveSharpe: number;
  /** Backtest win rate */
  backtestWinRate: number;
  /** Live win rate */
  liveWinRate: number;
}

export interface ExecutionQuality {
  /** Number of signals generated in backtest */
  backtestSignalCount: number;
  /** Number of signals actually executed live */
  liveExecutedCount: number;
  /** Fill rate: liveExecutedCount / backtestSignalCount (0-1) */
  fillRate: number;
  /** Average slippage (%) between signal price and fill price */
  avgSlippagePct: number;
  /** Total trades in backtest vs live */
  backtestTrades: number;
  liveTrades: number;
}

export interface EquityCurveOverlay {
  /** Aligned data points for charting: date + backtest value + live value */
  points: Array<{
    date: string;
    backtestValue: number | null;
    liveValue: number | null;
  }>;
}

export type DriftSeverity = "low" | "moderate" | "high" | "critical";

export interface DriftAnalysis {
  /** Composite drift score (0-100). Higher = more divergence. */
  score: number;
  severity: DriftSeverity;
  /** Individual component scores */
  components: {
    returnDrift: number;
    drawdownDrift: number;
    executionDrift: number;
    riskDrift: number;
  };
  /** Actionable observations */
  observations: string[];
}

export interface LiveBacktestCompareResult {
  strategyId: string;
  period: { start: string; end: string };
  returnComparison: ReturnComparison;
  drawdownComparison: DrawdownComparison;
  riskComparison: RiskComparison;
  executionQuality: ExecutionQuality;
  equityCurveOverlay: EquityCurveOverlay;
  drift: DriftAnalysis;
  /** Pearson correlation between backtest and live equity curves (−1 to 1) */
  curveCorrelation: number;
  /** Average signal-to-execution delay in milliseconds */
  avgSignalDelayMs: number;
  /** Whether the return deviation exceeds the configured decay alert threshold */
  decayAlertTriggered: boolean;
  /** Benchmark comparison (if benchmark data available) */
  benchmarkComparison?: BenchmarkComparison;
  /** Extended risk metrics (Sortino, Calmar, profitFactor) */
  extendedRisk?: ExtendedRiskComparison;
  /** Commission/tax drag decomposition */
  costDecomposition?: CostDecomposition;
}

// ── Benchmark comparison ───────────────────────────────────

export interface BenchmarkComparison {
  /** Benchmark total return (%) */
  benchmarkReturn: number;
  /** Alpha: live return - benchmark return */
  liveAlpha: number;
  /** Alpha: backtest return - benchmark return */
  backtestAlpha: number;
  /** Did underperformance come from the market or the strategy? */
  attribution: "market_decline" | "strategy_specific" | "both" | "outperforming";
  /** Benchmark equity curve for overlay */
  benchmarkCurve: Array<{ date: string; value: number }>;
}

// ── Extended risk metrics ──────────────────────────────────

export interface ExtendedRiskComparison {
  backtestSortino: number;
  liveSortino: number;
  backtestCalmar: number;
  liveCalmar: number;
  backtestProfitFactor: number;
  liveProfitFactor: number;
}

// ── Cost decomposition ─────────────────────────────────────

export interface CostDecomposition {
  /** Total commission paid in live trading */
  totalCommission: number;
  /** Total tax (stamp duty) paid */
  totalTax: number;
  /** Commission + tax as % of starting equity */
  costPct: number;
  /** How much of the live-vs-backtest gap is explained by costs */
  costExplainedGapPct: number;
  /** Estimated backtest cost assumption (from BacktestResult.totalFees) */
  backtestFees: number;
  /** Cost difference: live costs - backtest costs */
  costDiff: number;
}

// ── Out-of-sample (forward walk) comparison ──────────────────

export interface OutOfSampleResult {
  strategyId: string;
  /** The backtest's training period */
  inSamplePeriod: { start: string; end: string };
  /** The live trading period (post-backtest) */
  outOfSamplePeriod: { start: string; end: string };
  /** In-sample (backtest) performance */
  inSampleReturn: number;
  inSampleSharpe: number;
  inSampleMaxDrawdown: number;
  /** Out-of-sample (live) performance */
  outOfSampleReturn: number;
  outOfSampleSharpe: number;
  outOfSampleMaxDrawdown: number;
  /** Degradation metrics */
  returnDecay: number; // annualized return decay %
  sharpeDecay: number; // Sharpe ratio decay
  /** "on_track" | "degrading" | "failed" */
  verdict: "on_track" | "degrading" | "failed";
  /** Observations */
  observations: string[];
}

// ── Multi-backtest closest match ──────────────────────────────

export interface BacktestMatchCandidate {
  backtestId: string;
  strategyId: string;
  /** How closely the live equity tracks this backtest (0-100, 100=perfect match) */
  correlationScore: number;
  /** RMSE between normalized equity curves */
  rmse: number;
  /** Return difference (live - backtest) */
  returnDiff: number;
  backtestReturn: number;
  backtestSharpe: number;
  backtestPeriod: { start: string; end: string };
}

export interface MultiBacktestMatchResult {
  /** The live equity period used for matching */
  livePeriod: { start: string; end: string };
  liveReturn: number;
  /** All candidates ranked by correlation */
  candidates: BacktestMatchCandidate[];
  /** Best matching backtest */
  bestMatch: BacktestMatchCandidate | null;
}

// ── Per-symbol breakdown ───────────────────────────────────

export interface PerSymbolBreakdown {
  symbol: string;
  backtestReturn: number;
  liveReturn: number;
  returnDiff: number;
  backtestTrades: number;
  liveTrades: number;
  missedSignals: number;
  avgSlippagePct: number;
  totalCost: number;
  driftContribution: number;
}

export interface PerSymbolBreakdownResult {
  strategyId: string;
  period: { start: string; end: string };
  symbols: PerSymbolBreakdown[];
  /** Symbol contributing the most to overall drift */
  worstSymbol: string | null;
  /** Symbol with the best live performance relative to backtest */
  bestSymbol: string | null;
}

// ── Period-segmented comparison ──────────────────────────────

export type SegmentGranularity = "weekly" | "monthly";

export interface PeriodSegment {
  /** Segment label (e.g. "2024-01" for monthly, "2024-W03" for weekly) */
  label: string;
  startDate: string;
  endDate: string;
  backtestReturn: number;
  liveReturn: number;
  returnDiff: number;
  backtestTrades: number;
  liveTrades: number;
  driftScore: number;
}

export interface PeriodSegmentedResult {
  strategyId: string;
  granularity: SegmentGranularity;
  segments: PeriodSegment[];
  /** Segment where drift first exceeded 'moderate' threshold */
  driftOnset: string | null;
  /** Segment with the worst drift */
  worstSegment: string | null;
}

// ── Trade-level matching ────────────────────────────────────

export interface TradeMatch {
  /** Backtest signal that generated this expected trade */
  backtestSignal: TradingSignal;
  /** Matched live trade (null if signal was not executed) */
  liveTrade: TradeRecord | null;
  /** Match quality: "matched", "missed" (no live trade), "extra" (live trade without backtest signal) */
  matchType: "matched" | "missed" | "extra";
  /** Time delay between signal and execution (ms), null if unmatched */
  latencyMs: number | null;
  /** Price slippage (%), null if unmatched */
  slippagePct: number | null;
  /** P&L difference between backtest expectation and live result */
  pnlDiff: number | null;
}

export interface TradeMatchResult {
  strategyId: string;
  period: { start: string; end: string };
  matches: TradeMatch[];
  /** Summary statistics */
  summary: {
    totalBacktestSignals: number;
    matchedCount: number;
    missedCount: number;
    extraLiveCount: number;
    matchRate: number;
    avgLatencyMs: number;
    avgSlippagePct: number;
    /** Symbols with the most missed signals */
    problematicSymbols: Array<{ symbol: string; missedCount: number }>;
  };
}

// ── Historical drift tracking ───────────────────────────────

export interface DriftSnapshot {
  date: string;
  score: number;
  severity: DriftSeverity;
  components: DriftAnalysis["components"];
}

export interface DriftHistory {
  strategyId: string;
  snapshots: DriftSnapshot[];
  /** Trend: is drift improving, stable, or worsening? */
  trend: "improving" | "stable" | "worsening";
  /** Average drift over the period */
  avgDrift: number;
  /** Number of times severity crossed into 'high' or 'critical' */
  alertCount: number;
}

// ── Market regime types ────────────────────────────────────

export type MarketRegime = "bull" | "bear" | "sideways" | "high_volatility" | "low_volatility";

export interface RegimeAwareCompareResult {
  strategyId: string;
  period: { start: string; end: string };
  /** Detected market regime during comparison period */
  regime: MarketRegime;
  /** Raw comparison result */
  comparison: LiveBacktestCompareResult;
  /** Adjusted drift score that accounts for regime expectations */
  adjustedDriftScore: number;
  /** How much of the drift is attributable to regime change vs strategy decay */
  regimeAttribution: {
    /** Drift explained by regime change (0-100%) */
    regimeExplainedPct: number;
    /** Drift that is strategy-specific (0-100%) */
    strategySpecificPct: number;
  };
  /** Regime-contextual observations */
  regimeObservations: string[];
}

// ── Position-weighted comparison types ──────────────────────

export interface PositionWeightedCompareResult {
  strategyId: string;
  period: { start: string; end: string };
  /** Capital-weighted return comparison (weighted by actual position size) */
  weightedReturnComparison: {
    backtestWeightedReturn: number;
    liveWeightedReturn: number;
    weightedReturnDiff: number;
  };
  /** Capital-weighted drift score (positions with more capital weigh more) */
  weightedDriftScore: number;
  /** Per-symbol weights and their contributions to total drift */
  symbolWeights: Array<{
    symbol: string;
    /** Capital allocated to this symbol as fraction of total portfolio */
    capitalWeight: number;
    /** This symbol's contribution to the weighted drift score */
    weightedDriftContribution: number;
    liveReturn: number;
    backtestReturn: number;
  }>;
  /** Observations about capital-weighted performance */
  observations: string[];
}

// ── Auto-scheduled monitoring types ─────────────────────────

export interface ScheduledDriftCheckConfig {
  /** Strategies to monitor */
  strategyIds: string[];
  /** Check frequency: "daily" | "weekly" */
  frequency: "daily" | "weekly";
  /** Rolling window in days for drift calculation (default 30) */
  windowDays: number;
  /** Drift score threshold to trigger alert (default 35 = "high") */
  alertThreshold: number;
  /** Trading mode: paper or live */
  mode: "paper" | "live";
}

export const DEFAULT_SCHEDULED_DRIFT_CONFIG: ScheduledDriftCheckConfig = {
  strategyIds: [],
  frequency: "daily",
  windowDays: 30,
  alertThreshold: 35,
  mode: "paper",
};

export interface ScheduledDriftCheckResult {
  /** When this check was performed */
  checkTime: string;
  /** Results per strategy */
  strategyResults: Array<{
    strategyId: string;
    driftScore: number;
    severity: DriftSeverity;
    returnDiff: number;
    alertTriggered: boolean;
    observations: string[];
  }>;
  /** Strategies that need attention */
  alertedStrategies: string[];
  /** Overall portfolio health: "healthy" | "warning" | "critical" */
  portfolioHealth: "healthy" | "warning" | "critical";
}

// ── Implementation ──────────────────────────────────────────

/**
 * Compare live trading performance against a backtest result.
 */
export function compareLiveVsBacktest(
  req: LiveBacktestCompareRequest,
  decayConfig: DecayAlertConfig = DEFAULT_DECAY_ALERT_CONFIG,
): LiveBacktestCompareResult {
  const { strategyId, startDate, endDate, backtestResult, liveEquity, liveTrades, liveSignals } =
    req;

  const returnComp = computeReturnComparison(backtestResult, liveEquity);
  const drawdownComp = computeDrawdownComparison(backtestResult, liveEquity);
  const riskComp = computeRiskComparison(backtestResult, liveEquity, liveTrades);
  const execQuality = computeExecutionQuality(backtestResult, liveTrades, liveSignals);
  const overlay = buildEquityCurveOverlay(backtestResult, liveEquity);
  const drift = computeDrift(returnComp, drawdownComp, riskComp, execQuality);

  // Curve correlation: Pearson correlation between overlapping equity curve points
  const curveCorrelation = computeCurveCorrelation(overlay);

  // Average signal delay
  const avgSignalDelayMs = computeAvgSignalDelay(liveTrades, liveSignals);

  // Decay alert: check if return deviation exceeds threshold
  const threshold = decayConfig.perStrategyOverrides?.[strategyId]
    ?? decayConfig.returnDeviationThreshold;
  const returnDeviation = returnComp.backtestReturn !== 0
    ? Math.abs(returnComp.liveReturn / returnComp.backtestReturn - 1)
    : Math.abs(returnComp.returnDiff) / 100;
  const decayAlertTriggered = returnDeviation >= threshold;

  const result: LiveBacktestCompareResult = {
    strategyId,
    period: { start: startDate, end: endDate },
    returnComparison: returnComp,
    drawdownComparison: drawdownComp,
    riskComparison: riskComp,
    executionQuality: execQuality,
    equityCurveOverlay: overlay,
    drift,
    curveCorrelation,
    avgSignalDelayMs,
    decayAlertTriggered,
  };

  // Benchmark comparison (if benchmark data available in equity snapshots or backtest)
  const benchComp = computeBenchmarkComparison(backtestResult, liveEquity, returnComp);
  if (benchComp) result.benchmarkComparison = benchComp;

  // Extended risk metrics
  result.extendedRisk = computeExtendedRisk(backtestResult, liveEquity, liveTrades, drawdownComp);

  // Cost decomposition
  result.costDecomposition = computeCostDecomposition(backtestResult, liveTrades, liveEquity, returnComp);

  return result;
}

function computeReturnComparison(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
): ReturnComparison {
  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  let liveReturn = 0;
  let liveAnnualizedReturn = 0;

  if (sorted.length >= 2) {
    const first = sorted[0]!;
    const last = sorted[sorted.length - 1]!;
    if (first.equity > 0) {
      liveReturn = ((last.equity - first.equity) / first.equity) * 100;
      const days = Math.max(
        1,
        (new Date(last.date).getTime() - new Date(first.date).getTime()) / 86_400_000,
      );
      liveAnnualizedReturn = (Math.pow(1 + liveReturn / 100, 365 / days) - 1) * 100;
    }
  }

  return {
    backtestReturn: bt.totalReturn,
    liveReturn,
    returnDiff: liveReturn - bt.totalReturn,
    backtestAnnualizedReturn: bt.annualizedReturn,
    liveAnnualizedReturn,
  };
}

function computeDrawdownComparison(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
): DrawdownComparison {
  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  let liveMaxDrawdown = 0;

  if (sorted.length > 0) {
    let peak = -Infinity;
    for (const snap of sorted) {
      if (snap.equity > peak) peak = snap.equity;
      const dd = peak > 0 ? ((peak - snap.equity) / peak) * 100 : 0;
      if (dd > liveMaxDrawdown) liveMaxDrawdown = dd;
    }
  }

  return {
    backtestMaxDrawdown: bt.maxDrawdown,
    liveMaxDrawdown,
    drawdownDiff: liveMaxDrawdown - bt.maxDrawdown,
  };
}

function computeRiskComparison(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
  liveTrades: TradeRecord[],
): RiskComparison {
  // Estimate live Sharpe from daily PnL
  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  let liveSharpe = 0;

  if (sorted.length >= 5) {
    const dailyReturns: number[] = [];
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]!;
      if (prev.equity > 0) {
        dailyReturns.push((sorted[i]!.equity - prev.equity) / prev.equity);
      }
    }
    if (dailyReturns.length > 1) {
      const mean = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
      const variance =
        dailyReturns.reduce((a, r) => a + (r - mean) ** 2, 0) / (dailyReturns.length - 1);
      const std = Math.sqrt(variance);
      if (std > 0) {
        liveSharpe = (mean / std) * Math.sqrt(252);
      }
    }
  }

  // Live win rate from closed trades
  const closedTrades = liveTrades.filter((t) => t.exitPrice != null);
  const wins = closedTrades.filter((t) => t.netPnl > 0).length;
  const liveWinRate = closedTrades.length > 0 ? (wins / closedTrades.length) * 100 : 0;

  return {
    backtestSharpe: bt.sharpeRatio,
    liveSharpe,
    backtestWinRate: bt.winRate,
    liveWinRate,
  };
}

function computeExecutionQuality(
  bt: BacktestResult,
  liveTrades: TradeRecord[],
  liveSignals: TradingSignal[],
): ExecutionQuality {
  const backtestSignalCount = bt.signals.length;
  const liveExecutedCount = liveTrades.length;

  // Compute average slippage: compare signal price with actual fill price
  let totalSlippage = 0;
  let slippageCount = 0;

  for (const trade of liveTrades) {
    // Try to find the matching signal
    const matchingSignal = liveSignals.find(
      (s) =>
        s.symbol === trade.symbol &&
        s.strategyId === trade.strategyId &&
        Math.abs(s.timestamp - new Date(trade.entryTime).getTime()) < 5 * 60 * 1000,
    );
    if (matchingSignal && matchingSignal.price > 0) {
      const slippage =
        Math.abs(trade.entryPrice - matchingSignal.price) / matchingSignal.price;
      totalSlippage += slippage * 100;
      slippageCount++;
    }
  }

  return {
    backtestSignalCount,
    liveExecutedCount,
    fillRate: backtestSignalCount > 0 ? liveExecutedCount / backtestSignalCount : 0,
    avgSlippagePct: slippageCount > 0 ? totalSlippage / slippageCount : 0,
    backtestTrades: bt.totalTrades,
    liveTrades: liveTrades.length,
  };
}

function buildEquityCurveOverlay(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
): EquityCurveOverlay {
  // Build a map of all dates
  const dateMap = new Map<
    string,
    { backtestValue: number | null; liveValue: number | null }
  >();

  // Normalize backtest equity to percentage of initial
  for (const point of bt.equityCurve) {
    dateMap.set(point.date, {
      backtestValue: bt.initialCash > 0 ? (point.value / bt.initialCash) * 100 : point.value,
      liveValue: null,
    });
  }

  // Normalize live equity to percentage of initial
  const sortedLive = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  const liveInitial = sortedLive.length > 0 ? sortedLive[0]!.equity : 1;

  for (const snap of sortedLive) {
    const existing = dateMap.get(snap.date);
    const liveValue = liveInitial > 0 ? (snap.equity / liveInitial) * 100 : 0;
    if (existing) {
      existing.liveValue = liveValue;
    } else {
      dateMap.set(snap.date, { backtestValue: null, liveValue });
    }
  }

  // Sort by date and return
  const points = [...dateMap.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, values]) => ({
      date,
      backtestValue: values.backtestValue,
      liveValue: values.liveValue,
    }));

  return { points };
}

// ── Curve correlation ────────────────────────────────────────

function computeCurveCorrelation(overlay: EquityCurveOverlay): number {
  // Only use points where both backtest and live values exist
  const both = overlay.points.filter(
    (p) => p.backtestValue != null && p.liveValue != null,
  ) as Array<{ date: string; backtestValue: number; liveValue: number }>;

  if (both.length < 2) return 0;

  const n = both.length;
  const meanBt = both.reduce((a, p) => a + p.backtestValue, 0) / n;
  const meanLive = both.reduce((a, p) => a + p.liveValue, 0) / n;

  let covSum = 0;
  let varBt = 0;
  let varLive = 0;
  for (const p of both) {
    const dBt = p.backtestValue - meanBt;
    const dLive = p.liveValue - meanLive;
    covSum += dBt * dLive;
    varBt += dBt * dBt;
    varLive += dLive * dLive;
  }

  const denom = Math.sqrt(varBt * varLive);
  return denom > 0 ? covSum / denom : 0;
}

// ── Average signal delay ─────────────────────────────────────

function computeAvgSignalDelay(
  liveTrades: TradeRecord[],
  liveSignals: TradingSignal[],
): number {
  let totalDelay = 0;
  let count = 0;

  for (const trade of liveTrades) {
    const matchingSignal = liveSignals.find(
      (s) =>
        s.symbol === trade.symbol &&
        s.strategyId === trade.strategyId &&
        Math.abs(s.timestamp - new Date(trade.entryTime).getTime()) < 5 * 60 * 1000,
    );
    if (matchingSignal) {
      const delay = Math.abs(new Date(trade.entryTime).getTime() - matchingSignal.timestamp);
      totalDelay += delay;
      count++;
    }
  }

  return count > 0 ? totalDelay / count : 0;
}

// ── Period-segmented comparison ──────────────────────────────

/**
 * Break a comparison into weekly or monthly segments to identify when drift started.
 */
export function comparePeriodSegmented(
  req: LiveBacktestCompareRequest,
  granularity: SegmentGranularity = "monthly",
): PeriodSegmentedResult {
  const { strategyId, startDate, endDate, backtestResult, liveEquity, liveTrades } = req;

  const segments: PeriodSegment[] = [];
  const boundaries = computeSegmentBoundaries(startDate, endDate, granularity);

  for (const { label, start, end } of boundaries) {
    // Filter backtest equity curve for this segment
    const btPoints = backtestResult.equityCurve.filter(
      (p) => p.date >= start && p.date <= end,
    );
    const btReturn = computeSegmentReturn(btPoints.map((p) => p.value));

    // Filter live equity for this segment
    const livePoints = liveEquity
      .filter((s) => s.date >= start && s.date <= end)
      .sort((a, b) => a.date.localeCompare(b.date));
    const liveReturn = computeSegmentReturn(livePoints.map((p) => p.equity));

    // Count trades in this segment
    const btSignals = backtestResult.signals.filter((s) => {
      const d = new Date(s.timestamp).toISOString().slice(0, 10);
      return d >= start && d <= end;
    });
    const lTrades = liveTrades.filter(
      (t) => t.entryTime >= start && t.entryTime <= end + "T23:59:59",
    );

    const returnDiff = liveReturn - btReturn;
    const driftScore = Math.min(100, Math.abs(returnDiff) * 2);

    segments.push({
      label,
      startDate: start,
      endDate: end,
      backtestReturn: btReturn,
      liveReturn,
      returnDiff,
      backtestTrades: btSignals.length,
      liveTrades: lTrades.length,
      driftScore: Math.round(driftScore),
    });
  }

  // Find drift onset (first segment where drift > 35 = "high")
  const driftOnset = segments.find((s) => s.driftScore > 35)?.label ?? null;
  // Find worst segment
  const worstSegment = segments.length > 0
    ? segments.reduce((a, b) => (b.driftScore > a.driftScore ? b : a)).label
    : null;

  return { strategyId, granularity, segments, driftOnset, worstSegment };
}

function computeSegmentBoundaries(
  startDate: string,
  endDate: string,
  granularity: SegmentGranularity,
): Array<{ label: string; start: string; end: string }> {
  const boundaries: Array<{ label: string; start: string; end: string }> = [];
  const start = new Date(startDate);
  const end = new Date(endDate);

  if (granularity === "monthly") {
    const cursor = new Date(start.getFullYear(), start.getMonth(), 1);
    while (cursor <= end) {
      const year = cursor.getFullYear();
      const month = cursor.getMonth();
      const segStart = new Date(year, month, 1);
      const segEnd = new Date(year, month + 1, 0); // last day of month
      const label = `${year}-${String(month + 1).padStart(2, "0")}`;
      boundaries.push({
        label,
        start: Math.max(segStart.getTime(), start.getTime()) === start.getTime() && segStart < start
          ? startDate
          : segStart.toISOString().slice(0, 10),
        end: segEnd > end ? endDate : segEnd.toISOString().slice(0, 10),
      });
      cursor.setMonth(cursor.getMonth() + 1);
    }
  } else {
    // Weekly
    const cursor = new Date(start);
    // Align to Monday
    const dayOfWeek = cursor.getDay();
    const mondayOffset = dayOfWeek === 0 ? -6 : 1 - dayOfWeek;
    cursor.setDate(cursor.getDate() + mondayOffset);

    let weekNum = 1;
    while (cursor <= end) {
      const weekStart = new Date(cursor);
      const weekEnd = new Date(cursor);
      weekEnd.setDate(weekEnd.getDate() + 6);
      const label = `${weekStart.getFullYear()}-W${String(weekNum).padStart(2, "0")}`;
      boundaries.push({
        label,
        start: weekStart < start ? startDate : weekStart.toISOString().slice(0, 10),
        end: weekEnd > end ? endDate : weekEnd.toISOString().slice(0, 10),
      });
      cursor.setDate(cursor.getDate() + 7);
      weekNum++;
    }
  }

  return boundaries;
}

function computeSegmentReturn(values: number[]): number {
  if (values.length < 2) return 0;
  const first = values[0]!;
  const last = values[values.length - 1]!;
  return first > 0 ? ((last - first) / first) * 100 : 0;
}

// ── Trade-level matching ────────────────────────────────────

/**
 * Match backtest signals with live trades to find individual divergences.
 */
export function matchTrades(
  req: LiveBacktestCompareRequest,
): TradeMatchResult {
  const { strategyId, startDate, endDate, backtestResult, liveTrades, liveSignals } = req;

  const matches: TradeMatch[] = [];
  const matchedLiveTradeIds = new Set<string>();

  // For each backtest signal, try to find a matching live trade
  for (const btSignal of backtestResult.signals) {
    const signalDate = new Date(btSignal.timestamp).toISOString().slice(0, 10);
    if (signalDate < startDate || signalDate > endDate) continue;

    // Find closest matching live trade (same symbol, same direction, within 1 day)
    let bestMatch: TradeRecord | null = null;
    let bestTimeDiff = Infinity;

    for (const trade of liveTrades) {
      if (matchedLiveTradeIds.has(trade.tradeId)) continue;
      if (trade.symbol !== btSignal.symbol) continue;

      const isSameDirection =
        (btSignal.direction === "buy" && trade.side === "buy") ||
        (btSignal.direction === "sell" && trade.side === "sell") ||
        (btSignal.direction === "short" && trade.side === "short") ||
        (btSignal.direction === "cover" && trade.side === "cover");
      if (!isSameDirection) continue;

      const timeDiff = Math.abs(
        new Date(trade.entryTime).getTime() - btSignal.timestamp,
      );
      if (timeDiff < 24 * 60 * 60 * 1000 && timeDiff < bestTimeDiff) {
        bestTimeDiff = timeDiff;
        bestMatch = trade;
      }
    }

    if (bestMatch) {
      matchedLiveTradeIds.add(bestMatch.tradeId);
      const slippage =
        btSignal.price > 0
          ? ((bestMatch.entryPrice - btSignal.price) / btSignal.price) * 100
          : 0;

      matches.push({
        backtestSignal: btSignal,
        liveTrade: bestMatch,
        matchType: "matched",
        latencyMs: bestTimeDiff,
        slippagePct: slippage,
        pnlDiff: bestMatch.netPnl, // live PnL (backtest per-trade PnL is not tracked individually)
      });
    } else {
      matches.push({
        backtestSignal: btSignal,
        liveTrade: null,
        matchType: "missed",
        latencyMs: null,
        slippagePct: null,
        pnlDiff: null,
      });
    }
  }

  // Find extra live trades (not matched to any backtest signal)
  for (const trade of liveTrades) {
    if (matchedLiveTradeIds.has(trade.tradeId)) continue;
    matches.push({
      backtestSignal: {
        timestamp: new Date(trade.entryTime).getTime(),
        symbol: trade.symbol,
        direction: trade.side as TradingSignal["direction"],
        price: trade.entryPrice,
        strategyId,
      },
      liveTrade: trade,
      matchType: "extra",
      latencyMs: null,
      slippagePct: null,
      pnlDiff: trade.netPnl,
    });
  }

  // Summary
  const matched = matches.filter((m) => m.matchType === "matched");
  const missed = matches.filter((m) => m.matchType === "missed");
  const extra = matches.filter((m) => m.matchType === "extra");

  const avgLatency = matched.length > 0
    ? matched.reduce((a, m) => a + (m.latencyMs ?? 0), 0) / matched.length
    : 0;
  const avgSlippage = matched.length > 0
    ? matched.reduce((a, m) => a + Math.abs(m.slippagePct ?? 0), 0) / matched.length
    : 0;

  // Find problematic symbols
  const missedBySymbol = new Map<string, number>();
  for (const m of missed) {
    const sym = m.backtestSignal.symbol;
    missedBySymbol.set(sym, (missedBySymbol.get(sym) ?? 0) + 1);
  }
  const problematicSymbols = [...missedBySymbol.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([symbol, missedCount]) => ({ symbol, missedCount }));

  const totalBtSignals = backtestResult.signals.filter((s) => {
    const d = new Date(s.timestamp).toISOString().slice(0, 10);
    return d >= startDate && d <= endDate;
  }).length;

  return {
    strategyId,
    period: { start: startDate, end: endDate },
    matches,
    summary: {
      totalBacktestSignals: totalBtSignals,
      matchedCount: matched.length,
      missedCount: missed.length,
      extraLiveCount: extra.length,
      matchRate: totalBtSignals > 0 ? matched.length / totalBtSignals : 0,
      avgLatencyMs: avgLatency,
      avgSlippagePct: avgSlippage,
      problematicSymbols,
    },
  };
}

// ── Historical drift tracking ───────────────────────────────

/**
 * Compute drift snapshots over rolling windows to track strategy degradation.
 */
export function computeDriftHistory(
  req: LiveBacktestCompareRequest,
  windowDays = 30,
  stepDays = 7,
): DriftHistory {
  const { strategyId, startDate, endDate, backtestResult, liveEquity, liveTrades, liveSignals } = req;

  const snapshots: DriftSnapshot[] = [];
  const startMs = new Date(startDate).getTime();
  const endMs = new Date(endDate).getTime();
  const dayMs = 86_400_000;

  let cursor = startMs;
  while (cursor + windowDays * dayMs <= endMs + dayMs) {
    const winStart = new Date(cursor).toISOString().slice(0, 10);
    const winEnd = new Date(cursor + windowDays * dayMs).toISOString().slice(0, 10);

    // Filter data for this window
    const windowEquity = liveEquity.filter((s) => s.date >= winStart && s.date <= winEnd);
    const windowTrades = liveTrades.filter(
      (t) => t.entryTime >= winStart && t.entryTime <= winEnd + "T23:59:59",
    );
    const windowSignals = liveSignals.filter((s) => {
      const d = new Date(s.timestamp).toISOString().slice(0, 10);
      return d >= winStart && d <= winEnd;
    });

    if (windowEquity.length >= 2) {
      // Build a windowed backtest result with only the relevant equity curve
      const windowBtEquity = backtestResult.equityCurve.filter(
        (p) => p.date >= winStart && p.date <= winEnd,
      );
      const windowBtSignals = backtestResult.signals.filter((s) => {
        const d = new Date(s.timestamp).toISOString().slice(0, 10);
        return d >= winStart && d <= winEnd;
      });

      const windowBt: BacktestResult = {
        ...backtestResult,
        startDate: winStart,
        endDate: winEnd,
        equityCurve: windowBtEquity,
        signals: windowBtSignals,
        totalTrades: windowBtSignals.filter((s) => s.direction === "buy" || s.direction === "cover").length,
      };

      const returnComp = computeReturnComparison(windowBt, windowEquity);
      const drawdownComp = computeDrawdownComparison(windowBt, windowEquity);
      const riskComp = computeRiskComparison(windowBt, windowEquity, windowTrades);
      const execQuality = computeExecutionQuality(windowBt, windowTrades, windowSignals);
      const drift = computeDrift(returnComp, drawdownComp, riskComp, execQuality);

      snapshots.push({
        date: winEnd,
        score: drift.score,
        severity: drift.severity,
        components: drift.components,
      });
    }

    cursor += stepDays * dayMs;
  }

  // Determine trend
  let trend: DriftHistory["trend"] = "stable";
  if (snapshots.length >= 3) {
    const firstHalf = snapshots.slice(0, Math.floor(snapshots.length / 2));
    const secondHalf = snapshots.slice(Math.floor(snapshots.length / 2));
    const avgFirst = firstHalf.reduce((a, s) => a + s.score, 0) / firstHalf.length;
    const avgSecond = secondHalf.reduce((a, s) => a + s.score, 0) / secondHalf.length;
    if (avgSecond - avgFirst > 5) trend = "worsening";
    else if (avgFirst - avgSecond > 5) trend = "improving";
  }

  const avgDrift = snapshots.length > 0
    ? snapshots.reduce((a, s) => a + s.score, 0) / snapshots.length
    : 0;
  const alertCount = snapshots.filter(
    (s) => s.severity === "high" || s.severity === "critical",
  ).length;

  return { strategyId, snapshots, trend, avgDrift: Math.round(avgDrift), alertCount };
}

// ── Core drift computation (unchanged) ──────────────────────

function computeDrift(
  returnComp: ReturnComparison,
  drawdownComp: DrawdownComparison,
  riskComp: RiskComparison,
  execQuality: ExecutionQuality,
): DriftAnalysis {
  // Return drift: how much live return diverges from backtest
  const returnDrift = Math.min(
    100,
    Math.abs(returnComp.returnDiff) * 2,
  );

  // Drawdown drift: live drawdown worse than backtest
  const drawdownDrift = Math.min(
    100,
    drawdownComp.drawdownDiff > 0 ? drawdownComp.drawdownDiff * 3 : 0,
  );

  // Execution drift: based on fill rate and slippage
  const fillRatePenalty = (1 - execQuality.fillRate) * 50;
  const slippagePenalty = Math.min(50, execQuality.avgSlippagePct * 20);
  const executionDrift = Math.min(100, fillRatePenalty + slippagePenalty);

  // Risk drift: Sharpe ratio and win rate divergence
  const sharpeDiff = Math.abs(riskComp.backtestSharpe - riskComp.liveSharpe);
  const winRateDiff = Math.abs(riskComp.backtestWinRate - riskComp.liveWinRate);
  const riskDrift = Math.min(100, sharpeDiff * 20 + winRateDiff);

  // Composite score (weighted average)
  const score = Math.round(
    returnDrift * 0.35 + drawdownDrift * 0.25 + executionDrift * 0.25 + riskDrift * 0.15,
  );

  // Severity classification
  let severity: DriftSeverity;
  if (score <= 15) severity = "low";
  else if (score <= 35) severity = "moderate";
  else if (score <= 60) severity = "high";
  else severity = "critical";

  // Generate observations
  const observations: string[] = [];

  if (returnComp.returnDiff < -5) {
    observations.push(
      `实盘收益 (${returnComp.liveReturn.toFixed(1)}%) 显著低于回测 (${returnComp.backtestReturn.toFixed(1)}%)，差距 ${Math.abs(returnComp.returnDiff).toFixed(1)}%`,
    );
  } else if (returnComp.returnDiff > 5) {
    observations.push(
      `实盘收益 (${returnComp.liveReturn.toFixed(1)}%) 高于回测 (${returnComp.backtestReturn.toFixed(1)}%)，可能存在幸存者偏差`,
    );
  } else {
    observations.push(
      `收益偏差在可接受范围内 (差距 ${Math.abs(returnComp.returnDiff).toFixed(1)}%)`,
    );
  }

  if (drawdownComp.drawdownDiff > 5) {
    observations.push(
      `实盘最大回撤 (${drawdownComp.liveMaxDrawdown.toFixed(1)}%) 明显超过回测 (${drawdownComp.backtestMaxDrawdown.toFixed(1)}%)，注意风控`,
    );
  }

  if (execQuality.fillRate < 0.7) {
    observations.push(
      `信号执行率较低 (${(execQuality.fillRate * 100).toFixed(0)}%)，可能因流动性或价格偏移未成交`,
    );
  }

  if (execQuality.avgSlippagePct > 0.3) {
    observations.push(
      `平均滑点 ${execQuality.avgSlippagePct.toFixed(2)}% 偏高，建议优化下单时机`,
    );
  }

  if (sharpeDiff > 0.5) {
    observations.push(
      `Sharpe差异明显 (回测: ${riskComp.backtestSharpe.toFixed(2)}, 实盘: ${riskComp.liveSharpe.toFixed(2)})`,
    );
  }

  if (winRateDiff > 10) {
    observations.push(
      `胜率偏差 ${winRateDiff.toFixed(1)}% (回测: ${riskComp.backtestWinRate.toFixed(1)}%, 实盘: ${riskComp.liveWinRate.toFixed(1)}%)`,
    );
  }

  if (observations.length === 1 && severity === "low") {
    observations.push("策略实盘表现与回测基本一致，运行状态良好");
  }

  return {
    score,
    severity,
    components: {
      returnDrift: Math.round(returnDrift),
      drawdownDrift: Math.round(drawdownDrift),
      executionDrift: Math.round(executionDrift),
      riskDrift: Math.round(riskDrift),
    },
    observations,
  };
}

// ── Benchmark comparison ──────────────────────────────────

function computeBenchmarkComparison(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
  returnComp: ReturnComparison,
): BenchmarkComparison | null {
  // Try benchmark from backtest result first, then from equity snapshots
  const hasBtBenchmark = bt.benchmarkCurve && bt.benchmarkCurve.length >= 2;
  const sortedLive = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  const hasBenchmarkInEquity = sortedLive.some((s) => s.benchmark != null && s.benchmark > 0);

  if (!hasBtBenchmark && !hasBenchmarkInEquity) return null;

  let benchmarkReturn = 0;
  let benchmarkCurve: Array<{ date: string; value: number }> = [];

  if (hasBtBenchmark) {
    const bc = bt.benchmarkCurve!;
    const first = bc[0]!;
    const last = bc[bc.length - 1]!;
    benchmarkReturn = first.value > 0 ? ((last.value - first.value) / first.value) * 100 : 0;
    // Normalize to percentage of initial
    benchmarkCurve = bc.map((p) => ({
      date: p.date,
      value: first.value > 0 ? (p.value / first.value) * 100 : 0,
    }));
  } else if (hasBenchmarkInEquity) {
    const withBench = sortedLive.filter((s) => s.benchmark != null && s.benchmark > 0);
    if (withBench.length >= 2) {
      const first = withBench[0]!.benchmark!;
      const last = withBench[withBench.length - 1]!.benchmark!;
      benchmarkReturn = first > 0 ? ((last - first) / first) * 100 : 0;
      benchmarkCurve = withBench.map((s) => ({
        date: s.date,
        value: first > 0 ? (s.benchmark! / first) * 100 : 0,
      }));
    }
  }

  const liveAlpha = returnComp.liveReturn - benchmarkReturn;
  const backtestAlpha = returnComp.backtestReturn - benchmarkReturn;

  // Determine attribution
  let attribution: BenchmarkComparison["attribution"];
  if (liveAlpha >= 0) {
    attribution = "outperforming";
  } else if (benchmarkReturn < -2 && returnComp.returnDiff < -3) {
    attribution = "both";
  } else if (benchmarkReturn < -2) {
    attribution = "market_decline";
  } else {
    attribution = "strategy_specific";
  }

  return { benchmarkReturn, liveAlpha, backtestAlpha, attribution, benchmarkCurve };
}

// ── Extended risk metrics ──────────────────────────────────

function computeExtendedRisk(
  bt: BacktestResult,
  liveEquity: EquitySnapshot[],
  liveTrades: TradeRecord[],
  drawdownComp: DrawdownComparison,
): ExtendedRiskComparison {
  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));

  // Live Sortino: downside deviation only
  let liveSortino = 0;
  if (sorted.length >= 5) {
    const dailyReturns: number[] = [];
    for (let i = 1; i < sorted.length; i++) {
      const prev = sorted[i - 1]!;
      if (prev.equity > 0) {
        dailyReturns.push((sorted[i]!.equity - prev.equity) / prev.equity);
      }
    }
    if (dailyReturns.length > 1) {
      const mean = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
      const downsideReturns = dailyReturns.filter((r) => r < 0);
      const downsideVariance = downsideReturns.length > 0
        ? downsideReturns.reduce((a, r) => a + r ** 2, 0) / downsideReturns.length
        : 0;
      const downsideStd = Math.sqrt(downsideVariance);
      if (downsideStd > 0) {
        liveSortino = (mean / downsideStd) * Math.sqrt(252);
      }
    }
  }

  // Live Calmar: annualized return / max drawdown
  let liveCalmar = 0;
  if (sorted.length >= 2 && drawdownComp.liveMaxDrawdown > 0) {
    const first = sorted[0]!;
    const last = sorted[sorted.length - 1]!;
    const days = Math.max(1, (new Date(last.date).getTime() - new Date(first.date).getTime()) / 86_400_000);
    const totalReturn = first.equity > 0 ? (last.equity - first.equity) / first.equity : 0;
    const annualizedReturn = (Math.pow(1 + totalReturn, 365 / days) - 1) * 100;
    liveCalmar = annualizedReturn / drawdownComp.liveMaxDrawdown;
  }

  // Live profit factor: gross profit / gross loss
  const closedTrades = liveTrades.filter((t) => t.exitPrice != null);
  let grossProfit = 0;
  let grossLoss = 0;
  for (const t of closedTrades) {
    if (t.netPnl > 0) grossProfit += t.netPnl;
    else grossLoss += Math.abs(t.netPnl);
  }
  const liveProfitFactor = grossLoss > 0 ? grossProfit / grossLoss : grossProfit > 0 ? Infinity : 0;

  return {
    backtestSortino: bt.sortinoRatio ?? 0,
    liveSortino,
    backtestCalmar: bt.calmarRatio ?? 0,
    liveCalmar,
    backtestProfitFactor: bt.profitFactor ?? 0,
    liveProfitFactor: Number.isFinite(liveProfitFactor) ? liveProfitFactor : 0,
  };
}

// ── Cost decomposition ─────────────────────────────────────

function computeCostDecomposition(
  bt: BacktestResult,
  liveTrades: TradeRecord[],
  liveEquity: EquitySnapshot[],
  returnComp: ReturnComparison,
): CostDecomposition {
  let totalCommission = 0;
  let totalTax = 0;
  for (const t of liveTrades) {
    totalCommission += t.commission;
    // Use explicit tax field if available, otherwise infer from PnL breakdown
    if (t.tax != null && t.tax > 0) {
      totalTax += t.tax;
    } else {
      const impliedTax = t.grossPnl - t.netPnl - t.commission;
      if (impliedTax > 0) totalTax += impliedTax;
    }
  }

  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  const startEquity = sorted.length > 0 ? sorted[0]!.equity : 1;
  const totalLiveCost = totalCommission + totalTax;
  const costPct = startEquity > 0 ? (totalLiveCost / startEquity) * 100 : 0;

  const backtestFees = bt.totalFees ?? 0;
  const costDiff = totalLiveCost - backtestFees;

  // How much of the return gap is explained by cost difference
  const returnGap = Math.abs(returnComp.returnDiff);
  const costDiffPct = startEquity > 0 ? (costDiff / startEquity) * 100 : 0;
  const costExplainedGapPct = returnGap > 0 ? Math.min(100, (Math.abs(costDiffPct) / returnGap) * 100) : 0;

  return {
    totalCommission,
    totalTax,
    costPct,
    costExplainedGapPct,
    backtestFees,
    costDiff,
  };
}

// ── Per-symbol breakdown ───────────────────────────────────

/**
 * Break down comparison metrics by individual symbol.
 */
export function compareBySymbol(
  req: LiveBacktestCompareRequest,
): PerSymbolBreakdownResult {
  const { strategyId, startDate, endDate, backtestResult, liveTrades, liveSignals } = req;

  // Collect all symbols from backtest signals and live trades
  const symbolSet = new Set<string>();
  for (const s of backtestResult.signals) symbolSet.add(s.symbol);
  for (const t of liveTrades) symbolSet.add(t.symbol);

  // Estimate per-symbol backtest return proportionally based on signal count share.
  // Since BacktestResult only stores aggregate equity, we distribute the total P&L
  // proportionally to each symbol's share of backtest signals (buy+sell pairs).
  const btSignalsBySymbol = new Map<string, number>();
  let totalBtSignals = 0;
  for (const s of backtestResult.signals) {
    const d = new Date(s.timestamp).toISOString().slice(0, 10);
    if (d >= startDate && d <= endDate) {
      btSignalsBySymbol.set(s.symbol, (btSignalsBySymbol.get(s.symbol) ?? 0) + 1);
      totalBtSignals++;
    }
  }
  const btTotalPnl = backtestResult.finalValue - backtestResult.initialCash;

  const symbols: PerSymbolBreakdown[] = [];

  for (const symbol of symbolSet) {
    // Backtest signals for this symbol in range
    const btSignals = backtestResult.signals.filter((s) => {
      if (s.symbol !== symbol) return false;
      const d = new Date(s.timestamp).toISOString().slice(0, 10);
      return d >= startDate && d <= endDate;
    });

    // Live trades for this symbol
    const symLiveTrades = liveTrades.filter((t) => t.symbol === symbol);
    const closedLive = symLiveTrades.filter((t) => t.exitPrice != null);

    // Live return from closed trades
    const liveGross = closedLive.reduce((a, t) => a + t.netPnl, 0);
    const liveCost = closedLive.reduce((a, t) => a + t.commission, 0);

    // Estimate backtest per-symbol return from signals (buy count × avg expected movement)
    // Since backtest doesn't store per-symbol returns, use signal count as proxy
    const buySignals = btSignals.filter((s) => s.direction === "buy" || s.direction === "cover");
    const sellSignals = btSignals.filter((s) => s.direction === "sell" || s.direction === "short");

    // Match signals to find missed ones
    const matchedLiveIds = new Set<string>();
    let missedSignals = 0;
    let totalSlippage = 0;
    let slippageCount = 0;

    for (const btSig of btSignals) {
      let found = false;
      for (const trade of symLiveTrades) {
        if (matchedLiveIds.has(trade.tradeId)) continue;
        const isSameDir =
          (btSig.direction === "buy" && trade.side === "buy") ||
          (btSig.direction === "sell" && trade.side === "sell") ||
          (btSig.direction === "short" && trade.side === "short") ||
          (btSig.direction === "cover" && trade.side === "cover");
        if (!isSameDir) continue;
        const timeDiff = Math.abs(new Date(trade.entryTime).getTime() - btSig.timestamp);
        if (timeDiff < 24 * 60 * 60 * 1000) {
          matchedLiveIds.add(trade.tradeId);
          if (btSig.price > 0) {
            totalSlippage += Math.abs(trade.entryPrice - btSig.price) / btSig.price * 100;
            slippageCount++;
          }
          found = true;
          break;
        }
      }
      if (!found) missedSignals++;
    }

    // Estimate backtest return proportionally from total P&L based on signal share
    const symBtSignalCount = btSignalsBySymbol.get(symbol) ?? 0;
    const signalShare = totalBtSignals > 0 ? symBtSignalCount / totalBtSignals : 0;
    const backtestReturn = signalShare * btTotalPnl;
    const liveReturn = liveGross;

    symbols.push({
      symbol,
      backtestReturn,
      liveReturn,
      returnDiff: liveReturn - backtestReturn,
      backtestTrades: buySignals.length + sellSignals.length,
      liveTrades: symLiveTrades.length,
      missedSignals,
      avgSlippagePct: slippageCount > 0 ? totalSlippage / slippageCount : 0,
      totalCost: liveCost,
      driftContribution: missedSignals + (slippageCount > 0 ? totalSlippage / slippageCount : 0),
    });
  }

  // Sort by drift contribution descending
  symbols.sort((a, b) => b.driftContribution - a.driftContribution);

  return {
    strategyId,
    period: { start: startDate, end: endDate },
    symbols,
    worstSymbol: symbols.length > 0 ? symbols[0]!.symbol : null,
    bestSymbol: symbols.length > 0 ? symbols[symbols.length - 1]!.symbol : null,
  };
}

// ── Out-of-sample comparison ──────────────────────────────────

/**
 * Compare live performance in a period AFTER the backtest ended
 * against the backtest's in-sample metrics. This answers "how is
 * the strategy doing in genuinely unseen market conditions?"
 */
export function compareOutOfSample(
  backtestResult: BacktestResult,
  liveEquity: EquitySnapshot[],
  liveTrades: TradeRecord[],
): OutOfSampleResult {
  const strategyId = backtestResult.strategyId;
  const inSamplePeriod = { start: backtestResult.startDate, end: backtestResult.endDate };

  // Live equity should be after backtest end date
  const sortedLive = [...liveEquity]
    .filter((s) => s.date > backtestResult.endDate)
    .sort((a, b) => a.date.localeCompare(b.date));

  const outOfSamplePeriod = {
    start: sortedLive.length > 0 ? sortedLive[0]!.date : backtestResult.endDate,
    end: sortedLive.length > 0 ? sortedLive[sortedLive.length - 1]!.date : backtestResult.endDate,
  };

  // In-sample metrics (from backtest)
  const inSampleReturn = backtestResult.annualizedReturn;
  const inSampleSharpe = backtestResult.sharpeRatio;
  const inSampleMaxDrawdown = backtestResult.maxDrawdown;

  // Out-of-sample metrics (from live equity)
  let outOfSampleReturn = 0;
  let outOfSampleSharpe = 0;
  let outOfSampleMaxDrawdown = 0;

  if (sortedLive.length >= 2) {
    const first = sortedLive[0]!;
    const last = sortedLive[sortedLive.length - 1]!;

    // Total return
    const totalReturn = first.equity > 0 ? (last.equity - first.equity) / first.equity : 0;
    const days = Math.max(1, (new Date(last.date).getTime() - new Date(first.date).getTime()) / 86_400_000);
    outOfSampleReturn = (Math.pow(1 + totalReturn, 365 / days) - 1) * 100;

    // Sharpe from daily returns
    const dailyReturns: number[] = [];
    for (let i = 1; i < sortedLive.length; i++) {
      const prev = sortedLive[i - 1]!;
      if (prev.equity > 0) {
        dailyReturns.push((sortedLive[i]!.equity - prev.equity) / prev.equity);
      }
    }
    if (dailyReturns.length > 1) {
      const mean = dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length;
      const variance = dailyReturns.reduce((a, r) => a + (r - mean) ** 2, 0) / (dailyReturns.length - 1);
      const std = Math.sqrt(variance);
      if (std > 0) outOfSampleSharpe = (mean / std) * Math.sqrt(252);
    }

    // Max drawdown
    let peak = -Infinity;
    for (const snap of sortedLive) {
      if (snap.equity > peak) peak = snap.equity;
      const dd = peak > 0 ? ((peak - snap.equity) / peak) * 100 : 0;
      if (dd > outOfSampleMaxDrawdown) outOfSampleMaxDrawdown = dd;
    }
  }

  const returnDecay = inSampleReturn - outOfSampleReturn;
  const sharpeDecay = inSampleSharpe - outOfSampleSharpe;

  // Verdict
  let verdict: OutOfSampleResult["verdict"];
  if (returnDecay < 5 && sharpeDecay < 0.3) {
    verdict = "on_track";
  } else if (returnDecay < 15 && sharpeDecay < 1.0) {
    verdict = "degrading";
  } else {
    verdict = "failed";
  }

  const observations: string[] = [];
  if (sortedLive.length < 5) {
    observations.push("样本外数据不足，结论可能不可靠");
  }
  if (verdict === "on_track") {
    observations.push(`策略在样本外表现良好，年化收益衰减仅 ${returnDecay.toFixed(1)}%`);
  } else if (verdict === "degrading") {
    observations.push(`策略在样本外表现衰减，年化收益下降 ${returnDecay.toFixed(1)}%，Sharpe下降 ${sharpeDecay.toFixed(2)}`);
  } else {
    observations.push(`策略在样本外严重失效，年化收益衰减 ${returnDecay.toFixed(1)}%，建议停用并重新优化`);
  }
  if (outOfSampleMaxDrawdown > inSampleMaxDrawdown * 1.5) {
    observations.push(`样本外最大回撤 (${outOfSampleMaxDrawdown.toFixed(1)}%) 远超回测期 (${inSampleMaxDrawdown.toFixed(1)}%)`);
  }

  return {
    strategyId,
    inSamplePeriod,
    outOfSamplePeriod,
    inSampleReturn,
    inSampleSharpe,
    inSampleMaxDrawdown,
    outOfSampleReturn,
    outOfSampleSharpe,
    outOfSampleMaxDrawdown,
    returnDecay,
    sharpeDecay,
    verdict,
    observations,
  };
}

// ── CSV export ──────────────────────────────────────────────────

/**
 * Export comparison results and trade matching as a CSV string.
 * Produces two sections: summary metrics and trade-level detail.
 */
export function exportComparisonCsv(
  result: LiveBacktestCompareResult,
  matched: TradeMatchResult,
): string {
  const lines: string[] = [];

  // BOM for Excel compatibility
  lines.push("\uFEFF");

  // Section 1: Summary
  lines.push("=== 对比摘要 ===");
  lines.push("指标,回测,实盘,偏差");

  const rc = result.returnComparison;
  lines.push(`总收益(%),${rc.backtestReturn.toFixed(2)},${rc.liveReturn.toFixed(2)},${rc.returnDiff.toFixed(2)}`);
  lines.push(`年化收益(%),${rc.backtestAnnualizedReturn.toFixed(2)},${rc.liveAnnualizedReturn.toFixed(2)},${(rc.liveAnnualizedReturn - rc.backtestAnnualizedReturn).toFixed(2)}`);

  const dc = result.drawdownComparison;
  lines.push(`最大回撤(%),${dc.backtestMaxDrawdown.toFixed(2)},${dc.liveMaxDrawdown.toFixed(2)},${dc.drawdownDiff.toFixed(2)}`);

  const rkc = result.riskComparison;
  lines.push(`Sharpe,${rkc.backtestSharpe.toFixed(2)},${rkc.liveSharpe.toFixed(2)},${(rkc.liveSharpe - rkc.backtestSharpe).toFixed(2)}`);
  lines.push(`胜率(%),${rkc.backtestWinRate.toFixed(1)},${rkc.liveWinRate.toFixed(1)},${(rkc.liveWinRate - rkc.backtestWinRate).toFixed(1)}`);

  const eq = result.executionQuality;
  lines.push(`交易数,${eq.backtestTrades},${eq.liveTrades},${eq.liveTrades - eq.backtestTrades}`);
  lines.push(`信号执行率(%),100,${(eq.fillRate * 100).toFixed(0)},${((eq.fillRate - 1) * 100).toFixed(0)}`);
  lines.push(`平均滑点(%),0,${eq.avgSlippagePct.toFixed(3)},${eq.avgSlippagePct.toFixed(3)}`);

  lines.push("");
  lines.push(`漂移得分,${result.drift.score}/100`);
  lines.push(`漂移严重程度,${result.drift.severity}`);

  if (result.extendedRisk) {
    const er = result.extendedRisk;
    lines.push("");
    lines.push("=== 扩展风险指标 ===");
    lines.push("指标,回测,实盘");
    lines.push(`Sortino,${er.backtestSortino.toFixed(2)},${er.liveSortino.toFixed(2)}`);
    lines.push(`Calmar,${er.backtestCalmar.toFixed(2)},${er.liveCalmar.toFixed(2)}`);
    lines.push(`盈亏比,${er.backtestProfitFactor.toFixed(2)},${er.liveProfitFactor.toFixed(2)}`);
  }

  if (result.costDecomposition) {
    const cd = result.costDecomposition;
    lines.push("");
    lines.push("=== 成本分析 ===");
    lines.push(`实盘佣金,${cd.totalCommission.toFixed(2)}`);
    lines.push(`实盘税费,${cd.totalTax.toFixed(2)}`);
    lines.push(`成本占比(%),${cd.costPct.toFixed(3)}`);
    lines.push(`回测费用,${cd.backtestFees.toFixed(2)}`);
    lines.push(`成本解释收益差距(%),${cd.costExplainedGapPct.toFixed(1)}`);
  }

  // Section 2: Equity curve overlay
  lines.push("");
  lines.push("=== 权益曲线 ===");
  lines.push("日期,回测权益(%),实盘权益(%)");
  for (const pt of result.equityCurveOverlay.points) {
    lines.push(`${pt.date},${pt.backtestValue?.toFixed(2) ?? ""},${pt.liveValue?.toFixed(2) ?? ""}`);
  }

  // Section 3: Trade-level matching
  lines.push("");
  lines.push("=== 逐笔交易匹配 ===");
  lines.push("类型,标的,方向,信号价格,成交价格,滑点(%),实盘盈亏,延迟(秒)");
  for (const m of matched.matches) {
    const type = m.matchType === "matched" ? "已匹配" : m.matchType === "missed" ? "未执行" : "额外交易";
    const symbol = m.backtestSignal.symbol;
    const dir = m.backtestSignal.direction;
    const sigPrice = m.backtestSignal.price.toFixed(2);
    const fillPrice = m.liveTrade ? m.liveTrade.entryPrice.toFixed(2) : "";
    const slippage = m.slippagePct != null ? m.slippagePct.toFixed(3) : "";
    const pnl = m.liveTrade ? m.liveTrade.netPnl.toFixed(2) : "";
    const latency = m.latencyMs != null ? (m.latencyMs / 1000).toFixed(1) : "";
    lines.push(`${type},${symbol},${dir},${sigPrice},${fillPrice},${slippage},${pnl},${latency}`);
  }

  return lines.join("\n");
}

// ── Multi-backtest closest match ──────────────────────────────

/**
 * Given multiple backtest results and live equity, find which backtest
 * the live performance most closely matches.
 */
export function findClosestBacktest(
  backtests: Array<{ id: string; result: BacktestResult }>,
  liveEquity: EquitySnapshot[],
): MultiBacktestMatchResult {
  const sortedLive = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  const livePeriod = {
    start: sortedLive.length > 0 ? sortedLive[0]!.date : "",
    end: sortedLive.length > 0 ? sortedLive[sortedLive.length - 1]!.date : "",
  };

  let liveReturn = 0;
  if (sortedLive.length >= 2) {
    const first = sortedLive[0]!;
    const last = sortedLive[sortedLive.length - 1]!;
    if (first.equity > 0) liveReturn = ((last.equity - first.equity) / first.equity) * 100;
  }

  // Normalize live equity to percentages
  const liveInitial = sortedLive.length > 0 ? sortedLive[0]!.equity : 1;
  const livePctByDate = new Map<string, number>();
  for (const snap of sortedLive) {
    livePctByDate.set(snap.date, liveInitial > 0 ? (snap.equity / liveInitial) * 100 : 100);
  }

  const candidates: BacktestMatchCandidate[] = [];

  for (const { id, result: bt } of backtests) {
    // Normalize backtest equity to percentages
    const btPctByDate = new Map<string, number>();
    for (const p of bt.equityCurve) {
      btPctByDate.set(p.date, bt.initialCash > 0 ? (p.value / bt.initialCash) * 100 : 100);
    }

    // Find overlapping dates
    const overlap: Array<{ live: number; bt: number }> = [];
    for (const [date, livePct] of livePctByDate) {
      const btPct = btPctByDate.get(date);
      if (btPct != null) {
        overlap.push({ live: livePct, bt: btPct });
      }
    }

    if (overlap.length < 2) {
      candidates.push({
        backtestId: id,
        strategyId: bt.strategyId,
        correlationScore: 0,
        rmse: Infinity,
        returnDiff: liveReturn - bt.totalReturn,
        backtestReturn: bt.totalReturn,
        backtestSharpe: bt.sharpeRatio,
        backtestPeriod: { start: bt.startDate, end: bt.endDate },
      });
      continue;
    }

    // Pearson correlation
    const n = overlap.length;
    const meanLive = overlap.reduce((a, p) => a + p.live, 0) / n;
    const meanBt = overlap.reduce((a, p) => a + p.bt, 0) / n;
    let covSum = 0;
    let varLive = 0;
    let varBt = 0;
    for (const p of overlap) {
      const dL = p.live - meanLive;
      const dB = p.bt - meanBt;
      covSum += dL * dB;
      varLive += dL * dL;
      varBt += dB * dB;
    }
    const denom = Math.sqrt(varLive * varBt);
    const correlation = denom > 0 ? covSum / denom : 0;
    const correlationScore = Math.round(Math.max(0, correlation * 100));

    // RMSE
    const rmseSum = overlap.reduce((a, p) => a + (p.live - p.bt) ** 2, 0);
    const rmse = Math.sqrt(rmseSum / n);

    candidates.push({
      backtestId: id,
      strategyId: bt.strategyId,
      correlationScore,
      rmse: Math.round(rmse * 100) / 100,
      returnDiff: liveReturn - bt.totalReturn,
      backtestReturn: bt.totalReturn,
      backtestSharpe: bt.sharpeRatio,
      backtestPeriod: { start: bt.startDate, end: bt.endDate },
    });
  }

  // Sort by correlation descending
  candidates.sort((a, b) => b.correlationScore - a.correlationScore);

  return {
    livePeriod,
    liveReturn,
    candidates,
    bestMatch: candidates.length > 0 ? candidates[0]! : null,
  };
}

// ── Regime-aware comparison ─────────────────────────────────

/**
 * Detect market regime from equity curve behavior and benchmark data.
 */
function detectMarketRegime(
  liveEquity: EquitySnapshot[],
  backtestResult: BacktestResult,
): MarketRegime {
  // Use benchmark curve if available, otherwise use live equity as proxy
  const sorted = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  if (sorted.length < 5) return "sideways";

  const hasBenchmark = sorted.some((s) => s.benchmark != null && s.benchmark > 0);
  const values = hasBenchmark
    ? sorted.filter((s) => s.benchmark != null).map((s) => s.benchmark!)
    : sorted.map((s) => s.equity);

  if (values.length < 5) return "sideways";

  // Calculate trend: linear regression slope
  const n = values.length;
  const xMean = (n - 1) / 2;
  const yMean = values.reduce((a, b) => a + b, 0) / n;
  let num = 0;
  let den = 0;
  for (let i = 0; i < n; i++) {
    num += (i - xMean) * (values[i]! - yMean);
    den += (i - xMean) ** 2;
  }
  const slope = den > 0 ? num / den : 0;
  const slopePct = yMean > 0 ? (slope / yMean) * 100 : 0;

  // Calculate volatility: std of daily returns
  const dailyReturns: number[] = [];
  for (let i = 1; i < values.length; i++) {
    if (values[i - 1]! > 0) {
      dailyReturns.push((values[i]! - values[i - 1]!) / values[i - 1]!);
    }
  }
  const retMean = dailyReturns.length > 0
    ? dailyReturns.reduce((a, b) => a + b, 0) / dailyReturns.length
    : 0;
  const retVariance = dailyReturns.length > 1
    ? dailyReturns.reduce((a, r) => a + (r - retMean) ** 2, 0) / (dailyReturns.length - 1)
    : 0;
  const annualizedVol = Math.sqrt(retVariance) * Math.sqrt(252) * 100;

  // Classify regime
  if (annualizedVol > 30) return "high_volatility";
  if (annualizedVol < 10) return "low_volatility";
  if (slopePct > 0.3) return "bull";
  if (slopePct < -0.3) return "bear";
  return "sideways";
}

/**
 * Get expected drift adjustments for each market regime.
 * In bear/high-vol markets, some drift is expected and the score should be relaxed.
 */
function getRegimeAdjustment(regime: MarketRegime): {
  driftMultiplier: number;
  expectedReturnDrift: number;
} {
  switch (regime) {
    case "bull":
      return { driftMultiplier: 1.0, expectedReturnDrift: 0 };
    case "bear":
      return { driftMultiplier: 0.7, expectedReturnDrift: 5 };
    case "sideways":
      return { driftMultiplier: 0.9, expectedReturnDrift: 2 };
    case "high_volatility":
      return { driftMultiplier: 0.6, expectedReturnDrift: 8 };
    case "low_volatility":
      return { driftMultiplier: 1.1, expectedReturnDrift: 0 };
  }
}

/**
 * Compare live vs backtest with market regime context.
 * Adjusts drift scoring based on detected market conditions.
 */
export function compareRegimeAware(
  req: LiveBacktestCompareRequest,
  decayConfig: DecayAlertConfig = DEFAULT_DECAY_ALERT_CONFIG,
): RegimeAwareCompareResult {
  const regime = detectMarketRegime(req.liveEquity, req.backtestResult);
  const comparison = compareLiveVsBacktest(req, decayConfig);
  const adjustment = getRegimeAdjustment(regime);

  // Adjust drift score: reduce score in difficult market regimes
  const rawDrift = comparison.drift.score;
  const adjustedDriftScore = Math.round(
    Math.max(0, Math.min(100, rawDrift * adjustment.driftMultiplier)),
  );

  // Attribute how much drift is regime vs strategy
  const expectedDrift = adjustment.expectedReturnDrift;
  const actualReturnDrift = Math.abs(comparison.returnComparison.returnDiff);
  const regimeExplainedPct = actualReturnDrift > 0
    ? Math.min(100, (expectedDrift / actualReturnDrift) * 100)
    : 0;

  const regimeObservations: string[] = [];
  const regimeLabels: Record<MarketRegime, string> = {
    bull: "牛市",
    bear: "熊市",
    sideways: "震荡市",
    high_volatility: "高波动市",
    low_volatility: "低波动市",
  };
  regimeObservations.push(`检测到市场处于 ${regimeLabels[regime]} 环境`);

  if (regime === "bear" || regime === "high_volatility") {
    regimeObservations.push(
      `在${regimeLabels[regime]}中，策略表现回撤属于预期范围，` +
      `原始漂移得分 ${rawDrift} 调整为 ${adjustedDriftScore}`,
    );
  }

  if (regimeExplainedPct > 50) {
    regimeObservations.push(
      `收益偏差中约 ${regimeExplainedPct.toFixed(0)}% 可归因于市场环境变化，非策略本身衰减`,
    );
  } else if (regimeExplainedPct < 20) {
    regimeObservations.push("大部分漂移来自策略自身，建议审查策略参数");
  }

  return {
    strategyId: req.strategyId,
    period: { start: req.startDate, end: req.endDate },
    regime,
    comparison,
    adjustedDriftScore,
    regimeAttribution: {
      regimeExplainedPct: Math.round(regimeExplainedPct),
      strategySpecificPct: Math.round(100 - regimeExplainedPct),
    },
    regimeObservations,
  };
}

// ── Position-weighted comparison ───────────────────────────

/**
 * Compare live vs backtest with position-size weighting.
 * Drift from larger positions contributes more to the weighted score.
 */
export function comparePositionWeighted(
  req: LiveBacktestCompareRequest,
): PositionWeightedCompareResult {
  const { strategyId, startDate, endDate, backtestResult, liveTrades } = req;

  // Group live trades by symbol and compute capital per symbol
  const symbolCapital = new Map<string, number>();
  let totalCapital = 0;
  for (const trade of liveTrades) {
    const capital = trade.entryPrice * trade.quantity;
    symbolCapital.set(trade.symbol, (symbolCapital.get(trade.symbol) ?? 0) + capital);
    totalCapital += capital;
  }

  // Also include symbols from backtest signals that may not be in live trades
  for (const sig of backtestResult.signals) {
    if (!symbolCapital.has(sig.symbol)) {
      symbolCapital.set(sig.symbol, 0);
    }
  }

  // Compute per-symbol weights and returns
  const btTotalPnl = backtestResult.finalValue - backtestResult.initialCash;
  const btSignalsBySymbol = new Map<string, number>();
  let totalBtSignals = 0;
  for (const s of backtestResult.signals) {
    const d = new Date(s.timestamp).toISOString().slice(0, 10);
    if (d >= startDate && d <= endDate) {
      btSignalsBySymbol.set(s.symbol, (btSignalsBySymbol.get(s.symbol) ?? 0) + 1);
      totalBtSignals++;
    }
  }

  const symbolWeights: PositionWeightedCompareResult["symbolWeights"] = [];
  let weightedLiveReturn = 0;
  let weightedBtReturn = 0;
  let weightedDriftScore = 0;

  for (const [symbol, capital] of symbolCapital) {
    const weight = totalCapital > 0 ? capital / totalCapital : 1 / symbolCapital.size;

    // Live return for this symbol
    const symTrades = liveTrades.filter((t) => t.symbol === symbol && t.exitPrice != null);
    const livePnl = symTrades.reduce((a, t) => a + t.netPnl, 0);
    const liveReturn = capital > 0 ? (livePnl / capital) * 100 : 0;

    // Backtest return estimation (proportional)
    const symBtSignals = btSignalsBySymbol.get(symbol) ?? 0;
    const signalShare = totalBtSignals > 0 ? symBtSignals / totalBtSignals : 0;
    const btPnl = signalShare * btTotalPnl;
    const btReturn = backtestResult.initialCash > 0
      ? (btPnl / backtestResult.initialCash) * 100
      : 0;

    const returnDiff = Math.abs(liveReturn - btReturn);
    const symbolDrift = Math.min(100, returnDiff * 2);

    weightedLiveReturn += liveReturn * weight;
    weightedBtReturn += btReturn * weight;
    weightedDriftScore += symbolDrift * weight;

    symbolWeights.push({
      symbol,
      capitalWeight: Math.round(weight * 10000) / 100,
      weightedDriftContribution: Math.round(symbolDrift * weight * 100) / 100,
      liveReturn: Math.round(liveReturn * 100) / 100,
      backtestReturn: Math.round(btReturn * 100) / 100,
    });
  }

  // Sort by weighted drift contribution descending
  symbolWeights.sort((a, b) => b.weightedDriftContribution - a.weightedDriftContribution);

  const observations: string[] = [];
  if (symbolWeights.length > 0 && symbolWeights[0]!.weightedDriftContribution > weightedDriftScore * 0.5) {
    observations.push(
      `标的 ${symbolWeights[0]!.symbol} 贡献了超过50%的加权漂移，` +
      `资金占比 ${symbolWeights[0]!.capitalWeight.toFixed(1)}%`,
    );
  }

  const equalWeightDrift = symbolWeights.length > 0
    ? symbolWeights.reduce((a, s) => a + s.weightedDriftContribution, 0) / symbolWeights.length * symbolWeights.length
    : 0;
  if (Math.abs(weightedDriftScore - equalWeightDrift) > 5) {
    observations.push(
      `加权漂移 (${weightedDriftScore.toFixed(0)}) 与等权漂移差异明显，` +
      "重仓标的对总漂移影响较大",
    );
  }

  if (observations.length === 0) {
    observations.push("各标的仓位对漂移贡献较均衡");
  }

  return {
    strategyId,
    period: { start: startDate, end: endDate },
    weightedReturnComparison: {
      backtestWeightedReturn: Math.round(weightedBtReturn * 100) / 100,
      liveWeightedReturn: Math.round(weightedLiveReturn * 100) / 100,
      weightedReturnDiff: Math.round((weightedLiveReturn - weightedBtReturn) * 100) / 100,
    },
    weightedDriftScore: Math.round(weightedDriftScore),
    symbolWeights,
    observations,
  };
}

// ── Scheduled drift monitoring ──────────────────────────────

/**
 * Run a batch drift check across multiple strategies.
 * Designed to be called periodically (daily/weekly) by a scheduler.
 */
export function runScheduledDriftCheck(
  strategies: Array<{
    strategyId: string;
    backtestResult: BacktestResult;
    liveEquity: EquitySnapshot[];
    liveTrades: TradeRecord[];
    liveSignals: TradingSignal[];
  }>,
  config: ScheduledDriftCheckConfig = DEFAULT_SCHEDULED_DRIFT_CONFIG,
  decayConfig: DecayAlertConfig = DEFAULT_DECAY_ALERT_CONFIG,
): ScheduledDriftCheckResult {
  const checkTime = new Date().toISOString();
  const strategyResults: ScheduledDriftCheckResult["strategyResults"] = [];
  const alertedStrategies: string[] = [];

  const now = new Date();
  const windowEnd = now.toISOString().slice(0, 10);
  const windowStart = new Date(now.getTime() - config.windowDays * 86_400_000)
    .toISOString()
    .slice(0, 10);

  for (const entry of strategies) {
    // Filter data to the rolling window
    const windowEquity = entry.liveEquity.filter(
      (s) => s.date >= windowStart && s.date <= windowEnd,
    );
    const windowTrades = entry.liveTrades.filter(
      (t) => t.entryTime >= windowStart && t.entryTime <= windowEnd + "T23:59:59",
    );
    const windowSignals = entry.liveSignals.filter((s) => {
      const d = new Date(s.timestamp).toISOString().slice(0, 10);
      return d >= windowStart && d <= windowEnd;
    });

    if (windowEquity.length < 2) {
      strategyResults.push({
        strategyId: entry.strategyId,
        driftScore: 0,
        severity: "low",
        returnDiff: 0,
        alertTriggered: false,
        observations: ["监控窗口内数据不足，跳过本次检查"],
      });
      continue;
    }

    const req: LiveBacktestCompareRequest = {
      strategyId: entry.strategyId,
      startDate: windowStart,
      endDate: windowEnd,
      backtestResult: entry.backtestResult,
      liveEquity: windowEquity,
      liveTrades: windowTrades,
      liveSignals: windowSignals,
    };

    const result = compareLiveVsBacktest(req, decayConfig);
    const alertTriggered = result.drift.score >= config.alertThreshold;

    if (alertTriggered) {
      alertedStrategies.push(entry.strategyId);
    }

    strategyResults.push({
      strategyId: entry.strategyId,
      driftScore: result.drift.score,
      severity: result.drift.severity,
      returnDiff: result.returnComparison.returnDiff,
      alertTriggered,
      observations: result.drift.observations,
    });
  }

  // Determine portfolio health
  let portfolioHealth: ScheduledDriftCheckResult["portfolioHealth"] = "healthy";
  const alertRatio = strategies.length > 0 ? alertedStrategies.length / strategies.length : 0;
  const hasCritical = strategyResults.some((r) => r.severity === "critical");
  if (hasCritical || alertRatio > 0.5) {
    portfolioHealth = "critical";
  } else if (alertedStrategies.length > 0) {
    portfolioHealth = "warning";
  }

  return {
    checkTime,
    strategyResults,
    alertedStrategies,
    portfolioHealth,
  };
}

// ── Portfolio-level comparison ─────────────────────────────────

export interface PortfolioCompareResult {
  /** Comparison period */
  period: { start: string; end: string };
  /** Blended backtest return (weighted by each strategy's capital allocation) */
  blendedBacktestReturn: number;
  /** Actual live portfolio return */
  livePortfolioReturn: number;
  /** Return difference: live - blended backtest */
  returnDiff: number;
  /** Portfolio drift score (0-100) */
  portfolioDriftScore: number;
  portfolioDriftSeverity: DriftSeverity;
  /** Per-strategy contribution to overall drift */
  strategyContributions: Array<{
    strategyId: string;
    /** Capital weight of this strategy in the portfolio (0-1) */
    capitalWeight: number;
    backtestReturn: number;
    liveReturn: number;
    returnDiff: number;
    driftScore: number;
    /** Weighted contribution to portfolio drift */
    weightedDriftContribution: number;
  }>;
  /** Correlation between blended backtest equity and live equity */
  curveCorrelation: number;
  /** Diversification benefit: is the combined portfolio drift lower than average individual drift? */
  diversificationBenefit: number;
  observations: string[];
}

/**
 * Compare a multi-strategy live portfolio against the blended backtest of all active strategies.
 * Each strategy is weighted by its capital allocation to produce a portfolio-level comparison.
 */
export function comparePortfolio(
  strategies: Array<{
    strategyId: string;
    backtestResult: BacktestResult;
    liveEquity: EquitySnapshot[];
    liveTrades: TradeRecord[];
    liveSignals: TradingSignal[];
    /** Capital allocated to this strategy (absolute value, e.g. 100000) */
    capitalAllocation: number;
  }>,
  portfolioLiveEquity: EquitySnapshot[],
  startDate: string,
  endDate: string,
): PortfolioCompareResult {
  const totalCapital = strategies.reduce((a, s) => a + s.capitalAllocation, 0);

  // Per-strategy comparison + capital weighting
  const strategyContributions: PortfolioCompareResult["strategyContributions"] = [];
  let blendedBtReturn = 0;
  let totalWeightedDrift = 0;
  let sumIndividualDrift = 0;

  for (const entry of strategies) {
    const weight = totalCapital > 0 ? entry.capitalAllocation / totalCapital : 1 / strategies.length;

    const req: LiveBacktestCompareRequest = {
      strategyId: entry.strategyId,
      startDate,
      endDate,
      backtestResult: entry.backtestResult,
      liveEquity: entry.liveEquity,
      liveTrades: entry.liveTrades,
      liveSignals: entry.liveSignals,
    };

    const result = compareLiveVsBacktest(req);
    const btReturn = result.returnComparison.backtestReturn;
    const liveReturn = result.returnComparison.liveReturn;
    const returnDiff = liveReturn - btReturn;
    const driftScore = result.drift.score;

    blendedBtReturn += btReturn * weight;
    totalWeightedDrift += driftScore * weight;
    sumIndividualDrift += driftScore;

    strategyContributions.push({
      strategyId: entry.strategyId,
      capitalWeight: Math.round(weight * 10000) / 10000,
      backtestReturn: Math.round(btReturn * 100) / 100,
      liveReturn: Math.round(liveReturn * 100) / 100,
      returnDiff: Math.round(returnDiff * 100) / 100,
      driftScore,
      weightedDriftContribution: Math.round(driftScore * weight * 100) / 100,
    });
  }

  // Sort by weighted drift contribution descending
  strategyContributions.sort((a, b) => b.weightedDriftContribution - a.weightedDriftContribution);

  // Live portfolio return from actual portfolio equity
  const sortedPortfolio = [...portfolioLiveEquity].sort((a, b) => a.date.localeCompare(b.date));
  let livePortfolioReturn = 0;
  if (sortedPortfolio.length >= 2) {
    const first = sortedPortfolio[0]!;
    const last = sortedPortfolio[sortedPortfolio.length - 1]!;
    if (first.equity > 0) {
      livePortfolioReturn = ((last.equity - first.equity) / first.equity) * 100;
    }
  }

  const returnDiff = livePortfolioReturn - blendedBtReturn;
  const portfolioDriftScore = Math.round(totalWeightedDrift);

  let portfolioDriftSeverity: DriftSeverity;
  if (portfolioDriftScore <= 15) portfolioDriftSeverity = "low";
  else if (portfolioDriftScore <= 35) portfolioDriftSeverity = "moderate";
  else if (portfolioDriftScore <= 60) portfolioDriftSeverity = "high";
  else portfolioDriftSeverity = "critical";

  // Correlation between blended backtest equity and live equity
  // Build blended backtest equity curve from per-strategy curves
  const blendedDates = new Map<string, { bt: number; live: number }>();
  for (const entry of strategies) {
    const weight = totalCapital > 0 ? entry.capitalAllocation / totalCapital : 1 / strategies.length;
    for (const pt of entry.backtestResult.equityCurve) {
      const existing = blendedDates.get(pt.date) ?? { bt: 0, live: 0 };
      const normalizedBt = entry.backtestResult.initialCash > 0
        ? (pt.value / entry.backtestResult.initialCash) * 100
        : 100;
      existing.bt += normalizedBt * weight;
      blendedDates.set(pt.date, existing);
    }
  }
  for (const snap of sortedPortfolio) {
    const existing = blendedDates.get(snap.date);
    if (existing) {
      const liveInitial = sortedPortfolio[0]!.equity;
      existing.live = liveInitial > 0 ? (snap.equity / liveInitial) * 100 : 100;
    }
  }

  const overlapping = [...blendedDates.values()].filter((v) => v.bt > 0 && v.live > 0);
  let curveCorrelation = 0;
  if (overlapping.length >= 2) {
    const n = overlapping.length;
    const meanBt = overlapping.reduce((a, v) => a + v.bt, 0) / n;
    const meanLive = overlapping.reduce((a, v) => a + v.live, 0) / n;
    let covSum = 0;
    let varBt = 0;
    let varLive = 0;
    for (const v of overlapping) {
      const dBt = v.bt - meanBt;
      const dLive = v.live - meanLive;
      covSum += dBt * dLive;
      varBt += dBt * dBt;
      varLive += dLive * dLive;
    }
    const denom = Math.sqrt(varBt * varLive);
    curveCorrelation = denom > 0 ? covSum / denom : 0;
  }

  // Diversification benefit: compare portfolio drift to average individual drift
  const avgIndividualDrift = strategies.length > 0 ? sumIndividualDrift / strategies.length : 0;
  const diversificationBenefit = avgIndividualDrift > 0
    ? Math.round((1 - portfolioDriftScore / avgIndividualDrift) * 100)
    : 0;

  const observations: string[] = [];
  if (portfolioDriftSeverity === "low") {
    observations.push("组合整体漂移较低，各策略实盘表现与回测基本一致");
  } else if (portfolioDriftSeverity === "critical") {
    observations.push("组合漂移严重，建议全面审查各策略运行状态");
  }

  if (strategyContributions.length > 0) {
    const worst = strategyContributions[0]!;
    if (worst.weightedDriftContribution > portfolioDriftScore * 0.5) {
      observations.push(
        `策略 ${worst.strategyId} 贡献了超过50%的组合漂移 (资金占比 ${(worst.capitalWeight * 100).toFixed(1)}%)`,
      );
    }
  }

  if (diversificationBenefit > 10) {
    observations.push(`多策略分散化降低了约 ${diversificationBenefit}% 的漂移风险`);
  } else if (diversificationBenefit < -10) {
    observations.push("多策略组合未能有效分散漂移风险，各策略漂移方向一致");
  }

  return {
    period: { start: startDate, end: endDate },
    blendedBacktestReturn: Math.round(blendedBtReturn * 100) / 100,
    livePortfolioReturn: Math.round(livePortfolioReturn * 100) / 100,
    returnDiff: Math.round(returnDiff * 100) / 100,
    portfolioDriftScore,
    portfolioDriftSeverity,
    strategyContributions,
    curveCorrelation: Math.round(curveCorrelation * 10000) / 10000,
    diversificationBenefit,
    observations,
  };
}

// ── Re-optimization recommendation ───────────────────────────

export type ReoptimizeUrgency = "none" | "monitor" | "recommended" | "urgent";

export interface ReoptimizeCheckResult {
  strategyId: string;
  period: { start: string; end: string };
  /** Overall urgency for re-optimization */
  urgency: ReoptimizeUrgency;
  /** Score 0-100 indicating how strongly re-optimization is recommended */
  reoptimizeScore: number;
  /** Individual signals contributing to the recommendation */
  signals: Array<{
    signal: string;
    severity: "info" | "warning" | "critical";
    description: string;
  }>;
  /** Suggested actions */
  recommendations: string[];
  /** Key metrics that triggered the recommendation */
  metrics: {
    driftScore: number;
    driftTrend: DriftHistory["trend"];
    returnDecay: number;
    sharpeDecay: number;
    oosVerdict: OutOfSampleResult["verdict"] | null;
    regimeChanged: boolean;
  };
}

/**
 * Analyze whether a strategy needs re-optimization based on multiple degradation signals.
 * Combines drift history, out-of-sample performance, and regime analysis.
 */
export function checkReoptimizeNeed(
  req: LiveBacktestCompareRequest,
  /** Additional live equity data that extends beyond the backtest period */
  postBacktestEquity: EquitySnapshot[],
  postBacktestTrades: TradeRecord[],
  decayConfig: DecayAlertConfig = DEFAULT_DECAY_ALERT_CONFIG,
): ReoptimizeCheckResult {
  const { strategyId, startDate, endDate } = req;

  const signals: ReoptimizeCheckResult["signals"] = [];
  const recommendations: string[] = [];
  let reoptimizeScore = 0;

  // 1. Current drift analysis
  const comparison = compareLiveVsBacktest(req, decayConfig);
  const driftScore = comparison.drift.score;

  if (driftScore > 60) {
    signals.push({ signal: "critical_drift", severity: "critical", description: `当前漂移得分 ${driftScore}/100，策略严重偏离回测预期` });
    reoptimizeScore += 30;
  } else if (driftScore > 35) {
    signals.push({ signal: "high_drift", severity: "warning", description: `当前漂移得分 ${driftScore}/100，策略偏离回测预期` });
    reoptimizeScore += 15;
  }

  // 2. Drift trend analysis
  const history = computeDriftHistory(req, 30, 7);
  const driftTrend = history.trend;

  if (driftTrend === "worsening") {
    signals.push({ signal: "worsening_trend", severity: "warning", description: "漂移趋势持续恶化，策略可能正在失效" });
    reoptimizeScore += 20;
  }

  if (history.alertCount >= 3) {
    signals.push({ signal: "repeated_alerts", severity: "warning", description: `近期累计 ${history.alertCount} 次高风险告警` });
    reoptimizeScore += 10;
  }

  // 3. Out-of-sample check (if post-backtest data available)
  let oosVerdict: OutOfSampleResult["verdict"] | null = null;
  let returnDecay = 0;
  let sharpeDecay = 0;

  if (postBacktestEquity.length >= 2) {
    const oos = compareOutOfSample(req.backtestResult, postBacktestEquity, postBacktestTrades);
    oosVerdict = oos.verdict;
    returnDecay = oos.returnDecay;
    sharpeDecay = oos.sharpeDecay;

    if (oos.verdict === "failed") {
      signals.push({ signal: "oos_failed", severity: "critical", description: `样本外验证失败，年化收益衰减 ${returnDecay.toFixed(1)}%` });
      reoptimizeScore += 25;
    } else if (oos.verdict === "degrading") {
      signals.push({ signal: "oos_degrading", severity: "warning", description: `样本外表现衰减，年化收益下降 ${returnDecay.toFixed(1)}%` });
      reoptimizeScore += 15;
    }
  }

  // 4. Regime change detection
  const regimeResult = compareRegimeAware(req, decayConfig);
  const regimeChanged = regimeResult.regimeAttribution.strategySpecificPct > 60;

  if (regimeChanged && driftScore > 20) {
    signals.push({
      signal: "strategy_specific_drift",
      severity: "warning",
      description: `漂移主要来自策略自身 (${regimeResult.regimeAttribution.strategySpecificPct}%)，非市场环境变化`,
    });
    reoptimizeScore += 10;
  } else if (!regimeChanged && regimeResult.regime !== "sideways") {
    signals.push({
      signal: "regime_shift",
      severity: "info",
      description: `市场环境变化 (${regimeResult.regime}) 解释了大部分偏差，策略参数可能仍然有效`,
    });
    reoptimizeScore = Math.max(0, reoptimizeScore - 10);
  }

  // 5. Execution quality degradation
  if (comparison.executionQuality.fillRate < 0.6) {
    signals.push({ signal: "poor_fill_rate", severity: "warning", description: `信号执行率仅 ${(comparison.executionQuality.fillRate * 100).toFixed(0)}%，执行环节可能需要调整` });
    reoptimizeScore += 5;
  }

  // Cap score
  reoptimizeScore = Math.min(100, Math.max(0, reoptimizeScore));

  // Determine urgency
  let urgency: ReoptimizeUrgency;
  if (reoptimizeScore >= 60) urgency = "urgent";
  else if (reoptimizeScore >= 35) urgency = "recommended";
  else if (reoptimizeScore >= 15) urgency = "monitor";
  else urgency = "none";

  // Generate recommendations
  if (urgency === "urgent") {
    recommendations.push("建议立即暂停实盘，重新优化策略参数");
    if (oosVerdict === "failed") {
      recommendations.push("样本外验证失败，策略在当前市场条件下可能已完全失效，建议重新设计策略逻辑");
    }
  }
  if (urgency === "recommended") {
    recommendations.push("建议在下一个非交易日重新运行参数优化");
    if (driftTrend === "worsening") {
      recommendations.push("漂移趋势恶化，可考虑缩小仓位直至优化完成");
    }
  }
  if (urgency === "monitor") {
    recommendations.push("当前偏差在可控范围内，建议持续监控");
    if (comparison.executionQuality.fillRate < 0.7) {
      recommendations.push("执行率偏低，建议先检查下单通道和流动性");
    }
  }
  if (urgency === "none") {
    recommendations.push("策略运行正常，无需重新优化");
  }

  if (regimeChanged && urgency !== "none") {
    recommendations.push(`策略参数可能需要针对当前 ${regimeResult.regime} 环境进行调整`);
  }

  return {
    strategyId,
    period: { start: startDate, end: endDate },
    urgency,
    reoptimizeScore,
    signals,
    recommendations,
    metrics: {
      driftScore,
      driftTrend,
      returnDecay,
      sharpeDecay,
      oosVerdict,
      regimeChanged,
    },
  };
}

// ── Custom benchmark comparison ───────────────────────────────

export interface CustomBenchmarkResult {
  strategyId: string;
  period: { start: string; end: string };
  /** Benchmark symbol used */
  benchmarkSymbol: string;
  /** Benchmark total return (%) */
  benchmarkReturn: number;
  /** Live alpha: live return - benchmark return */
  liveAlpha: number;
  /** Backtest alpha: backtest return - benchmark return */
  backtestAlpha: number;
  /** Alpha decay: backtest alpha - live alpha */
  alphaDecay: number;
  /** Information ratio (live excess return / tracking error) */
  informationRatio: number;
  /** Tracking error (annualized std of return difference) */
  trackingError: number;
  /** Beta: sensitivity of live returns to benchmark returns */
  beta: number;
  /** Attribution */
  attribution: "outperforming" | "alpha_decay" | "market_decline" | "strategy_underperform";
  /** Equity curve overlay: normalized to 100 base */
  curves: {
    dates: string[];
    benchmarkValues: number[];
    liveValues: number[];
    backtestValues: number[];
  };
  observations: string[];
}

/**
 * Compare live and backtest performance against a user-specified benchmark.
 * Accepts pre-fetched benchmark kline data (daily closes).
 */
export function compareWithCustomBenchmark(
  req: LiveBacktestCompareRequest,
  benchmarkSymbol: string,
  benchmarkKlines: Array<{ date: string; close: number }>,
): CustomBenchmarkResult {
  const { strategyId, startDate, endDate, backtestResult, liveEquity } = req;

  // Filter benchmark data to comparison period
  const benchData = benchmarkKlines
    .filter((k) => k.date >= startDate && k.date <= endDate)
    .sort((a, b) => a.date.localeCompare(b.date));

  // Benchmark return
  let benchmarkReturn = 0;
  if (benchData.length >= 2) {
    const first = benchData[0]!.close;
    const last = benchData[benchData.length - 1]!.close;
    benchmarkReturn = first > 0 ? ((last - first) / first) * 100 : 0;
  }

  // Live return
  const sortedLive = [...liveEquity].sort((a, b) => a.date.localeCompare(b.date));
  let liveReturn = 0;
  if (sortedLive.length >= 2) {
    const first = sortedLive[0]!.equity;
    const last = sortedLive[sortedLive.length - 1]!.equity;
    liveReturn = first > 0 ? ((last - first) / first) * 100 : 0;
  }

  const liveAlpha = liveReturn - benchmarkReturn;
  const backtestAlpha = backtestResult.totalReturn - benchmarkReturn;
  const alphaDecay = backtestAlpha - liveAlpha;

  // Daily returns for tracking error, beta, information ratio
  const benchByDate = new Map(benchData.map((k) => [k.date, k.close]));
  const liveByDate = new Map(sortedLive.map((s) => [s.date, s.equity]));

  // Find overlapping dates
  const commonDates = [...benchByDate.keys()]
    .filter((d) => liveByDate.has(d))
    .sort();

  let trackingError = 0;
  let informationRatio = 0;
  let beta = 0;

  if (commonDates.length >= 5) {
    const benchReturns: number[] = [];
    const liveReturns: number[] = [];
    const excessReturns: number[] = [];

    for (let i = 1; i < commonDates.length; i++) {
      const prevDate = commonDates[i - 1]!;
      const currDate = commonDates[i]!;
      const prevBench = benchByDate.get(prevDate)!;
      const currBench = benchByDate.get(currDate)!;
      const prevLive = liveByDate.get(prevDate)!;
      const currLive = liveByDate.get(currDate)!;

      if (prevBench > 0 && prevLive > 0) {
        const br = (currBench - prevBench) / prevBench;
        const lr = (currLive - prevLive) / prevLive;
        benchReturns.push(br);
        liveReturns.push(lr);
        excessReturns.push(lr - br);
      }
    }

    if (excessReturns.length > 1) {
      // Tracking error
      const meanExcess = excessReturns.reduce((a, b) => a + b, 0) / excessReturns.length;
      const teVariance = excessReturns.reduce((a, r) => a + (r - meanExcess) ** 2, 0) / (excessReturns.length - 1);
      trackingError = Math.sqrt(teVariance) * Math.sqrt(252) * 100;

      // Information ratio
      informationRatio = trackingError > 0 ? (meanExcess * Math.sqrt(252) * 100) / trackingError : 0;

      // Beta: Cov(live, bench) / Var(bench)
      const meanBench = benchReturns.reduce((a, b) => a + b, 0) / benchReturns.length;
      const meanLive = liveReturns.reduce((a, b) => a + b, 0) / liveReturns.length;
      let covSum = 0;
      let varBench = 0;
      for (let i = 0; i < benchReturns.length; i++) {
        covSum += (liveReturns[i]! - meanLive) * (benchReturns[i]! - meanBench);
        varBench += (benchReturns[i]! - meanBench) ** 2;
      }
      beta = varBench > 0 ? covSum / varBench : 1;
    }
  }

  // Attribution
  let attribution: CustomBenchmarkResult["attribution"];
  if (liveAlpha >= 0) {
    attribution = "outperforming";
  } else if (alphaDecay > 3 && benchmarkReturn >= -2) {
    attribution = "alpha_decay";
  } else if (benchmarkReturn < -5) {
    attribution = "market_decline";
  } else {
    attribution = "strategy_underperform";
  }

  // Build equity curve overlay
  const dates: string[] = [];
  const benchmarkValues: number[] = [];
  const liveValues: number[] = [];
  const backtestValues: number[] = [];

  const benchInitial = benchData.length > 0 ? benchData[0]!.close : 1;
  const liveInitial = sortedLive.length > 0 ? sortedLive[0]!.equity : 1;
  const btInitial = backtestResult.initialCash;
  const btByDate = new Map(backtestResult.equityCurve.map((p) => [p.date, p.value]));

  const allDates = new Set([
    ...benchData.map((k) => k.date),
    ...sortedLive.map((s) => s.date),
    ...backtestResult.equityCurve.map((p) => p.date),
  ]);

  for (const date of [...allDates].sort()) {
    dates.push(date);
    const bv = benchByDate.get(date);
    benchmarkValues.push(bv != null && benchInitial > 0 ? (bv / benchInitial) * 100 : 0);
    const lv = liveByDate.get(date);
    liveValues.push(lv != null && liveInitial > 0 ? (lv / liveInitial) * 100 : 0);
    const btv = btByDate.get(date);
    backtestValues.push(btv != null && btInitial > 0 ? (btv / btInitial) * 100 : 0);
  }

  // Observations
  const observations: string[] = [];
  if (liveAlpha > 0) {
    observations.push(`实盘跑赢 ${benchmarkSymbol} 基准 ${liveAlpha.toFixed(2)}%`);
  } else {
    observations.push(`实盘跑输 ${benchmarkSymbol} 基准 ${Math.abs(liveAlpha).toFixed(2)}%`);
  }

  if (alphaDecay > 5) {
    observations.push(`Alpha衰减 ${alphaDecay.toFixed(2)}%：回测时跑赢基准 ${backtestAlpha.toFixed(2)}%，实盘仅 ${liveAlpha.toFixed(2)}%`);
  }

  if (Math.abs(beta) > 1.3) {
    observations.push(`Beta ${beta.toFixed(2)} 偏高，策略对市场波动敏感度超出预期`);
  } else if (Math.abs(beta) < 0.5) {
    observations.push(`Beta ${beta.toFixed(2)} 偏低，策略与基准走势相关性较弱`);
  }

  if (trackingError > 20) {
    observations.push(`跟踪误差 ${trackingError.toFixed(2)}% 较大，策略偏离基准幅度显著`);
  }

  return {
    strategyId,
    period: { start: startDate, end: endDate },
    benchmarkSymbol,
    benchmarkReturn: Math.round(benchmarkReturn * 100) / 100,
    liveAlpha: Math.round(liveAlpha * 100) / 100,
    backtestAlpha: Math.round(backtestAlpha * 100) / 100,
    alphaDecay: Math.round(alphaDecay * 100) / 100,
    informationRatio: Math.round(informationRatio * 100) / 100,
    trackingError: Math.round(trackingError * 100) / 100,
    beta: Math.round(beta * 100) / 100,
    attribution,
    curves: { dates, benchmarkValues, liveValues, backtestValues },
    observations,
  };
}
