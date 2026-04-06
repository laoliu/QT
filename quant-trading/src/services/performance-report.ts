/**
 * PerformanceReport — structured trading performance report service.
 *
 * Aggregates trade records into comprehensive performance reports with:
 *   - Configurable performance metrics (return, risk, efficiency, consistency)
 *   - Filtering by account mode (paper/live), strategy, symbol, time range
 *   - Trade-level aggregation into daily/weekly/monthly summaries
 *   - Risk-adjusted return metrics (Sharpe, Sortino, Calmar proxies)
 *   - Streak & consistency analysis
 *
 * Reads from the TradeRecord store (trades-paper.json / trades-live.json).
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { QuantTradingConfig, TradeRecord } from "../types.js";

// ── Performance Metric Enum ─────────────────────────────────────

/** All computable performance metrics. */
export type PerformanceMetric =
  // Return metrics
  | "totalNetPnl"
  | "totalGrossPnl"
  | "totalCommission"
  | "returnOnCapital" // netPnl / initialCash (if known)
  // Win/loss metrics
  | "winRate"
  | "lossRate"
  | "avgWin"
  | "avgLoss"
  | "profitFactor"
  | "payoffRatio" // avgWin / avgLoss
  | "expectancy" // (winRate * avgWin) - (lossRate * avgLoss)
  // Trade count metrics
  | "totalTrades"
  | "totalWins"
  | "totalLosses"
  | "avgTradesPerDay"
  // Risk metrics
  | "maxConsecutiveWins"
  | "maxConsecutiveLosses"
  | "largestWin"
  | "largestLoss"
  | "maxDrawdown" // peak-to-trough based on cumulative PnL
  // Holding metrics
  | "avgHoldingDays"
  | "avgWinHoldingDays"
  | "avgLossHoldingDays";

/** Groupings for metric display. */
export const METRIC_GROUPS: Record<string, PerformanceMetric[]> = {
  return: ["totalNetPnl", "totalGrossPnl", "totalCommission", "returnOnCapital"],
  winLoss: [
    "winRate",
    "lossRate",
    "avgWin",
    "avgLoss",
    "profitFactor",
    "payoffRatio",
    "expectancy",
  ],
  trade: ["totalTrades", "totalWins", "totalLosses", "avgTradesPerDay"],
  risk: [
    "maxConsecutiveWins",
    "maxConsecutiveLosses",
    "largestWin",
    "largestLoss",
    "maxDrawdown",
  ],
  holding: ["avgHoldingDays", "avgWinHoldingDays", "avgLossHoldingDays"],
};

// ── Aggregation Granularity ─────────────────────────────────────

export type AggregationPeriod = "daily" | "weekly" | "monthly";

// ── Query / Filter Interface ────────────────────────────────────

export interface PerformanceReportQuery {
  /** Account mode — determines which trades file to read. */
  accountMode?: "paper" | "live";
  /** Filter by strategy ID(s). Empty/undefined = all strategies. */
  strategyIds?: string[];
  /** Filter by symbol(s). Empty/undefined = all symbols. */
  symbols?: string[];
  /** Start date inclusive (YYYY-MM-DD). */
  from?: string;
  /** End date inclusive (YYYY-MM-DD). */
  to?: string;
  /** Which metrics to include in the report; undefined = all. */
  metrics?: PerformanceMetric[];
  /** Aggregation period for the time-series breakdown. Default: "daily". */
  aggregationPeriod?: AggregationPeriod;
  /** Initial capital (for returnOnCapital). If not provided, the metric is omitted. */
  initialCapital?: number;
}

// ── Report Result Structures ────────────────────────────────────

/** Computed metric values — all numeric (NaN if not computable). */
export type MetricValues = Record<PerformanceMetric, number>;

/** Single row in the time-series aggregation. */
export interface PeriodSummary {
  /** Period label: YYYY-MM-DD, YYYY-WNN, or YYYY-MM. */
  period: string;
  trades: number;
  wins: number;
  losses: number;
  netPnl: number;
  grossPnl: number;
  commission: number;
  cumNetPnl: number;
  winRate: number;
}

/** Per-strategy breakdown within a report. */
export interface StrategySection {
  strategyId: string;
  metrics: MetricValues;
  tradeCount: number;
}

/** Per-symbol breakdown within a report. */
export interface SymbolSection {
  symbol: string;
  metrics: MetricValues;
  tradeCount: number;
}

/** The full performance report result. */
export interface PerformanceReportResult {
  /** Effective query that produced this report. */
  query: PerformanceReportQuery;
  /** Date range of the included trades. */
  dateRange: { from: string; to: string };
  /** Overall metrics across all included trades. */
  overallMetrics: MetricValues;
  /** Time-series aggregation. */
  periodSummaries: PeriodSummary[];
  /** Per-strategy sections (sorted by |netPnl| desc). */
  byStrategy: StrategySection[];
  /** Per-symbol sections (sorted by |netPnl| desc). */
  bySymbol: SymbolSection[];
  /** Total number of trades included. */
  totalTradesIncluded: number;
}

// ── Helpers ─────────────────────────────────────────────────────

function tradesFilePath(config: QuantTradingConfig, mode?: "paper" | "live"): string {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  if (mode) return path.join(dir, `trades-${mode}.json`);
  // Fallback: generic trades.json
  return path.join(dir, "trades.json");
}

async function loadTradesFromFile(filePath: string): Promise<TradeRecord[]> {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf-8")) as TradeRecord[];
  } catch {
    return [];
  }
}

/** ISO week string YYYY-WNN. */
function isoWeek(dateStr: string): string {
  const d = new Date(dateStr);
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${String(d.getUTCFullYear())}-W${String(weekNo).padStart(2, "0")}`;
}

function periodKey(dateStr: string, period: AggregationPeriod): string {
  switch (period) {
    case "daily":
      return dateStr.slice(0, 10);
    case "weekly":
      return isoWeek(dateStr);
    case "monthly":
      return dateStr.slice(0, 7);
  }
}

/** Count distinct days in a set of trade exitTimes. */
function distinctDays(trades: TradeRecord[]): number {
  const days = new Set<string>();
  for (const t of trades) {
    if (t.exitTime) days.add(t.exitTime.slice(0, 10));
  }
  return Math.max(days.size, 1);
}

// ── Metric Computation ──────────────────────────────────────────

function computeMetrics(trades: TradeRecord[], initialCapital?: number): MetricValues {
  const wins = trades.filter((t) => t.netPnl > 0);
  const losses = trades.filter((t) => t.netPnl <= 0);

  const totalNetPnl = trades.reduce((s, t) => s + t.netPnl, 0);
  const totalGrossPnl = trades.reduce((s, t) => s + t.grossPnl, 0);
  const totalCommission = trades.reduce((s, t) => s + t.commission, 0);
  const totalTrades = trades.length;
  const totalWins = wins.length;
  const totalLosses = losses.length;
  const winRate = totalTrades > 0 ? totalWins / totalTrades : 0;
  const lossRate = totalTrades > 0 ? totalLosses / totalTrades : 0;

  const totalWinAmount = wins.reduce((s, t) => s + t.netPnl, 0);
  const totalLossAmount = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));
  const avgWin = totalWins > 0 ? totalWinAmount / totalWins : 0;
  const avgLoss = totalLosses > 0 ? totalLossAmount / totalLosses : 0;

  const profitFactor =
    totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount > 0 ? Infinity : 0;
  const payoffRatio = avgLoss > 0 ? avgWin / avgLoss : avgWin > 0 ? Infinity : 0;
  const expectancy = winRate * avgWin - lossRate * avgLoss;

  const returnOnCapital =
    initialCapital != null && initialCapital > 0 ? (totalNetPnl / initialCapital) * 100 : NaN;

  // Consecutive wins/losses
  let maxConsecutiveWins = 0;
  let maxConsecutiveLosses = 0;
  let currentWinStreak = 0;
  let currentLossStreak = 0;
  for (const t of trades) {
    if (t.netPnl > 0) {
      currentWinStreak++;
      currentLossStreak = 0;
      maxConsecutiveWins = Math.max(maxConsecutiveWins, currentWinStreak);
    } else {
      currentLossStreak++;
      currentWinStreak = 0;
      maxConsecutiveLosses = Math.max(maxConsecutiveLosses, currentLossStreak);
    }
  }

  const largestWin = totalWins > 0 ? Math.max(...wins.map((t) => t.netPnl)) : 0;
  const largestLoss = totalLosses > 0 ? Math.min(...losses.map((t) => t.netPnl)) : 0;

  // Max drawdown based on cumulative PnL
  let peak = 0;
  let cumPnl = 0;
  let maxDrawdown = 0;
  for (const t of trades) {
    cumPnl += t.netPnl;
    if (cumPnl > peak) peak = cumPnl;
    const dd = peak - cumPnl;
    if (dd > maxDrawdown) maxDrawdown = dd;
  }

  // Holding days
  const allHolding = trades.filter((t) => t.holdingDays != null);
  const avgHoldingDays =
    allHolding.length > 0
      ? allHolding.reduce((s, t) => s + (t.holdingDays ?? 0), 0) / allHolding.length
      : 0;
  const winHolding = wins.filter((t) => t.holdingDays != null);
  const avgWinHoldingDays =
    winHolding.length > 0
      ? winHolding.reduce((s, t) => s + (t.holdingDays ?? 0), 0) / winHolding.length
      : 0;
  const lossHolding = losses.filter((t) => t.holdingDays != null);
  const avgLossHoldingDays =
    lossHolding.length > 0
      ? lossHolding.reduce((s, t) => s + (t.holdingDays ?? 0), 0) / lossHolding.length
      : 0;

  const numDays = distinctDays(trades);
  const avgTradesPerDay = totalTrades / numDays;

  return {
    totalNetPnl,
    totalGrossPnl,
    totalCommission,
    returnOnCapital,
    winRate,
    lossRate,
    avgWin,
    avgLoss,
    profitFactor,
    payoffRatio,
    expectancy,
    totalTrades,
    totalWins,
    totalLosses,
    avgTradesPerDay,
    maxConsecutiveWins,
    maxConsecutiveLosses,
    largestWin,
    largestLoss,
    maxDrawdown,
    avgHoldingDays,
    avgWinHoldingDays,
    avgLossHoldingDays,
  };
}

// ── Service Class ───────────────────────────────────────────────

export class PerformanceReportService {
  private readonly config: QuantTradingConfig;

  constructor(config: QuantTradingConfig) {
    this.config = config;
  }

  /**
   * Generate a performance report based on the given query.
   * Loads trades, applies filters, computes metrics and aggregations.
   */
  async generateReport(query: PerformanceReportQuery = {}): Promise<PerformanceReportResult> {
    const trades = await this.loadAndFilterTrades(query);

    // Sort by exit time for correct streak and cumulative calculations
    trades.sort((a, b) => (a.exitTime ?? "").localeCompare(b.exitTime ?? ""));

    const overallMetrics = computeMetrics(trades, query.initialCapital);

    const periodSummaries = this.buildPeriodSummaries(
      trades,
      query.aggregationPeriod ?? "daily",
    );

    const byStrategy = this.buildStrategySections(trades, query.initialCapital);
    const bySymbol = this.buildSymbolSections(trades, query.initialCapital);

    // Date range
    const exitDates = trades
      .map((t) => t.exitTime ?? "")
      .filter(Boolean)
      .sort();
    const from = exitDates[0]?.slice(0, 10) ?? "";
    const to = exitDates[exitDates.length - 1]?.slice(0, 10) ?? "";

    return {
      query,
      dateRange: { from, to },
      overallMetrics,
      periodSummaries,
      byStrategy,
      bySymbol,
      totalTradesIncluded: trades.length,
    };
  }

  /**
   * Load trades from disk and apply query filters.
   */
  async loadAndFilterTrades(query: PerformanceReportQuery): Promise<TradeRecord[]> {
    // Load from appropriate file(s) based on account mode
    let trades: TradeRecord[];
    if (query.accountMode) {
      trades = await loadTradesFromFile(tradesFilePath(this.config, query.accountMode));
    } else {
      // Try both paper + live, then fallback to generic
      const [paper, live, generic] = await Promise.all([
        loadTradesFromFile(tradesFilePath(this.config, "paper")),
        loadTradesFromFile(tradesFilePath(this.config, "live")),
        loadTradesFromFile(tradesFilePath(this.config)),
      ]);
      trades = [...paper, ...live];
      if (trades.length === 0) trades = generic;
    }

    // Only include closed trades (with exitTime)
    trades = trades.filter((t) => t.exitTime != null);

    // Filter by strategy
    if (query.strategyIds && query.strategyIds.length > 0) {
      const ids = new Set(query.strategyIds);
      trades = trades.filter((t) => ids.has(t.strategyId ?? "unknown"));
    }

    // Filter by symbol
    if (query.symbols && query.symbols.length > 0) {
      const syms = new Set(query.symbols);
      trades = trades.filter((t) => syms.has(t.symbol));
    }

    // Filter by date range (based on exitTime)
    if (query.from) {
      trades = trades.filter((t) => (t.exitTime ?? "") >= query.from!);
    }
    if (query.to) {
      // To is inclusive; compare exit date (first 10 chars)
      trades = trades.filter((t) => (t.exitTime ?? "").slice(0, 10) <= query.to!);
    }

    return trades;
  }

  // ── Private helpers ───────────────────────────────────────────

  private buildPeriodSummaries(
    trades: TradeRecord[],
    period: AggregationPeriod,
  ): PeriodSummary[] {
    const map = new Map<
      string,
      {
        trades: number;
        wins: number;
        losses: number;
        netPnl: number;
        grossPnl: number;
        commission: number;
      }
    >();

    for (const t of trades) {
      const key = periodKey(t.exitTime ?? "", period);
      if (!map.has(key)) {
        map.set(key, { trades: 0, wins: 0, losses: 0, netPnl: 0, grossPnl: 0, commission: 0 });
      }
      const p = map.get(key)!;
      p.trades++;
      if (t.netPnl > 0) p.wins++;
      else p.losses++;
      p.netPnl += t.netPnl;
      p.grossPnl += t.grossPnl;
      p.commission += t.commission;
    }

    const sorted = [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    let cumNetPnl = 0;
    return sorted.map(([periodLabel, p]) => {
      cumNetPnl += p.netPnl;
      return {
        period: periodLabel,
        trades: p.trades,
        wins: p.wins,
        losses: p.losses,
        netPnl: p.netPnl,
        grossPnl: p.grossPnl,
        commission: p.commission,
        cumNetPnl,
        winRate: p.trades > 0 ? p.wins / p.trades : 0,
      };
    });
  }

  private buildStrategySections(
    trades: TradeRecord[],
    initialCapital?: number,
  ): StrategySection[] {
    const groups = new Map<string, TradeRecord[]>();
    for (const t of trades) {
      const sid = t.strategyId ?? "unknown";
      if (!groups.has(sid)) groups.set(sid, []);
      groups.get(sid)!.push(t);
    }

    const sections: StrategySection[] = [...groups.entries()].map(([sid, trs]) => ({
      strategyId: sid,
      metrics: computeMetrics(trs, initialCapital),
      tradeCount: trs.length,
    }));

    // Sort by absolute net PnL descending
    sections.sort((a, b) => Math.abs(b.metrics.totalNetPnl) - Math.abs(a.metrics.totalNetPnl));
    return sections;
  }

  private buildSymbolSections(
    trades: TradeRecord[],
    initialCapital?: number,
  ): SymbolSection[] {
    const groups = new Map<string, TradeRecord[]>();
    for (const t of trades) {
      if (!groups.has(t.symbol)) groups.set(t.symbol, []);
      groups.get(t.symbol)!.push(t);
    }

    const sections: SymbolSection[] = [...groups.entries()].map(([sym, trs]) => ({
      symbol: sym,
      metrics: computeMetrics(trs, initialCapital),
      tradeCount: trs.length,
    }));

    sections.sort((a, b) => Math.abs(b.metrics.totalNetPnl) - Math.abs(a.metrics.totalNetPnl));
    return sections;
  }
}
