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

// ── Implementation ──────────────────────────────────────────

/**
 * Compare live trading performance against a backtest result.
 */
export function compareLiveVsBacktest(
  req: LiveBacktestCompareRequest,
): LiveBacktestCompareResult {
  const { strategyId, startDate, endDate, backtestResult, liveEquity, liveTrades, liveSignals } =
    req;

  const returnComp = computeReturnComparison(backtestResult, liveEquity);
  const drawdownComp = computeDrawdownComparison(backtestResult, liveEquity);
  const riskComp = computeRiskComparison(backtestResult, liveEquity, liveTrades);
  const execQuality = computeExecutionQuality(backtestResult, liveTrades, liveSignals);
  const overlay = buildEquityCurveOverlay(backtestResult, liveEquity);
  const drift = computeDrift(returnComp, drawdownComp, riskComp, execQuality);

  const result: LiveBacktestCompareResult = {
    strategyId,
    period: { start: startDate, end: endDate },
    returnComparison: returnComp,
    drawdownComparison: drawdownComp,
    riskComparison: riskComp,
    executionQuality: execQuality,
    equityCurveOverlay: overlay,
    drift,
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
    // tax = grossPnl - netPnl - commission (when grossPnl > netPnl + commission)
    const impliedTax = t.grossPnl - t.netPnl - t.commission;
    if (impliedTax > 0) totalTax += impliedTax;
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

    // Estimate backtest return proportionally from equity curve is not possible per-symbol,
    // so use signal-based heuristic
    const backtestReturn = 0; // Not available per-symbol from aggregate backtest
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
