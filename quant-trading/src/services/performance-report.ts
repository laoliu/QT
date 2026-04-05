/**
 * PerformanceReportService — generate comprehensive trading performance reports.
 *
 * Aggregates trade records, equity snapshots, and benchmark data to produce:
 *  - Core metrics (return, drawdown, Sharpe, Sortino, win rate, profit factor)
 *  - Period returns (daily/weekly/monthly/quarterly/yearly)
 *  - Attribution (by sector, symbol, strategy)
 *  - Equity and drawdown curves
 */
import type { TradeRecord } from "../types.js";

// ── Types ────────────────────────────────────────────────────

export interface ReportRequest {
  accountId?: string;
  dateFrom: string;
  dateTo: string;
  benchmark?: string;
  groupBy?: "day" | "week" | "month" | "quarter" | "year";
  includeTradeDetail?: boolean;
}

export interface PeriodReturn {
  period: string;
  return_pct: number;
  pnl: number;
  trades: number;
}

export interface SectorAttribution {
  sector: string;
  contribution: number;
  weight: number;
}

export interface SymbolAttribution {
  symbol: string;
  name: string;
  contribution: number;
  trades: number;
}

export interface StrategyAttribution {
  strategyId: string;
  contribution: number;
  trades: number;
}

export interface PerformanceReport {
  meta: { accountId: string; dateFrom: string; dateTo: string; generatedAt: string };
  summary: {
    totalReturn: number;
    annualizedReturn: number;
    maxDrawdown: number;
    maxDrawdownDuration: number;
    sharpeRatio: number;
    sortinoRatio: number;
    calmarRatio: number;
    winRate: number;
    profitFactor: number;
    avgHoldingDays: number;
    turnoverRate: number;
    maxSingleProfit: number;
    maxSingleLoss: number;
    totalTrades: number;
    totalCommission: number;
  };
  periodReturns: PeriodReturn[];
  attribution: {
    bySector: SectorAttribution[];
    bySymbol: SymbolAttribution[];
    byStrategy: StrategyAttribution[];
  };
  equityCurve: { date: string; equity: number }[];
  drawdownCurve: { date: string; drawdown: number }[];
  benchmarkCurve?: { date: string; value: number }[];
}

// ── Data source interface (duck-typed) ──────────────────────

export interface ReportDataSource {
  getTrades(accountId?: string, dateFrom?: string, dateTo?: string): Promise<TradeRecord[]>;
  getEquityCurve(accountId?: string, dateFrom?: string, dateTo?: string): Promise<{ date: string; equity: number }[]>;
  getBenchmarkCurve?(benchmark: string, dateFrom: string, dateTo: string): Promise<{ date: string; value: number }[]>;
  getSectorForSymbol?(symbol: string): string;
}

// ── Calculation helpers ─────────────────────────────────────

export function calcMaxDrawdown(equityCurve: { date: string; equity: number }[]): { maxDrawdown: number; maxDrawdownDuration: number } {
  if (equityCurve.length === 0) return { maxDrawdown: 0, maxDrawdownDuration: 0 };

  let peak = equityCurve[0].equity;
  let maxDd = 0;
  let maxDdDuration = 0;
  let currentDdStart = 0;

  for (let i = 0; i < equityCurve.length; i++) {
    const eq = equityCurve[i].equity;
    if (eq > peak) {
      peak = eq;
      currentDdStart = i;
    }
    const dd = (peak - eq) / peak;
    if (dd > maxDd) {
      maxDd = dd;
      maxDdDuration = i - currentDdStart;
    }
  }

  return { maxDrawdown: maxDd, maxDrawdownDuration: maxDdDuration };
}

export function calcSharpeRatio(returns: number[], riskFreeRate = 0.03): number {
  if (returns.length < 2) return 0;
  const dailyRf = riskFreeRate / 252;
  const excessReturns = returns.map((r) => r - dailyRf);
  const mean = excessReturns.reduce((a, b) => a + b, 0) / excessReturns.length;
  const variance = excessReturns.reduce((a, r) => a + (r - mean) ** 2, 0) / (excessReturns.length - 1);
  const std = Math.sqrt(variance);
  if (std === 0) return 0;
  return (mean / std) * Math.sqrt(252);
}

export function calcSortinoRatio(returns: number[], riskFreeRate = 0.03): number {
  if (returns.length < 2) return 0;
  const dailyRf = riskFreeRate / 252;
  const excessReturns = returns.map((r) => r - dailyRf);
  const mean = excessReturns.reduce((a, b) => a + b, 0) / excessReturns.length;
  const downside = excessReturns.filter((r) => r < 0);
  if (downside.length === 0) return mean > 0 ? Infinity : 0;
  const downsideVariance = downside.reduce((a, r) => a + r ** 2, 0) / downside.length;
  const downsideStd = Math.sqrt(downsideVariance);
  if (downsideStd === 0) return 0;
  return (mean / downsideStd) * Math.sqrt(252);
}

function groupByPeriod(trades: TradeRecord[], groupBy: string): PeriodReturn[] {
  const groups = new Map<string, { pnl: number; trades: number }>();

  for (const t of trades) {
    const date = t.exitTime ?? t.entryTime;
    let key: string;
    switch (groupBy) {
      case "day":
        key = date.slice(0, 10);
        break;
      case "week": {
        const d = new Date(date);
        const dayNum = d.getUTCDay() || 7;
        d.setUTCDate(d.getUTCDate() + 4 - dayNum);
        const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
        const weekNo = Math.ceil(((d.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
        key = `${d.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
        break;
      }
      case "month":
        key = date.slice(0, 7);
        break;
      case "quarter": {
        const m = parseInt(date.slice(5, 7), 10);
        key = `${date.slice(0, 4)}-Q${Math.ceil(m / 3)}`;
        break;
      }
      case "year":
        key = date.slice(0, 4);
        break;
      default:
        key = date.slice(0, 7);
    }

    const existing = groups.get(key) ?? { pnl: 0, trades: 0 };
    existing.pnl += t.netPnl;
    existing.trades++;
    groups.set(key, existing);
  }

  const sorted = [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  const totalPnl = trades.reduce((sum, t) => sum + t.netPnl, 0);
  return sorted.map(([period, data]) => ({
    period,
    return_pct: totalPnl !== 0 ? data.pnl / Math.abs(totalPnl) : 0,
    pnl: data.pnl,
    trades: data.trades,
  }));
}

// ── Service ─────────────────────────────────────────────────

function escapeHtml(str: string): string {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

export class PerformanceReportService {
  private dataSource: ReportDataSource;

  constructor(dataSource: ReportDataSource) {
    this.dataSource = dataSource;
  }

  async generate(req: ReportRequest): Promise<PerformanceReport> {
    const trades = await this.dataSource.getTrades(req.accountId, req.dateFrom, req.dateTo);
    const equityCurve = await this.dataSource.getEquityCurve(req.accountId, req.dateFrom, req.dateTo);

    // Core metrics
    const closedTrades = trades.filter((t) => t.exitTime);
    const wins = closedTrades.filter((t) => t.netPnl > 0);
    const losses = closedTrades.filter((t) => t.netPnl <= 0);
    const totalNetPnl = closedTrades.reduce((sum, t) => sum + t.netPnl, 0);
    const totalGrossWin = wins.reduce((sum, t) => sum + t.netPnl, 0);
    const totalGrossLoss = Math.abs(losses.reduce((sum, t) => sum + t.netPnl, 0));
    const totalCommission = trades.reduce((sum, t) => sum + t.commission, 0);

    const initialEquity = equityCurve.length > 0 ? equityCurve[0].equity : 100000;
    const finalEquity = equityCurve.length > 0 ? equityCurve[equityCurve.length - 1].equity : initialEquity;
    const totalReturn = (finalEquity - initialEquity) / initialEquity;

    const daysDiff = equityCurve.length > 1
      ? (new Date(equityCurve[equityCurve.length - 1].date).getTime() - new Date(equityCurve[0].date).getTime()) / 86400000
      : 365;
    const annualizedReturn = Math.pow(1 + totalReturn, 365 / Math.max(daysDiff, 1)) - 1;

    // Drawdown
    const { maxDrawdown, maxDrawdownDuration } = calcMaxDrawdown(equityCurve);
    const drawdownCurve = calcDrawdownCurve(equityCurve);

    // Daily returns for Sharpe/Sortino
    const dailyReturns: number[] = [];
    for (let i = 1; i < equityCurve.length; i++) {
      dailyReturns.push((equityCurve[i].equity - equityCurve[i - 1].equity) / equityCurve[i - 1].equity);
    }

    const sharpeRatio = calcSharpeRatio(dailyReturns);
    const sortinoRatio = calcSortinoRatio(dailyReturns);
    const calmarRatio = maxDrawdown > 0 ? annualizedReturn / maxDrawdown : 0;

    const avgHoldingDays = closedTrades.length > 0
      ? closedTrades.reduce((sum, t) => sum + (t.holdingDays ?? 0), 0) / closedTrades.length
      : 0;

    // Attribution
    const bySymbol = computeSymbolAttribution(closedTrades, totalNetPnl);
    const byStrategy = computeStrategyAttribution(closedTrades, totalNetPnl);
    const bySector = computeSectorAttribution(closedTrades, totalNetPnl, this.dataSource.getSectorForSymbol);

    // Period returns
    const periodReturns = groupByPeriod(closedTrades, req.groupBy ?? "month");

    // Benchmark
    let benchmarkCurve: { date: string; value: number }[] | undefined;
    if (req.benchmark && this.dataSource.getBenchmarkCurve) {
      benchmarkCurve = await this.dataSource.getBenchmarkCurve(req.benchmark, req.dateFrom, req.dateTo);
    }

    return {
      meta: {
        accountId: req.accountId ?? "default",
        dateFrom: req.dateFrom,
        dateTo: req.dateTo,
        generatedAt: new Date().toISOString(),
      },
      summary: {
        totalReturn,
        annualizedReturn,
        maxDrawdown,
        maxDrawdownDuration,
        sharpeRatio,
        sortinoRatio,
        calmarRatio,
        winRate: closedTrades.length > 0 ? wins.length / closedTrades.length : 0,
        profitFactor: totalGrossLoss > 0 ? totalGrossWin / totalGrossLoss : totalGrossWin > 0 ? Infinity : 0,
        avgHoldingDays,
        turnoverRate: 0, // TODO: requires position snapshot data
        maxSingleProfit: closedTrades.length > 0 ? Math.max(...closedTrades.map((t) => t.netPnl)) : 0,
        maxSingleLoss: closedTrades.length > 0 ? Math.min(...closedTrades.map((t) => t.netPnl)) : 0,
        totalTrades: closedTrades.length,
        totalCommission,
      },
      periodReturns,
      attribution: { bySector, bySymbol, byStrategy },
      equityCurve,
      drawdownCurve,
      benchmarkCurve,
    };
  }

  renderHtml(report: PerformanceReport): string {
    const s = report.summary;
    return `<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Performance Report</title>
<style>body{font-family:sans-serif;max-width:900px;margin:0 auto;padding:20px}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;text-align:right}
th{background:#f5f5f5;text-align:left}.metric{display:inline-block;min-width:200px;margin:10px}
.positive{color:#2e7d32}.negative{color:#c62828}</style></head><body>
<h1>Trading Performance Report</h1>
<p>Account: ${escapeHtml(report.meta.accountId)} | Period: ${escapeHtml(report.meta.dateFrom)} ~ ${escapeHtml(report.meta.dateTo)}</p>
<h2>Summary</h2>
<div class="metric">Total Return: <b class="${s.totalReturn >= 0 ? "positive" : "negative"}">${(s.totalReturn * 100).toFixed(2)}%</b></div>
<div class="metric">Annualized: <b>${(s.annualizedReturn * 100).toFixed(2)}%</b></div>
<div class="metric">Max Drawdown: <b class="negative">${(s.maxDrawdown * 100).toFixed(2)}%</b></div>
<div class="metric">Sharpe Ratio: <b>${s.sharpeRatio.toFixed(2)}</b></div>
<div class="metric">Sortino Ratio: <b>${s.sortinoRatio.toFixed(2)}</b></div>
<div class="metric">Win Rate: <b>${(s.winRate * 100).toFixed(1)}%</b></div>
<div class="metric">Profit Factor: <b>${s.profitFactor === Infinity ? "∞" : s.profitFactor.toFixed(2)}</b></div>
<div class="metric">Total Trades: <b>${s.totalTrades}</b></div>
<div class="metric">Avg Holding Days: <b>${s.avgHoldingDays.toFixed(1)}</b></div>
<div class="metric">Max Single Profit: <b class="positive">${s.maxSingleProfit.toFixed(2)}</b></div>
<div class="metric">Max Single Loss: <b class="negative">${s.maxSingleLoss.toFixed(2)}</b></div>
<div class="metric">Total Commission: <b>${s.totalCommission.toFixed(2)}</b></div>
<h2>Attribution by Symbol</h2>
<table><tr><th>Symbol</th><th>Name</th><th>Contribution</th><th>Trades</th></tr>
${report.attribution.bySymbol.map((a) => `<tr><td>${escapeHtml(a.symbol)}</td><td>${escapeHtml(a.name)}</td><td>${(a.contribution * 100).toFixed(2)}%</td><td>${a.trades}</td></tr>`).join("\n")}
</table>
<h2>Attribution by Strategy</h2>
<table><tr><th>Strategy</th><th>Contribution</th><th>Trades</th></tr>
${report.attribution.byStrategy.map((a) => `<tr><td>${escapeHtml(a.strategyId)}</td><td>${(a.contribution * 100).toFixed(2)}%</td><td>${a.trades}</td></tr>`).join("\n")}
</table>
<p><small>Generated at ${report.meta.generatedAt}</small></p>
</body></html>`;
  }
}

// ── Attribution helpers ─────────────────────────────────────

function computeSymbolAttribution(trades: TradeRecord[], totalPnl: number): SymbolAttribution[] {
  const map = new Map<string, { name: string; pnl: number; trades: number }>();
  for (const t of trades) {
    const existing = map.get(t.symbol) ?? { name: t.name ?? t.symbol, pnl: 0, trades: 0 };
    existing.pnl += t.netPnl;
    existing.trades++;
    map.set(t.symbol, existing);
  }
  return [...map.entries()].map(([symbol, data]) => ({
    symbol,
    name: data.name,
    contribution: totalPnl !== 0 ? data.pnl / Math.abs(totalPnl) : 0,
    trades: data.trades,
  }));
}

function computeStrategyAttribution(trades: TradeRecord[], totalPnl: number): StrategyAttribution[] {
  const map = new Map<string, { pnl: number; trades: number }>();
  for (const t of trades) {
    const key = t.strategyId ?? "manual";
    const existing = map.get(key) ?? { pnl: 0, trades: 0 };
    existing.pnl += t.netPnl;
    existing.trades++;
    map.set(key, existing);
  }
  return [...map.entries()].map(([strategyId, data]) => ({
    strategyId,
    contribution: totalPnl !== 0 ? data.pnl / Math.abs(totalPnl) : 0,
    trades: data.trades,
  }));
}

function computeSectorAttribution(
  trades: TradeRecord[],
  totalPnl: number,
  getSector?: (symbol: string) => string,
): SectorAttribution[] {
  const map = new Map<string, { pnl: number; count: number }>();
  const totalCount = trades.length;
  for (const t of trades) {
    const sector = getSector ? getSector(t.symbol) : "unknown";
    const existing = map.get(sector) ?? { pnl: 0, count: 0 };
    existing.pnl += t.netPnl;
    existing.count++;
    map.set(sector, existing);
  }
  return [...map.entries()].map(([sector, data]) => ({
    sector,
    contribution: totalPnl !== 0 ? data.pnl / Math.abs(totalPnl) : 0,
    weight: totalCount > 0 ? data.count / totalCount : 0,
  }));
}

function calcDrawdownCurve(equityCurve: { date: string; equity: number }[]): { date: string; drawdown: number }[] {
  if (equityCurve.length === 0) return [];
  let peak = equityCurve[0].equity;
  return equityCurve.map((p) => {
    if (p.equity > peak) peak = p.equity;
    return { date: p.date, drawdown: peak > 0 ? (peak - p.equity) / peak : 0 };
  });
}
