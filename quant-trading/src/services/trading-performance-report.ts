/**
 * TradingPerformanceReport — unified, on-demand performance report service.
 *
 * Combines equity history, trade attribution, and risk metrics into a single
 * comprehensive report for stock traders. Covers use cases not addressed by
 * existing tools:
 *
 *   1. Equity curve summary with risk-adjusted metrics
 *   2. Win/loss streak analysis
 *   3. Period-over-period comparison (WoW, MoM)
 *   4. Capital utilization efficiency
 *   5. Holding period analysis
 *   6. Intraday vs swing classification
 *   7. Best/worst trade details
 */
import type { TradeRecord } from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Types ────────────────────────────────────────────────────

export interface StreakInfo {
  /** Current consecutive wins or losses (positive=wins, negative=losses) */
  currentStreak: number;
  currentStreakType: "win" | "loss" | "none";
  maxWinStreak: number;
  maxLossStreak: number;
  maxWinStreakPnl: number;
  maxLossStreakPnl: number;
}

export interface PeriodComparison {
  currentPeriod: string;
  previousPeriod: string;
  current: PeriodStats;
  previous: PeriodStats;
  delta: {
    pnlChange: number;
    tradeCountChange: number;
    winRateChange: number;
  };
}

export interface PeriodStats {
  netPnl: number;
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  avgWin: number;
  avgLoss: number;
}

export interface CapitalUtilization {
  /** Average percentage of capital deployed in positions */
  avgUtilizationPct: number;
  /** Peak utilization */
  maxUtilizationPct: number;
  /** Minimum utilization */
  minUtilizationPct: number;
  /** Number of days at >80% utilization */
  highUtilizationDays: number;
  /** Number of days at <20% utilization */
  lowUtilizationDays: number;
}

export interface HoldingAnalysis {
  avgHoldingDays: number;
  medianHoldingDays: number;
  /** Trades held <= 5 days */
  shortTermCount: number;
  shortTermPnl: number;
  shortTermWinRate: number;
  /** Trades held 6-20 days */
  mediumTermCount: number;
  mediumTermPnl: number;
  mediumTermWinRate: number;
  /** Trades held > 20 days */
  longTermCount: number;
  longTermPnl: number;
  longTermWinRate: number;
}

export interface TradeHighlight {
  symbol: string;
  name?: string;
  entryTime: string;
  exitTime: string;
  netPnl: number;
  returnPct: number;
  holdingDays: number;
  strategyId?: string;
}

export interface TradingPerformanceReportResult {
  reportDate: string;
  dateRange: { from: string; to: string };
  // ── Equity curve summary ──
  equity: {
    startEquity: number;
    endEquity: number;
    totalReturn: number;
    totalReturnPct: number;
    maxDrawdown: number;
    maxDrawdownPct: number;
    bestDay: { date: string; pnl: number } | null;
    worstDay: { date: string; pnl: number } | null;
    avgDailyPnl: number;
    winDays: number;
    lossDays: number;
    winDayRate: number;
    totalDays: number;
  };
  // ── Trade-level stats ──
  trades: {
    totalTrades: number;
    totalNetPnl: number;
    totalCommission: number;
    winRate: number;
    avgWin: number;
    avgLoss: number;
    profitFactor: number;
    avgTradesPerWeek: number;
    expectancy: number;
  };
  // ── Streak analysis ──
  streaks: StreakInfo;
  // ── Capital utilization ──
  capitalUtilization: CapitalUtilization;
  // ── Holding period analysis ──
  holdingAnalysis: HoldingAnalysis;
  // ── Best/worst trades ──
  bestTrades: TradeHighlight[];
  worstTrades: TradeHighlight[];
  // ── Period comparison ──
  weekOverWeek: PeriodComparison | null;
  monthOverMonth: PeriodComparison | null;
}

// ── Implementation ──────────────────────────────────────────

function computeStreaks(trades: TradeRecord[]): StreakInfo {
  const sorted = trades
    .filter((t) => t.exitTime != null)
    .sort((a, b) => (a.exitTime ?? "").localeCompare(b.exitTime ?? ""));

  let currentStreak = 0;
  let currentStreakType: "win" | "loss" | "none" = "none";
  let maxWinStreak = 0;
  let maxLossStreak = 0;
  let maxWinStreakPnl = 0;
  let maxLossStreakPnl = 0;

  let tempWinStreak = 0;
  let tempLossStreak = 0;
  let tempWinPnl = 0;
  let tempLossPnl = 0;

  for (const t of sorted) {
    if (t.netPnl > 0) {
      tempWinStreak++;
      tempWinPnl += t.netPnl;
      if (tempLossStreak > maxLossStreak) {
        maxLossStreak = tempLossStreak;
        maxLossStreakPnl = tempLossPnl;
      }
      tempLossStreak = 0;
      tempLossPnl = 0;
    } else {
      tempLossStreak++;
      tempLossPnl += t.netPnl;
      if (tempWinStreak > maxWinStreak) {
        maxWinStreak = tempWinStreak;
        maxWinStreakPnl = tempWinPnl;
      }
      tempWinStreak = 0;
      tempWinPnl = 0;
    }
  }

  // Final check
  if (tempWinStreak > maxWinStreak) {
    maxWinStreak = tempWinStreak;
    maxWinStreakPnl = tempWinPnl;
  }
  if (tempLossStreak > maxLossStreak) {
    maxLossStreak = tempLossStreak;
    maxLossStreakPnl = tempLossPnl;
  }

  // Current streak
  if (tempWinStreak > 0) {
    currentStreak = tempWinStreak;
    currentStreakType = "win";
  } else if (tempLossStreak > 0) {
    currentStreak = tempLossStreak;
    currentStreakType = "loss";
  }

  return {
    currentStreak,
    currentStreakType,
    maxWinStreak,
    maxLossStreak,
    maxWinStreakPnl,
    maxLossStreakPnl,
  };
}

function computeCapitalUtilization(snapshots: EquitySnapshot[]): CapitalUtilization {
  if (snapshots.length === 0) {
    return {
      avgUtilizationPct: 0,
      maxUtilizationPct: 0,
      minUtilizationPct: 0,
      highUtilizationDays: 0,
      lowUtilizationDays: 0,
    };
  }

  let totalUtil = 0;
  let maxUtil = 0;
  let minUtil = 100;
  let highDays = 0;
  let lowDays = 0;

  for (const snap of snapshots) {
    const util = snap.equity > 0 ? (snap.marketValue / snap.equity) * 100 : 0;
    totalUtil += util;
    if (util > maxUtil) maxUtil = util;
    if (util < minUtil) minUtil = util;
    if (util > 80) highDays++;
    if (util < 20) lowDays++;
  }

  return {
    avgUtilizationPct: totalUtil / snapshots.length,
    maxUtilizationPct: maxUtil,
    minUtilizationPct: minUtil,
    highUtilizationDays: highDays,
    lowUtilizationDays: lowDays,
  };
}

function computeHoldingAnalysis(trades: TradeRecord[]): HoldingAnalysis {
  const completed = trades.filter((t) => t.exitTime != null && t.holdingDays != null);

  if (completed.length === 0) {
    return {
      avgHoldingDays: 0,
      medianHoldingDays: 0,
      shortTermCount: 0,
      shortTermPnl: 0,
      shortTermWinRate: 0,
      mediumTermCount: 0,
      mediumTermPnl: 0,
      mediumTermWinRate: 0,
      longTermCount: 0,
      longTermPnl: 0,
      longTermWinRate: 0,
    };
  }

  const days = completed.map((t) => t.holdingDays!).sort((a, b) => a - b);
  const avg = days.reduce((s, d) => s + d, 0) / days.length;
  const median = days[Math.floor(days.length / 2)]!;

  const shortTerm = completed.filter((t) => t.holdingDays! <= 5);
  const mediumTerm = completed.filter((t) => t.holdingDays! > 5 && t.holdingDays! <= 20);
  const longTerm = completed.filter((t) => t.holdingDays! > 20);

  const winRate = (arr: TradeRecord[]) =>
    arr.length > 0 ? arr.filter((t) => t.netPnl > 0).length / arr.length : 0;
  const totalPnl = (arr: TradeRecord[]) => arr.reduce((s, t) => s + t.netPnl, 0);

  return {
    avgHoldingDays: avg,
    medianHoldingDays: median,
    shortTermCount: shortTerm.length,
    shortTermPnl: totalPnl(shortTerm),
    shortTermWinRate: winRate(shortTerm),
    mediumTermCount: mediumTerm.length,
    mediumTermPnl: totalPnl(mediumTerm),
    mediumTermWinRate: winRate(mediumTerm),
    longTermCount: longTerm.length,
    longTermPnl: totalPnl(longTerm),
    longTermWinRate: winRate(longTerm),
  };
}

function computePeriodComparison(
  trades: TradeRecord[],
  periodType: "week" | "month",
): PeriodComparison | null {
  const now = new Date();
  let currentStart: Date;
  let previousStart: Date;
  let previousEnd: Date;
  let currentLabel: string;
  let previousLabel: string;

  if (periodType === "week") {
    // Current week: Monday of this week
    const dayOfWeek = now.getDay() || 7; // Sunday = 7
    currentStart = new Date(now);
    currentStart.setDate(now.getDate() - dayOfWeek + 1);
    currentStart.setHours(0, 0, 0, 0);

    previousStart = new Date(currentStart);
    previousStart.setDate(previousStart.getDate() - 7);
    previousEnd = new Date(currentStart);
    previousEnd.setMilliseconds(-1);

    currentLabel = `本周 (${currentStart.toISOString().slice(0, 10)}~)`;
    previousLabel = `上周 (${previousStart.toISOString().slice(0, 10)}~${previousEnd.toISOString().slice(0, 10)})`;
  } else {
    // Current month
    currentStart = new Date(now.getFullYear(), now.getMonth(), 1);
    previousStart = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    previousEnd = new Date(currentStart);
    previousEnd.setMilliseconds(-1);

    currentLabel = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
    previousLabel = `${previousStart.getFullYear()}-${String(previousStart.getMonth() + 1).padStart(2, "0")}`;
  }

  const currentStartStr = currentStart.toISOString().slice(0, 10);
  const previousStartStr = previousStart.toISOString().slice(0, 10);
  const previousEndStr = previousEnd.toISOString().slice(0, 10);

  const currentTrades = trades.filter(
    (t) => t.exitTime && t.exitTime.slice(0, 10) >= currentStartStr,
  );
  const previousTrades = trades.filter(
    (t) =>
      t.exitTime &&
      t.exitTime.slice(0, 10) >= previousStartStr &&
      t.exitTime.slice(0, 10) <= previousEndStr,
  );

  if (currentTrades.length === 0 && previousTrades.length === 0) return null;

  const buildStats = (arr: TradeRecord[]): PeriodStats => {
    const wins = arr.filter((t) => t.netPnl > 0);
    const losses = arr.filter((t) => t.netPnl <= 0);
    const totalWin = wins.reduce((s, t) => s + t.netPnl, 0);
    const totalLoss = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));
    return {
      netPnl: arr.reduce((s, t) => s + t.netPnl, 0),
      trades: arr.length,
      wins: wins.length,
      losses: losses.length,
      winRate: arr.length > 0 ? wins.length / arr.length : 0,
      avgWin: wins.length > 0 ? totalWin / wins.length : 0,
      avgLoss: losses.length > 0 ? totalLoss / losses.length : 0,
    };
  };

  const current = buildStats(currentTrades);
  const previous = buildStats(previousTrades);

  return {
    currentPeriod: currentLabel,
    previousPeriod: previousLabel,
    current,
    previous,
    delta: {
      pnlChange: current.netPnl - previous.netPnl,
      tradeCountChange: current.trades - previous.trades,
      winRateChange: current.winRate - previous.winRate,
    },
  };
}

function buildTradeHighlight(t: TradeRecord): TradeHighlight {
  const returnPct =
    t.entryPrice > 0 ? ((t.exitPrice ?? t.entryPrice) / t.entryPrice - 1) * 100 : 0;
  return {
    symbol: t.symbol,
    name: t.name,
    entryTime: t.entryTime,
    exitTime: t.exitTime ?? "",
    netPnl: t.netPnl,
    returnPct,
    holdingDays: t.holdingDays ?? 0,
    strategyId: t.strategyId,
  };
}

/**
 * Generate a comprehensive trading performance report.
 */
export function generatePerformanceReport(
  trades: TradeRecord[],
  equitySnapshots: EquitySnapshot[],
  options?: { from?: string; to?: string; topN?: number },
): TradingPerformanceReportResult {
  const from = options?.from;
  const to = options?.to;
  const topN = options?.topN ?? 5;

  // Filter trades by date range
  let filteredTrades = trades.filter((t) => t.exitTime != null);
  if (from) filteredTrades = filteredTrades.filter((t) => (t.exitTime ?? "") >= from);
  if (to) filteredTrades = filteredTrades.filter((t) => (t.exitTime ?? "") <= to);
  filteredTrades.sort((a, b) => (a.exitTime ?? "").localeCompare(b.exitTime ?? ""));

  // Filter snapshots
  let filteredSnapshots = [...equitySnapshots].sort((a, b) => a.date.localeCompare(b.date));
  if (from) filteredSnapshots = filteredSnapshots.filter((s) => s.date >= from);
  if (to) filteredSnapshots = filteredSnapshots.filter((s) => s.date <= to);

  // ── Equity summary ──
  const firstSnap = filteredSnapshots[0];
  const lastSnap = filteredSnapshots[filteredSnapshots.length - 1];
  const startEquity = firstSnap?.equity ?? 0;
  const endEquity = lastSnap?.equity ?? 0;
  const totalReturn = endEquity - startEquity;
  const totalReturnPct = startEquity > 0 ? (totalReturn / startEquity) * 100 : 0;

  let peak = -Infinity;
  let maxDrawdown = 0;
  let maxDrawdownPct = 0;
  for (const s of filteredSnapshots) {
    if (s.equity > peak) peak = s.equity;
    const dd = peak - s.equity;
    if (dd > maxDrawdown) {
      maxDrawdown = dd;
      maxDrawdownPct = peak > 0 ? (dd / peak) * 100 : 0;
    }
  }

  let bestDay: { date: string; pnl: number } | null = null;
  let worstDay: { date: string; pnl: number } | null = null;
  let totalDailyPnl = 0;
  let winDays = 0;
  let lossDays = 0;

  for (const s of filteredSnapshots) {
    totalDailyPnl += s.dailyPnl;
    if (!bestDay || s.dailyPnl > bestDay.pnl) bestDay = { date: s.date, pnl: s.dailyPnl };
    if (!worstDay || s.dailyPnl < worstDay.pnl) worstDay = { date: s.date, pnl: s.dailyPnl };
    if (s.dailyPnl > 0) winDays++;
    else if (s.dailyPnl < 0) lossDays++;
  }

  // ── Trade-level stats ──
  const wins = filteredTrades.filter((t) => t.netPnl > 0);
  const losses = filteredTrades.filter((t) => t.netPnl <= 0);
  const totalNetPnl = filteredTrades.reduce((s, t) => s + t.netPnl, 0);
  const totalCommission = filteredTrades.reduce((s, t) => s + t.commission, 0);
  const totalWinAmount = wins.reduce((s, t) => s + t.netPnl, 0);
  const totalLossAmount = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));
  const winRate = filteredTrades.length > 0 ? wins.length / filteredTrades.length : 0;
  const avgWin = wins.length > 0 ? totalWinAmount / wins.length : 0;
  const avgLoss = losses.length > 0 ? totalLossAmount / losses.length : 0;
  const profitFactor =
    totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount > 0 ? Infinity : 0;

  // Avg trades per week
  const tradeDates = filteredTrades.map((t) => t.exitTime!.slice(0, 10));
  const uniqueDays = new Set(tradeDates).size;
  const tradingWeeks = Math.max(1, uniqueDays / 5);
  const avgTradesPerWeek = filteredTrades.length / tradingWeeks;

  // Expectancy = (winRate * avgWin) - (lossRate * avgLoss)
  const expectancy = winRate * avgWin - (1 - winRate) * avgLoss;

  // ── Best/Worst trades ──
  const sortedByPnl = [...filteredTrades].sort((a, b) => b.netPnl - a.netPnl);
  const bestTrades = sortedByPnl.slice(0, topN).map(buildTradeHighlight);
  const worstTrades = sortedByPnl
    .slice(-topN)
    .reverse()
    .map(buildTradeHighlight);

  // ── Date range ──
  const dates = filteredTrades.map((t) => t.exitTime!.slice(0, 10));
  const snapDates = filteredSnapshots.map((s) => s.date);
  const allDates = [...dates, ...snapDates].filter(Boolean).sort();
  const rangeFrom = allDates[0] ?? "";
  const rangeTo = allDates[allDates.length - 1] ?? "";

  return {
    reportDate: new Date().toISOString().slice(0, 10),
    dateRange: { from: rangeFrom, to: rangeTo },
    equity: {
      startEquity,
      endEquity,
      totalReturn,
      totalReturnPct,
      maxDrawdown,
      maxDrawdownPct,
      bestDay,
      worstDay,
      avgDailyPnl: filteredSnapshots.length > 0 ? totalDailyPnl / filteredSnapshots.length : 0,
      winDays,
      lossDays,
      winDayRate: winDays + lossDays > 0 ? (winDays / (winDays + lossDays)) * 100 : 0,
      totalDays: filteredSnapshots.length,
    },
    trades: {
      totalTrades: filteredTrades.length,
      totalNetPnl,
      totalCommission,
      winRate,
      avgWin,
      avgLoss,
      profitFactor,
      avgTradesPerWeek,
      expectancy,
    },
    streaks: computeStreaks(filteredTrades),
    capitalUtilization: computeCapitalUtilization(filteredSnapshots),
    holdingAnalysis: computeHoldingAnalysis(filteredTrades),
    bestTrades,
    worstTrades,
    weekOverWeek: computePeriodComparison(filteredTrades, "week"),
    monthOverMonth: computePeriodComparison(filteredTrades, "month"),
  };
}
