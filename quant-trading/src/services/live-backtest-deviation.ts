/**
 * LiveBacktestDeviation — compares live trading performance against backtest results.
 *
 * Computes deviation metrics between live (real/paper) trades and historical
 * backtest expectations for the same strategy:
 *
 *   - Return deviation: live return vs backtest return
 *   - Win rate deviation: live win rate vs backtest win rate
 *   - Slippage analysis: actual execution slippage vs modeled slippage
 *   - Drawdown deviation: live max drawdown vs backtest max drawdown
 *   - Trade frequency deviation: actual trades vs expected trade count
 *   - Equity curve correlation: how closely live equity tracks backtest
 *   - Per-strategy breakdown with health scoring
 *
 * Data sources:
 *   - Live trades: trades.json (TradeRecord[])
 *   - Backtest results: BacktestStore (BacktestRecord)
 *   - Equity history: EquityHistory snapshots
 */
import type { BacktestResult, TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Types ────────────────────────────────────────────────────

export interface DeviationMetric {
  /** Metric name (e.g. "totalReturn", "winRate") */
  metric: string;
  /** Backtest value */
  backtest: number;
  /** Live value */
  live: number;
  /** Absolute deviation (live - backtest) */
  deviation: number;
  /** Relative deviation as percentage: (live - backtest) / |backtest| * 100 */
  deviationPct: number;
  /** Severity: "normal" | "warning" | "critical" */
  severity: DeviationSeverity;
}

export type DeviationSeverity = "normal" | "warning" | "critical";

export interface SlippageAnalysis {
  /** Total number of trades analyzed */
  totalTrades: number;
  /** Average slippage per trade in basis points */
  avgSlippageBps: number;
  /** Maximum single-trade slippage in basis points */
  maxSlippageBps: number;
  /** Slippage standard deviation in bps */
  slippageStdBps: number;
  /** Estimated total slippage cost in CNY */
  totalSlippageCost: number;
  /** Slippage as percentage of initial capital */
  slippagePctOfCapital: number;
  /** Buy-side average slippage bps */
  buyAvgSlippageBps: number;
  /** Sell-side average slippage bps */
  sellAvgSlippageBps: number;
}

export interface EquityCurveCorrelation {
  /** Pearson correlation coefficient between live and backtest equity curves (-1 to 1) */
  correlation: number;
  /** Mean absolute deviation of normalized equity curves */
  meanAbsDeviation: number;
  /** Maximum divergence point */
  maxDivergence: { date: string; liveReturn: number; backtestReturn: number; gap: number } | null;
  /** Tracking error (annualized std dev of daily return differences) */
  trackingError: number;
}

export interface StrategyDeviation {
  strategyId: string;
  liveTrades: number;
  backtestTrades: number;
  metrics: DeviationMetric[];
  /** Overall health score 0-100 (100 = perfect match) */
  healthScore: number;
  /** Human-readable summary */
  summary: string;
}

export interface DeviationReport {
  /** Date range of analysis */
  dateRange: { from: string; to: string };
  /** Overall metrics deviation */
  overallMetrics: DeviationMetric[];
  /** Slippage analysis */
  slippage: SlippageAnalysis;
  /** Equity curve correlation (null if insufficient data) */
  equityCurveCorrelation: EquityCurveCorrelation | null;
  /** Per-strategy breakdown */
  byStrategy: StrategyDeviation[];
  /** Overall health score 0-100 */
  overallHealthScore: number;
  /** Alerts for significant deviations */
  alerts: DeviationAlert[];
}

export interface DeviationAlert {
  severity: DeviationSeverity;
  metric: string;
  message: string;
  strategyId?: string;
}

// ── Thresholds for severity classification ──────────────────

interface DeviationThresholds {
  /** Warning threshold for return deviation (percentage points) */
  returnWarning: number;
  returnCritical: number;
  /** Warning threshold for win rate deviation (percentage points) */
  winRateWarning: number;
  winRateCritical: number;
  /** Warning threshold for max drawdown deviation (percentage points) */
  drawdownWarning: number;
  drawdownCritical: number;
  /** Warning threshold for Sharpe ratio deviation (absolute) */
  sharpeWarning: number;
  sharpeCritical: number;
  /** Warning threshold for trade frequency deviation (percentage) */
  tradeFreqWarning: number;
  tradeFreqCritical: number;
}

const DEFAULT_THRESHOLDS: DeviationThresholds = {
  returnWarning: 5,
  returnCritical: 15,
  winRateWarning: 5,
  winRateCritical: 15,
  drawdownWarning: 5,
  drawdownCritical: 10,
  sharpeWarning: 0.3,
  sharpeCritical: 0.8,
  tradeFreqWarning: 20,
  tradeFreqCritical: 50,
};

// ── Core computation ────────────────────────────────────────

/**
 * Classify deviation severity for a return-like metric (percentage points).
 */
function classifyReturnDeviation(
  absDeviation: number,
  warnThreshold: number,
  critThreshold: number,
): DeviationSeverity {
  if (absDeviation >= critThreshold) return "critical";
  if (absDeviation >= warnThreshold) return "warning";
  return "normal";
}

/**
 * Compute live trading metrics from trade records.
 */
function computeLiveMetrics(
  trades: TradeRecord[],
  initialCash: number,
): {
  totalReturn: number;
  winRate: number;
  totalTrades: number;
  profitFactor: number;
  avgWin: number;
  avgLoss: number;
  maxDrawdown: number;
  netPnl: number;
} {
  const closed = trades.filter((t) => t.exitTime != null);
  if (closed.length === 0) {
    return {
      totalReturn: 0,
      winRate: 0,
      totalTrades: 0,
      profitFactor: 0,
      avgWin: 0,
      avgLoss: 0,
      maxDrawdown: 0,
      netPnl: 0,
    };
  }

  const wins = closed.filter((t) => t.netPnl > 0);
  const losses = closed.filter((t) => t.netPnl <= 0);
  const totalWinAmount = wins.reduce((s, t) => s + t.netPnl, 0);
  const totalLossAmount = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));
  const netPnl = closed.reduce((s, t) => s + t.netPnl, 0);

  // Compute max drawdown from cumulative P&L
  let cumPnl = 0;
  let peak = 0;
  let maxDd = 0;
  for (const t of closed) {
    cumPnl += t.netPnl;
    if (cumPnl > peak) peak = cumPnl;
    const dd = peak - cumPnl;
    if (dd > maxDd) maxDd = dd;
  }
  const maxDrawdown = initialCash > 0 ? (maxDd / (initialCash + peak)) * 100 : 0;

  return {
    totalReturn: initialCash > 0 ? (netPnl / initialCash) * 100 : 0,
    winRate: closed.length > 0 ? (wins.length / closed.length) * 100 : 0,
    totalTrades: closed.length,
    profitFactor:
      totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount > 0 ? Infinity : 0,
    avgWin: wins.length > 0 ? totalWinAmount / wins.length : 0,
    avgLoss: losses.length > 0 ? totalLossAmount / losses.length : 0,
    maxDrawdown,
    netPnl,
  };
}

/**
 * Analyze execution slippage from live trades compared to signal prices.
 */
export function computeSlippageAnalysis(
  trades: TradeRecord[],
  initialCash: number,
): SlippageAnalysis {
  if (trades.length === 0) {
    return {
      totalTrades: 0,
      avgSlippageBps: 0,
      maxSlippageBps: 0,
      slippageStdBps: 0,
      totalSlippageCost: 0,
      slippagePctOfCapital: 0,
      buyAvgSlippageBps: 0,
      sellAvgSlippageBps: 0,
    };
  }

  // Slippage is measured as the difference between actual execution price
  // and the signal/expected price. Since we don't have signal prices stored
  // directly, we estimate using the spread between entry/exit prices and
  // the gross vs net P&L (commission component).
  const slippages: number[] = [];
  const buySlippages: number[] = [];
  const sellSlippages: number[] = [];
  let totalCost = 0;

  for (const t of trades) {
    // Commission per unit as proxy for total friction
    const tradeValue = t.entryPrice * t.quantity;
    if (tradeValue <= 0) continue;

    // Total friction = grossPnl - netPnl (includes commission, slippage, fees)
    const friction = t.grossPnl - t.netPnl;
    const frictionBps = (friction / tradeValue) * 10_000;

    slippages.push(Math.abs(frictionBps));
    totalCost += Math.abs(friction);

    if (t.side === "buy") {
      buySlippages.push(Math.abs(frictionBps));
    } else {
      sellSlippages.push(Math.abs(frictionBps));
    }
  }

  const mean = slippages.length > 0 ? slippages.reduce((a, b) => a + b, 0) / slippages.length : 0;
  const variance =
    slippages.length > 1
      ? slippages.reduce((sum, s) => sum + (s - mean) ** 2, 0) / (slippages.length - 1)
      : 0;

  return {
    totalTrades: trades.length,
    avgSlippageBps: mean,
    maxSlippageBps: slippages.length > 0 ? Math.max(...slippages) : 0,
    slippageStdBps: Math.sqrt(variance),
    totalSlippageCost: totalCost,
    slippagePctOfCapital: initialCash > 0 ? (totalCost / initialCash) * 100 : 0,
    buyAvgSlippageBps:
      buySlippages.length > 0
        ? buySlippages.reduce((a, b) => a + b, 0) / buySlippages.length
        : 0,
    sellAvgSlippageBps:
      sellSlippages.length > 0
        ? sellSlippages.reduce((a, b) => a + b, 0) / sellSlippages.length
        : 0,
  };
}

/**
 * Compute equity curve correlation between live and backtest data.
 */
export function computeEquityCurveCorrelation(
  liveSnapshots: EquitySnapshot[],
  backtestCurve: Array<{ date: string; value: number }>,
): EquityCurveCorrelation | null {
  if (liveSnapshots.length < 5 || backtestCurve.length < 5) return null;

  // Build date-indexed maps
  const liveByDate = new Map(liveSnapshots.map((s) => [s.date, s.equity]));
  const btByDate = new Map(backtestCurve.map((p) => [p.date, p.value]));

  // Find common dates
  const commonDates = liveSnapshots
    .map((s) => s.date)
    .filter((d) => btByDate.has(d))
    .sort();

  if (commonDates.length < 5) return null;

  const firstLive = liveByDate.get(commonDates[0]!)!;
  const firstBt = btByDate.get(commonDates[0]!)!;

  // Compute normalized returns
  const liveReturns: number[] = [];
  const btReturns: number[] = [];
  let maxDivergence: EquityCurveCorrelation["maxDivergence"] = null;
  let totalAbsDev = 0;

  for (const date of commonDates) {
    const liveRet = firstLive > 0 ? (liveByDate.get(date)! / firstLive - 1) * 100 : 0;
    const btRet = firstBt > 0 ? (btByDate.get(date)! / firstBt - 1) * 100 : 0;
    liveReturns.push(liveRet);
    btReturns.push(btRet);

    const gap = Math.abs(liveRet - btRet);
    totalAbsDev += gap;

    if (!maxDivergence || gap > maxDivergence.gap) {
      maxDivergence = { date, liveReturn: liveRet, backtestReturn: btRet, gap };
    }
  }

  // Pearson correlation
  const n = liveReturns.length;
  const meanLive = liveReturns.reduce((a, b) => a + b, 0) / n;
  const meanBt = btReturns.reduce((a, b) => a + b, 0) / n;

  let sumXY = 0;
  let sumX2 = 0;
  let sumY2 = 0;
  for (let i = 0; i < n; i++) {
    const dx = liveReturns[i]! - meanLive;
    const dy = btReturns[i]! - meanBt;
    sumXY += dx * dy;
    sumX2 += dx * dx;
    sumY2 += dy * dy;
  }
  const denom = Math.sqrt(sumX2 * sumY2);
  const correlation = denom > 0 ? sumXY / denom : 0;

  // Tracking error: annualized std dev of daily return differences
  const dailyDiffs: number[] = [];
  for (let i = 1; i < commonDates.length; i++) {
    const prevLive = liveByDate.get(commonDates[i - 1]!)!;
    const curLive = liveByDate.get(commonDates[i]!)!;
    const prevBt = btByDate.get(commonDates[i - 1]!)!;
    const curBt = btByDate.get(commonDates[i]!)!;

    const liveDaily = prevLive > 0 ? (curLive - prevLive) / prevLive : 0;
    const btDaily = prevBt > 0 ? (curBt - prevBt) / prevBt : 0;
    dailyDiffs.push(liveDaily - btDaily);
  }

  const meanDiff =
    dailyDiffs.length > 0 ? dailyDiffs.reduce((a, b) => a + b, 0) / dailyDiffs.length : 0;
  const diffVariance =
    dailyDiffs.length > 1
      ? dailyDiffs.reduce((sum, d) => sum + (d - meanDiff) ** 2, 0) / (dailyDiffs.length - 1)
      : 0;
  const trackingError = Math.sqrt(diffVariance) * Math.sqrt(252) * 100; // annualized, in %

  return {
    correlation,
    meanAbsDeviation: n > 0 ? totalAbsDev / n : 0,
    maxDivergence,
    trackingError,
  };
}

/**
 * Build a deviation metric entry with severity classification.
 */
function buildMetric(
  name: string,
  backtest: number,
  live: number,
  warnThreshold: number,
  critThreshold: number,
  /** If true, lower live value is worse (e.g. return, winRate). If false, higher is worse (e.g. drawdown). */
  lowerIsWorse = true,
): DeviationMetric {
  const deviation = live - backtest;
  const deviationPct = backtest !== 0 ? (deviation / Math.abs(backtest)) * 100 : 0;

  // For severity, check absolute deviation against thresholds
  const absDeviation = lowerIsWorse ? Math.max(0, backtest - live) : Math.max(0, live - backtest);
  const severity = classifyReturnDeviation(absDeviation, warnThreshold, critThreshold);

  return { metric: name, backtest, live, deviation, deviationPct, severity };
}

/**
 * Compute health score from deviation metrics (0-100).
 */
function computeHealthScore(metrics: DeviationMetric[]): number {
  if (metrics.length === 0) return 100;

  let score = 100;
  for (const m of metrics) {
    if (m.severity === "critical") score -= 25;
    else if (m.severity === "warning") score -= 10;
  }
  return Math.max(0, Math.min(100, score));
}

/**
 * Generate alerts from deviation metrics.
 */
function generateAlerts(
  metrics: DeviationMetric[],
  strategyId?: string,
): DeviationAlert[] {
  const alerts: DeviationAlert[] = [];
  for (const m of metrics) {
    if (m.severity === "normal") continue;

    const direction = m.deviation < 0 ? "低于" : "高于";
    const msg = `${m.metric} 实盘${direction}回测 ${Math.abs(m.deviation).toFixed(2)} (偏差${Math.abs(m.deviationPct).toFixed(1)}%)`;
    alerts.push({
      severity: m.severity,
      metric: m.metric,
      message: strategyId ? `[${strategyId}] ${msg}` : msg,
      strategyId,
    });
  }
  return alerts;
}

// ── Main API ────────────────────────────────────────────────

export interface ComputeDeviationOptions {
  /** Live trade records (closed trades) */
  liveTrades: TradeRecord[];
  /** Backtest result to compare against */
  backtestResult: BacktestResult;
  /** Live equity snapshots (optional, for equity curve correlation) */
  liveEquity?: EquitySnapshot[];
  /** Initial capital (for relative calculations) */
  initialCash?: number;
  /** Custom severity thresholds */
  thresholds?: Partial<DeviationThresholds>;
}

/**
 * Compute the full deviation report comparing live trading vs backtest.
 */
export function computeDeviation(opts: ComputeDeviationOptions): DeviationReport {
  const {
    liveTrades,
    backtestResult: bt,
    liveEquity,
    initialCash = bt.initialCash,
    thresholds: customThresholds,
  } = opts;

  const th = { ...DEFAULT_THRESHOLDS, ...customThresholds };

  // Filter trades to same date range as backtest
  const filtered = liveTrades.filter((t) => {
    const date = (t.exitTime ?? t.entryTime).slice(0, 10);
    return date >= bt.startDate && date <= bt.endDate;
  });

  // Group by strategy
  const byStrategyMap = new Map<string, TradeRecord[]>();
  for (const t of filtered) {
    const sid = t.strategyId ?? "unknown";
    if (!byStrategyMap.has(sid)) byStrategyMap.set(sid, []);
    byStrategyMap.get(sid)!.push(t);
  }

  // Compute overall live metrics
  const liveMetrics = computeLiveMetrics(filtered, initialCash);

  // Build overall deviation metrics
  const overallMetrics: DeviationMetric[] = [
    buildMetric("收益率(%)", bt.totalReturn, liveMetrics.totalReturn, th.returnWarning, th.returnCritical),
    buildMetric("胜率(%)", bt.winRate * 100, liveMetrics.winRate, th.winRateWarning, th.winRateCritical),
    buildMetric("最大回撤(%)", bt.maxDrawdown, liveMetrics.maxDrawdown, th.drawdownWarning, th.drawdownCritical, false),
    buildMetric("盈亏比", bt.profitFactor ?? 0, liveMetrics.profitFactor === Infinity ? 999 : liveMetrics.profitFactor, 0.3, 0.8),
    buildMetric("交易次数", bt.totalTrades, liveMetrics.totalTrades, bt.totalTrades * th.tradeFreqWarning / 100, bt.totalTrades * th.tradeFreqCritical / 100),
  ];

  // Slippage analysis
  const slippage = computeSlippageAnalysis(filtered, initialCash);

  // Equity curve correlation
  let equityCurveCorrelation: EquityCurveCorrelation | null = null;
  if (liveEquity && bt.equityCurve.length > 0) {
    equityCurveCorrelation = computeEquityCurveCorrelation(liveEquity, bt.equityCurve);
  }

  // Per-strategy breakdown
  const byStrategy: StrategyDeviation[] = [];
  // Only do per-strategy if the backtest is for a single strategy
  const btStrategyId = bt.strategyId;
  for (const [sid, stratTrades] of byStrategyMap) {
    const stratLive = computeLiveMetrics(stratTrades, initialCash);
    const isSameStrategy = sid === btStrategyId;

    const stratMetrics: DeviationMetric[] = isSameStrategy
      ? [
          buildMetric("收益率(%)", bt.totalReturn, stratLive.totalReturn, th.returnWarning, th.returnCritical),
          buildMetric("胜率(%)", bt.winRate * 100, stratLive.winRate, th.winRateWarning, th.winRateCritical),
          buildMetric("最大回撤(%)", bt.maxDrawdown, stratLive.maxDrawdown, th.drawdownWarning, th.drawdownCritical, false),
        ]
      : [
          // For non-matching strategies, only report live metrics without backtest comparison
          { metric: "收益率(%)", backtest: 0, live: stratLive.totalReturn, deviation: stratLive.totalReturn, deviationPct: 0, severity: "normal" as DeviationSeverity },
          { metric: "胜率(%)", backtest: 0, live: stratLive.winRate, deviation: stratLive.winRate, deviationPct: 0, severity: "normal" as DeviationSeverity },
        ];

    const healthScore = computeHealthScore(stratMetrics);
    const critCount = stratMetrics.filter((m) => m.severity === "critical").length;
    const warnCount = stratMetrics.filter((m) => m.severity === "warning").length;

    let summary: string;
    if (critCount > 0) {
      summary = `策略 ${sid} 存在 ${critCount} 个严重偏差指标，需要重点关注`;
    } else if (warnCount > 0) {
      summary = `策略 ${sid} 存在 ${warnCount} 个轻微偏差，整体表现尚可`;
    } else {
      summary = `策略 ${sid} 实盘表现与回测基本一致`;
    }

    byStrategy.push({
      strategyId: sid,
      liveTrades: stratTrades.length,
      backtestTrades: isSameStrategy ? bt.totalTrades : 0,
      metrics: stratMetrics,
      healthScore,
      summary,
    });
  }

  // Generate alerts
  const alerts: DeviationAlert[] = [
    ...generateAlerts(overallMetrics),
    ...byStrategy.flatMap((s) => generateAlerts(s.metrics, s.strategyId)),
  ];

  // Sort alerts: critical first
  const severityOrder: Record<DeviationSeverity, number> = { critical: 0, warning: 1, normal: 2 };
  alerts.sort((a, b) => severityOrder[a.severity] - severityOrder[b.severity]);

  // Deduplicate alerts (overall vs per-strategy may overlap)
  const seen = new Set<string>();
  const uniqueAlerts = alerts.filter((a) => {
    const key = `${a.metric}:${a.strategyId ?? "overall"}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // Determine date range
  const dates = filtered.map((t) => (t.exitTime ?? t.entryTime).slice(0, 10)).sort();
  const from = dates[0] ?? bt.startDate;
  const to = dates[dates.length - 1] ?? bt.endDate;

  return {
    dateRange: { from, to },
    overallMetrics,
    slippage,
    equityCurveCorrelation,
    byStrategy,
    overallHealthScore: computeHealthScore(overallMetrics),
    alerts: uniqueAlerts,
  };
}
