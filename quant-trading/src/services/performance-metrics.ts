/**
 * PerformanceMetrics — core performance indicator calculation engine.
 *
 * Computes comprehensive trading performance metrics from trade records
 * and equity history snapshots:
 *
 *   - Return metrics: total return, annualized return, monthly/weekly returns
 *   - Risk metrics: annualized volatility, max drawdown, drawdown duration,
 *                   VaR (95%), downside deviation
 *   - Risk-adjusted: Sharpe ratio, Sortino ratio, Calmar ratio, recovery factor
 *   - Trade analysis: win rate, profit factor (payoff ratio), expectancy,
 *                     avg holding period, consecutive win/loss streaks,
 *                     Kelly criterion, trade frequency
 *   - Capital efficiency: capital utilization rate
 *
 * Designed to work with both live trading data (TradeRecord + EquitySnapshot)
 * and backtest results (BacktestResult).
 */
import type { TradeRecord, BacktestResult } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Result Types ────────────────────────────────────────────────

export interface ReturnMetrics {
  /** Total cumulative return (decimal, e.g. 0.15 = 15%) */
  totalReturn: number;
  /** Compound annual growth rate */
  annualizedReturn: number;
  /** Annualized volatility (std dev of daily returns × √252) */
  annualizedVolatility: number;
  /** Monthly return series */
  monthlyReturns: Array<{ month: string; return: number }>;
  /** Weekly return series */
  weeklyReturns: Array<{ week: string; return: number }>;
}

export interface DrawdownMetrics {
  /** Maximum peak-to-trough drawdown (decimal, e.g. 0.10 = 10%) */
  maxDrawdown: number;
  /** Duration of the longest drawdown period in trading days */
  maxDrawdownDuration: number;
  /** Start date of the max drawdown period */
  maxDrawdownStart: string;
  /** End date (trough) of the max drawdown period */
  maxDrawdownEnd: string;
  /** Current drawdown from peak (decimal) */
  currentDrawdown: number;
}

export interface RiskAdjustedMetrics {
  /** Annualized Sharpe ratio (excess return / volatility) */
  sharpeRatio: number;
  /** Annualized Sortino ratio (excess return / downside deviation) */
  sortinoRatio: number;
  /** Calmar ratio (annualized return / max drawdown) */
  calmarRatio: number;
  /** Recovery factor (total return / max drawdown) */
  recoveryFactor: number;
  /** Historical VaR at 95% confidence (daily, as positive number) */
  var95: number;
}

export interface TradeAnalysisMetrics {
  /** Total number of completed (closed) trades */
  totalTrades: number;
  /** Number of winning trades */
  winningTrades: number;
  /** Number of losing trades */
  losingTrades: number;
  /** Win rate (decimal, e.g. 0.6 = 60%) */
  winRate: number;
  /** Profit factor: total gross wins / total gross losses */
  profitFactor: number;
  /** Payoff ratio: average win size / average loss size */
  payoffRatio: number;
  /** Mathematical expectancy per trade (avg win × winRate - avg loss × lossRate) */
  expectancy: number;
  /** Average profit/loss per trade in currency */
  avgTradeNetPnl: number;
  /** Average winning trade P&L */
  avgWin: number;
  /** Average losing trade P&L */
  avgLoss: number;
  /** Largest single winning trade */
  largestWin: number;
  /** Largest single losing trade (negative number) */
  largestLoss: number;
  /** Average holding period across all trades in calendar days */
  avgHoldingDays: number;
  /** Maximum consecutive winning trades */
  maxConsecutiveWins: number;
  /** Maximum consecutive losing trades */
  maxConsecutiveLosses: number;
  /** Kelly criterion optimal fraction (win_rate - (1 - win_rate) / payoff_ratio) */
  kellyCriterion: number;
  /** Total commission paid */
  totalCommission: number;
}

export interface TradeFrequencyMetrics {
  /** Average number of trades per trading day (over active period) */
  tradesPerDay: number;
  /** Average number of trades per week */
  tradesPerWeek: number;
  /** Average number of trades per month */
  tradesPerMonth: number;
  /** Number of active trading days (days with at least one trade) */
  activeTradingDays: number;
}

export interface CapitalMetrics {
  /** Initial capital */
  initialCapital: number;
  /** Final portfolio value */
  finalValue: number;
  /** Net profit/loss in currency */
  netProfit: number;
  /** Average capital utilization rate (avg invested / total equity, decimal) */
  avgCapitalUtilization: number;
}

export interface PerformanceReport {
  /** Date range of the analysis */
  dateRange: { from: string; to: string };
  /** Number of calendar days in the period */
  periodDays: number;

  returns: ReturnMetrics;
  drawdown: DrawdownMetrics;
  riskAdjusted: RiskAdjustedMetrics;
  tradeAnalysis: TradeAnalysisMetrics;
  tradeFrequency: TradeFrequencyMetrics;
  capital: CapitalMetrics;
}

// ── Configuration ───────────────────────────────────────────────

export interface PerformanceMetricsConfig {
  /** Annual risk-free rate for Sharpe/Sortino calculation (default: 0.03) */
  riskFreeRate?: number;
  /** Trading days per year (default: 252 for A-shares) */
  tradingDaysPerYear?: number;
}

const DEFAULTS: Required<PerformanceMetricsConfig> = {
  riskFreeRate: 0.03,
  tradingDaysPerYear: 252,
};

// ── Core Computation ────────────────────────────────────────────

/**
 * Compute full performance report from equity snapshots and trade records.
 */
export function computePerformanceReport(
  equityHistory: EquitySnapshot[],
  trades: TradeRecord[],
  config?: PerformanceMetricsConfig,
): PerformanceReport {
  const cfg = { ...DEFAULTS, ...config };

  // Sort equity history by date
  const sorted = [...equityHistory].sort((a, b) => a.date.localeCompare(b.date));
  // Only closed trades
  const closedTrades = trades.filter((t) => t.exitTime != null);

  // Date range
  const fromDate = sorted.length > 0 ? sorted[0]!.date : "";
  const toDate = sorted.length > 0 ? sorted[sorted.length - 1]!.date : "";
  const periodDays = fromDate && toDate ? daysBetween(fromDate, toDate) : 0;

  const returns = computeReturnMetrics(sorted, cfg);
  const drawdown = computeDrawdownMetrics(sorted);
  const riskAdjusted = computeRiskAdjustedMetrics(sorted, drawdown, returns, cfg);
  const tradeAnalysis = computeTradeAnalysis(closedTrades);
  const tradeFrequency = computeTradeFrequency(closedTrades, periodDays);
  const capital = computeCapitalMetrics(sorted, closedTrades);

  return {
    dateRange: { from: fromDate, to: toDate },
    periodDays,
    returns,
    drawdown,
    riskAdjusted,
    tradeAnalysis,
    tradeFrequency,
    capital,
  };
}

/**
 * Compute performance report from a BacktestResult (convenience wrapper).
 */
export function computeFromBacktestResult(
  result: BacktestResult,
  config?: PerformanceMetricsConfig,
): PerformanceReport {
  // Convert backtest equity curve to EquitySnapshots
  const equitySnapshots: EquitySnapshot[] = (result.equityCurve ?? []).map((p) => ({
    date: p.date,
    equity: p.value,
    cash: 0,
    marketValue: p.value,
    dailyPnl: 0,
    positionCount: 0,
  }));

  // Fill in dailyPnl from sequential differences
  for (let i = 1; i < equitySnapshots.length; i++) {
    equitySnapshots[i]!.dailyPnl = equitySnapshots[i]!.equity - equitySnapshots[i - 1]!.equity;
  }

  // Convert signals to minimal TradeRecords
  const trades: TradeRecord[] = (result.signals ?? [])
    .filter((s) => s.action === "sell")
    .map((s, idx) => ({
      tradeId: `bt-${idx}`,
      symbol: s.symbol,
      market: "cn-a" as const,
      side: "sell" as const,
      entryTime: result.startDate,
      exitTime: s.date,
      entryPrice: s.price,
      exitPrice: s.price,
      quantity: s.quantity ?? 0,
      grossPnl: 0,
      commission: 0,
      netPnl: 0,
      holdingDays: undefined,
      strategyId: result.strategyId,
      entryOrderId: `bt-entry-${idx}`,
      exitOrderId: `bt-exit-${idx}`,
    }));

  return computePerformanceReport(equitySnapshots, trades, config);
}

// ── Return Metrics ──────────────────────────────────────────────

function computeReturnMetrics(
  snapshots: EquitySnapshot[],
  cfg: Required<PerformanceMetricsConfig>,
): ReturnMetrics {
  if (snapshots.length < 2) {
    return {
      totalReturn: 0,
      annualizedReturn: 0,
      annualizedVolatility: 0,
      monthlyReturns: [],
      weeklyReturns: [],
    };
  }

  const first = snapshots[0]!;
  const last = snapshots[snapshots.length - 1]!;
  const totalReturn = first.equity > 0 ? last.equity / first.equity - 1 : 0;

  const tradingDays = snapshots.length - 1;
  const years = tradingDays / cfg.tradingDaysPerYear;
  const annualizedReturn =
    years > 0 && totalReturn > -1 ? Math.pow(1 + totalReturn, 1 / years) - 1 : 0;

  // Daily returns
  const dailyReturns = calcDailyReturns(snapshots);
  const annualizedVolatility = stdDev(dailyReturns) * Math.sqrt(cfg.tradingDaysPerYear);

  // Monthly returns
  const monthlyReturns = calcPeriodicReturns(snapshots, (d) => d.slice(0, 7));

  // Weekly returns (ISO week)
  const weeklyReturns = calcPeriodicReturns(snapshots, isoWeek).map((r) => ({
    week: r.period,
    return: r.return,
  }));

  return {
    totalReturn: round4(totalReturn),
    annualizedReturn: round4(annualizedReturn),
    annualizedVolatility: round4(annualizedVolatility),
    monthlyReturns: monthlyReturns.map((r) => ({ month: r.period, return: r.return })),
    weeklyReturns,
  };
}

// ── Drawdown Metrics ────────────────────────────────────────────

function computeDrawdownMetrics(snapshots: EquitySnapshot[]): DrawdownMetrics {
  if (snapshots.length < 2) {
    return {
      maxDrawdown: 0,
      maxDrawdownDuration: 0,
      maxDrawdownStart: "",
      maxDrawdownEnd: "",
      currentDrawdown: 0,
    };
  }

  let peak = snapshots[0]!.equity;
  let peakDate = snapshots[0]!.date;
  let maxDd = 0;
  let maxDdStart = "";
  let maxDdEnd = "";

  // For duration tracking
  let inDrawdown = false;
  let drawdownStartIdx = 0;
  let maxDuration = 0;

  for (let i = 0; i < snapshots.length; i++) {
    const s = snapshots[i]!;
    if (s.equity >= peak) {
      if (inDrawdown) {
        const duration = i - drawdownStartIdx;
        if (duration > maxDuration) maxDuration = duration;
        inDrawdown = false;
      }
      peak = s.equity;
      peakDate = s.date;
    } else {
      if (!inDrawdown) {
        inDrawdown = true;
        drawdownStartIdx = i - 1;
      }
      const dd = peak > 0 ? (peak - s.equity) / peak : 0;
      if (dd > maxDd) {
        maxDd = dd;
        maxDdStart = peakDate;
        maxDdEnd = s.date;
      }
    }
  }

  // Handle ongoing drawdown
  if (inDrawdown) {
    const duration = snapshots.length - 1 - drawdownStartIdx;
    if (duration > maxDuration) maxDuration = duration;
  }

  // Current drawdown
  const lastEquity = snapshots[snapshots.length - 1]!.equity;
  const currentDd = peak > 0 ? (peak - lastEquity) / peak : 0;

  return {
    maxDrawdown: round4(maxDd),
    maxDrawdownDuration: maxDuration,
    maxDrawdownStart: maxDdStart,
    maxDrawdownEnd: maxDdEnd,
    currentDrawdown: round4(Math.max(currentDd, 0)),
  };
}

// ── Risk-Adjusted Metrics ───────────────────────────────────────

function computeRiskAdjustedMetrics(
  snapshots: EquitySnapshot[],
  drawdown: DrawdownMetrics,
  returns: ReturnMetrics,
  cfg: Required<PerformanceMetricsConfig>,
): RiskAdjustedMetrics {
  const dailyReturns = calcDailyReturns(snapshots);

  const sharpeRatio = calcSharpe(dailyReturns, cfg.riskFreeRate, cfg.tradingDaysPerYear);
  const sortinoRatio = calcSortino(dailyReturns, cfg.riskFreeRate, cfg.tradingDaysPerYear);

  const calmarRatio =
    drawdown.maxDrawdown > 0 ? returns.annualizedReturn / drawdown.maxDrawdown : 0;

  const recoveryFactor =
    drawdown.maxDrawdown > 0 ? returns.totalReturn / drawdown.maxDrawdown : 0;

  const var95 = calcVaR(dailyReturns, 0.05);

  return {
    sharpeRatio: round4(sharpeRatio),
    sortinoRatio: round4(sortinoRatio),
    calmarRatio: round4(calmarRatio),
    recoveryFactor: round4(recoveryFactor),
    var95: round4(var95),
  };
}

// ── Trade Analysis ──────────────────────────────────────────────

function computeTradeAnalysis(trades: TradeRecord[]): TradeAnalysisMetrics {
  if (trades.length === 0) {
    return {
      totalTrades: 0,
      winningTrades: 0,
      losingTrades: 0,
      winRate: 0,
      profitFactor: 0,
      payoffRatio: 0,
      expectancy: 0,
      avgTradeNetPnl: 0,
      avgWin: 0,
      avgLoss: 0,
      largestWin: 0,
      largestLoss: 0,
      avgHoldingDays: 0,
      maxConsecutiveWins: 0,
      maxConsecutiveLosses: 0,
      kellyCriterion: 0,
      totalCommission: 0,
    };
  }

  const wins = trades.filter((t) => t.netPnl > 0);
  const losses = trades.filter((t) => t.netPnl <= 0);

  const totalWinAmount = wins.reduce((s, t) => s + t.netPnl, 0);
  const totalLossAmount = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));

  const winRate = trades.length > 0 ? wins.length / trades.length : 0;
  const avgWin = wins.length > 0 ? totalWinAmount / wins.length : 0;
  const avgLoss = losses.length > 0 ? totalLossAmount / losses.length : 0;

  const profitFactor = totalLossAmount > 0 ? totalWinAmount / totalLossAmount : 0;
  const payoffRatio = avgLoss > 0 ? avgWin / avgLoss : 0;

  // Expectancy = winRate × avgWin - lossRate × avgLoss
  const lossRate = 1 - winRate;
  const expectancy = winRate * avgWin - lossRate * avgLoss;

  const avgTradeNetPnl =
    trades.length > 0 ? trades.reduce((s, t) => s + t.netPnl, 0) / trades.length : 0;

  const largestWin = wins.length > 0 ? Math.max(...wins.map((t) => t.netPnl)) : 0;
  const largestLoss = losses.length > 0 ? Math.min(...losses.map((t) => t.netPnl)) : 0;

  // Average holding period
  const tradesWithHolding = trades.filter((t) => t.holdingDays != null);
  const avgHoldingDays =
    tradesWithHolding.length > 0
      ? tradesWithHolding.reduce((s, t) => s + (t.holdingDays ?? 0), 0) / tradesWithHolding.length
      : calcAvgHoldingFromDates(trades);

  // Consecutive wins/losses
  const { maxWins, maxLosses } = calcConsecutiveStreaks(trades);

  // Kelly criterion: f* = W - (1-W)/R where W = win rate, R = payoff ratio
  const kellyCriterion = payoffRatio > 0 ? winRate - (1 - winRate) / payoffRatio : 0;

  const totalCommission = trades.reduce((s, t) => s + t.commission, 0);

  return {
    totalTrades: trades.length,
    winningTrades: wins.length,
    losingTrades: losses.length,
    winRate: round4(winRate),
    profitFactor: round4(profitFactor),
    payoffRatio: round4(payoffRatio),
    expectancy: round2(expectancy),
    avgTradeNetPnl: round2(avgTradeNetPnl),
    avgWin: round2(avgWin),
    avgLoss: round2(avgLoss),
    largestWin: round2(largestWin),
    largestLoss: round2(largestLoss),
    avgHoldingDays: round1(avgHoldingDays),
    maxConsecutiveWins: maxWins,
    maxConsecutiveLosses: maxLosses,
    kellyCriterion: round4(kellyCriterion),
    totalCommission: round2(totalCommission),
  };
}

// ── Trade Frequency ─────────────────────────────────────────────

function computeTradeFrequency(trades: TradeRecord[], periodDays: number): TradeFrequencyMetrics {
  if (trades.length === 0 || periodDays <= 0) {
    return { tradesPerDay: 0, tradesPerWeek: 0, tradesPerMonth: 0, activeTradingDays: 0 };
  }

  // Count unique trading days (by exit date)
  const tradingDays = new Set(
    trades.map((t) => (t.exitTime ?? t.entryTime).slice(0, 10)),
  );
  const activeTradingDays = tradingDays.size;

  const weeks = Math.max(periodDays / 7, 1);
  const months = Math.max(periodDays / 30.44, 1);

  return {
    tradesPerDay: round2(activeTradingDays > 0 ? trades.length / activeTradingDays : 0),
    tradesPerWeek: round2(trades.length / weeks),
    tradesPerMonth: round2(trades.length / months),
    activeTradingDays,
  };
}

// ── Capital Metrics ─────────────────────────────────────────────

function computeCapitalMetrics(
  snapshots: EquitySnapshot[],
  trades: TradeRecord[],
): CapitalMetrics {
  if (snapshots.length === 0) {
    return {
      initialCapital: 0,
      finalValue: 0,
      netProfit: 0,
      avgCapitalUtilization: 0,
    };
  }

  const initialCapital = snapshots[0]!.equity;
  const finalValue = snapshots[snapshots.length - 1]!.equity;
  const netProfit = trades.reduce((s, t) => s + t.netPnl, 0);

  // Capital utilization: average (marketValue / equity)
  const utilizationSamples = snapshots.filter((s) => s.equity > 0);
  const avgCapitalUtilization =
    utilizationSamples.length > 0
      ? utilizationSamples.reduce((s, snap) => s + snap.marketValue / snap.equity, 0) /
        utilizationSamples.length
      : 0;

  return {
    initialCapital: round2(initialCapital),
    finalValue: round2(finalValue),
    netProfit: round2(netProfit),
    avgCapitalUtilization: round4(avgCapitalUtilization),
  };
}

// ── Statistical Helpers ─────────────────────────────────────────

function calcDailyReturns(snapshots: EquitySnapshot[]): number[] {
  const returns: number[] = [];
  for (let i = 1; i < snapshots.length; i++) {
    const prev = snapshots[i - 1]!.equity;
    if (prev > 0) {
      returns.push(snapshots[i]!.equity / prev - 1);
    }
  }
  return returns;
}

function mean(arr: number[]): number {
  return arr.length > 0 ? arr.reduce((a, b) => a + b, 0) / arr.length : 0;
}

function stdDev(arr: number[]): number {
  if (arr.length < 2) return 0;
  const m = mean(arr);
  const variance = arr.reduce((s, v) => s + (v - m) ** 2, 0) / (arr.length - 1);
  return Math.sqrt(variance);
}

function calcSharpe(
  dailyReturns: number[],
  riskFreeAnnual: number,
  tradingDays: number,
): number {
  if (dailyReturns.length < 2) return 0;
  const rfDaily = riskFreeAnnual / tradingDays;
  const excess = dailyReturns.map((r) => r - rfDaily);
  const meanExcess = mean(excess);
  const std = stdDev(excess);
  return std > 0 ? (meanExcess / std) * Math.sqrt(tradingDays) : 0;
}

function calcSortino(
  dailyReturns: number[],
  riskFreeAnnual: number,
  tradingDays: number,
): number {
  if (dailyReturns.length < 2) return 0;
  const rfDaily = riskFreeAnnual / tradingDays;
  const excess = dailyReturns.map((r) => r - rfDaily);
  const meanExcess = mean(excess);
  const downside = excess.filter((r) => r < 0);
  if (downside.length === 0) return 0;
  const downsideDev = Math.sqrt(downside.reduce((s, r) => s + r ** 2, 0) / downside.length);
  return downsideDev > 0 ? (meanExcess / downsideDev) * Math.sqrt(tradingDays) : 0;
}

function calcVaR(dailyReturns: number[], alpha: number): number {
  if (dailyReturns.length === 0) return 0;
  const sorted = [...dailyReturns].sort((a, b) => a - b);
  const idx = Math.max(Math.floor(sorted.length * alpha), 0);
  return Math.abs(sorted[idx]!);
}

function calcPeriodicReturns(
  snapshots: EquitySnapshot[],
  periodKeyFn: (date: string) => string,
): Array<{ period: string; return: number }> {
  if (snapshots.length < 2) return [];

  const periodReturns: Array<{ period: string; return: number }> = [];
  let prevValue = snapshots[0]!.equity;
  let prevPeriod = periodKeyFn(snapshots[0]!.date);

  for (let i = 1; i < snapshots.length; i++) {
    const snap = snapshots[i]!;
    const period = periodKeyFn(snap.date);
    if (period !== prevPeriod) {
      const ret = prevValue > 0 ? snap.equity / prevValue - 1 : 0;
      periodReturns.push({ period: prevPeriod, return: round4(ret) });
      prevValue = snap.equity;
      prevPeriod = period;
    }
  }

  // Last period
  const lastEquity = snapshots[snapshots.length - 1]!.equity;
  const ret = prevValue > 0 ? lastEquity / prevValue - 1 : 0;
  periodReturns.push({ period: prevPeriod, return: round4(ret) });

  return periodReturns;
}

function calcConsecutiveStreaks(trades: TradeRecord[]): {
  maxWins: number;
  maxLosses: number;
} {
  let maxWins = 0;
  let maxLosses = 0;
  let curWins = 0;
  let curLosses = 0;

  // Sort by exit time for proper sequence
  const sorted = [...trades].sort((a, b) =>
    (a.exitTime ?? a.entryTime).localeCompare(b.exitTime ?? b.entryTime),
  );

  for (const t of sorted) {
    if (t.netPnl > 0) {
      curWins++;
      curLosses = 0;
      if (curWins > maxWins) maxWins = curWins;
    } else {
      curLosses++;
      curWins = 0;
      if (curLosses > maxLosses) maxLosses = curLosses;
    }
  }

  return { maxWins, maxLosses };
}

function calcAvgHoldingFromDates(trades: TradeRecord[]): number {
  let total = 0;
  let count = 0;
  for (const t of trades) {
    if (t.entryTime && t.exitTime) {
      const days = daysBetween(t.entryTime.slice(0, 10), t.exitTime.slice(0, 10));
      total += days;
      count++;
    }
  }
  return count > 0 ? total / count : 0;
}

function daysBetween(a: string, b: string): number {
  const da = new Date(a);
  const db = new Date(b);
  return Math.max(0, Math.round(Math.abs(db.getTime() - da.getTime()) / (1000 * 60 * 60 * 24)));
}

function isoWeek(dateStr: string): string {
  const d = new Date(dateStr);
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${String(d.getUTCFullYear())}-W${String(weekNo).padStart(2, "0")}`;
}

// ── Rounding Helpers ────────────────────────────────────────────

function round4(n: number): number {
  return Math.round(n * 10000) / 10000;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}
