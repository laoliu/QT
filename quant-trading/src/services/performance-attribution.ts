/**
 * PerformanceAttribution — analyzes P&L sources.
 *
 * Breaks down returns by:
 *   - Strategy: which strategy contributed how much
 *   - Symbol: which symbols were most profitable / loss-making
 *   - Time period: daily / weekly / monthly P&L breakdown
 *   - Win/loss analysis: average win size vs average loss size
 *
 * Reads from the TradeRecord store (orders.json / trades.json).
 */
import type { TradeRecord } from "../types.js";

export interface StrategyAttribution {
  strategyId: string;
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  grossPnl: number;
  netPnl: number;
  avgWin: number;
  avgLoss: number;
  largestWin: number;
  largestLoss: number;
  profitFactor: number; // totalWins / totalLosses
  /** Percentage contribution to total portfolio return. */
  contributionPct: number;
}

export interface SymbolAttribution {
  symbol: string;
  trades: number;
  netPnl: number;
  avgPnl: number;
  winRate: number;
  holdingDaysAvg: number;
  contributionPct: number;
}

export interface PeriodPnl {
  period: string; // YYYY-MM-DD, YYYY-WW, or YYYY-MM
  trades: number;
  netPnl: number;
  cumPnl: number;
  wins: number;
  losses: number;
}

export interface AttributionResult {
  dateRange: { from: string; to: string };
  totalTrades: number;
  totalNetPnl: number;
  totalGrossPnl: number;
  totalCommission: number;
  overallWinRate: number;
  avgWin: number;
  avgLoss: number;
  profitFactor: number;
  byStrategy: StrategyAttribution[];
  bySymbol: SymbolAttribution[];
  byDay: PeriodPnl[];
  byWeek: PeriodPnl[];
  byMonth: PeriodPnl[];
}

/** Get ISO week string YYYY-WNN */
function isoWeek(dateStr: string): string {
  const d = new Date(dateStr);
  const dayNum = d.getUTCDay() || 7;
  d.setUTCDate(d.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${String(d.getUTCFullYear())}-W${String(weekNo).padStart(2, "0")}`;
}

export function computeAttribution(
  trades: TradeRecord[],
  options?: { from?: string; to?: string },
): AttributionResult {
  // Filter by date range
  let filtered = trades.filter((t) => t.exitTime != null);
  if (options?.from) {
    filtered = filtered.filter((t) => (t.exitTime ?? "") >= options.from!);
  }
  if (options?.to) {
    filtered = filtered.filter((t) => (t.exitTime ?? "") <= options.to!);
  }

  // Sort by exit time
  filtered.sort((a, b) => (a.exitTime ?? "").localeCompare(b.exitTime ?? ""));

  const totalNetPnl = filtered.reduce((s, t) => s + t.netPnl, 0);
  const totalGrossPnl = filtered.reduce((s, t) => s + t.grossPnl, 0);
  const totalCommission = filtered.reduce((s, t) => s + t.commission, 0);

  const wins = filtered.filter((t) => t.netPnl > 0);
  const losses = filtered.filter((t) => t.netPnl <= 0);

  const totalWinAmount = wins.reduce((s, t) => s + t.netPnl, 0);
  const totalLossAmount = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));

  // ── By Strategy ──

  const stratMap = new Map<
    string,
    { trades: TradeRecord[]; wins: number; losses: number; grossPnl: number; netPnl: number }
  >();
  for (const t of filtered) {
    const sid = t.strategyId ?? "unknown";
    if (!stratMap.has(sid)) {
      stratMap.set(sid, { trades: [], wins: 0, losses: 0, grossPnl: 0, netPnl: 0 });
    }
    const s = stratMap.get(sid)!;
    s.trades.push(t);
    if (t.netPnl > 0) s.wins++;
    else s.losses++;
    s.grossPnl += t.grossPnl;
    s.netPnl += t.netPnl;
  }

  const byStrategy: StrategyAttribution[] = [...stratMap.entries()].map(([sid, s]) => {
    const sWins = s.trades.filter((t) => t.netPnl > 0);
    const sLosses = s.trades.filter((t) => t.netPnl <= 0);
    const sWinTotal = sWins.reduce((sum, t) => sum + t.netPnl, 0);
    const sLossTotal = Math.abs(sLosses.reduce((sum, t) => sum + t.netPnl, 0));
    return {
      strategyId: sid,
      trades: s.trades.length,
      wins: s.wins,
      losses: s.losses,
      winRate: s.trades.length > 0 ? s.wins / s.trades.length : 0,
      grossPnl: s.grossPnl,
      netPnl: s.netPnl,
      avgWin: sWins.length > 0 ? sWinTotal / sWins.length : 0,
      avgLoss: sLosses.length > 0 ? sLossTotal / sLosses.length : 0,
      largestWin: sWins.length > 0 ? Math.max(...sWins.map((t) => t.netPnl)) : 0,
      largestLoss: sLosses.length > 0 ? Math.min(...sLosses.map((t) => t.netPnl)) : 0,
      profitFactor: sLossTotal > 0 ? sWinTotal / sLossTotal : sWinTotal > 0 ? Infinity : 0,
      contributionPct: totalNetPnl !== 0 ? (s.netPnl / Math.abs(totalNetPnl)) * 100 : 0,
    };
  });

  // Sort by absolute PnL contribution desc
  byStrategy.sort((a, b) => Math.abs(b.netPnl) - Math.abs(a.netPnl));

  // ── By Symbol ──

  const symMap = new Map<
    string,
    { trades: TradeRecord[]; netPnl: number; wins: number; holdingDaysTotal: number }
  >();
  for (const t of filtered) {
    if (!symMap.has(t.symbol)) {
      symMap.set(t.symbol, { trades: [], netPnl: 0, wins: 0, holdingDaysTotal: 0 });
    }
    const s = symMap.get(t.symbol)!;
    s.trades.push(t);
    s.netPnl += t.netPnl;
    if (t.netPnl > 0) s.wins++;
    s.holdingDaysTotal += t.holdingDays ?? 0;
  }

  const bySymbol: SymbolAttribution[] = [...symMap.entries()].map(([sym, s]) => ({
    symbol: sym,
    trades: s.trades.length,
    netPnl: s.netPnl,
    avgPnl: s.trades.length > 0 ? s.netPnl / s.trades.length : 0,
    winRate: s.trades.length > 0 ? s.wins / s.trades.length : 0,
    holdingDaysAvg: s.trades.length > 0 ? s.holdingDaysTotal / s.trades.length : 0,
    contributionPct: totalNetPnl !== 0 ? (s.netPnl / Math.abs(totalNetPnl)) * 100 : 0,
  }));
  bySymbol.sort((a, b) => b.netPnl - a.netPnl);

  // ── By Period ──

  function buildPeriodPnl(keyFn: (t: TradeRecord) => string): PeriodPnl[] {
    const map = new Map<string, { trades: number; netPnl: number; wins: number; losses: number }>();
    for (const t of filtered) {
      const key = keyFn(t);
      if (!map.has(key)) map.set(key, { trades: 0, netPnl: 0, wins: 0, losses: 0 });
      const p = map.get(key)!;
      p.trades++;
      p.netPnl += t.netPnl;
      if (t.netPnl > 0) p.wins++;
      else p.losses++;
    }

    const sorted = [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    let cum = 0;
    return sorted.map(([period, p]) => {
      cum += p.netPnl;
      return {
        period,
        trades: p.trades,
        netPnl: p.netPnl,
        cumPnl: cum,
        wins: p.wins,
        losses: p.losses,
      };
    });
  }

  const byDay = buildPeriodPnl((t) => (t.exitTime ?? "").slice(0, 10));
  const byWeek = buildPeriodPnl((t) => isoWeek(t.exitTime ?? ""));
  const byMonth = buildPeriodPnl((t) => (t.exitTime ?? "").slice(0, 7));

  // ── Date range ──

  const dates = filtered.map((t) => t.exitTime ?? "").filter(Boolean);
  const from = dates[0]?.slice(0, 10) ?? "";
  const to = dates[dates.length - 1]?.slice(0, 10) ?? "";

  return {
    dateRange: { from, to },
    totalTrades: filtered.length,
    totalNetPnl,
    totalGrossPnl,
    totalCommission,
    overallWinRate: filtered.length > 0 ? wins.length / filtered.length : 0,
    avgWin: wins.length > 0 ? totalWinAmount / wins.length : 0,
    avgLoss: losses.length > 0 ? totalLossAmount / losses.length : 0,
    profitFactor:
      totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount > 0 ? Infinity : 0,
    byStrategy,
    bySymbol,
    byDay,
    byWeek,
    byMonth,
  };
}
