/**
 * TradingPerformanceReportService — generates comprehensive trading performance reports.
 *
 * Aggregates data from:
 *   - TradeRecord store (orders/trades JSON files)
 *   - EquityHistory (daily equity snapshots)
 *
 * Supports filtering by:
 *   - Account mode (paper / live)
 *   - Strategy IDs
 *   - Symbol codes
 *   - Date range
 *
 * Computes risk-adjusted metrics (Sharpe, Sortino, Calmar, VaR, etc.)
 * from equity curve data, and trade-level statistics from TradeRecords.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { EquitySnapshot } from "./equity-history.js";
import type {
  EquityCurvePoint,
  PerformanceMetricEntry,
  PerformanceMetricId,
  PerformanceReportQuery,
  QuantTradingConfig,
  TradeAggregation,
  TradeRecord,
  TradingPerformanceReport,
} from "../types.js";

// ── Constants ───────────────────────────────────────────────────

/** Annualization factor for daily data (~242 A-share trading days). */
const TRADING_DAYS_PER_YEAR = 242;

/** Risk-free rate for Sharpe / Sortino (annual, default 2%). */
const RISK_FREE_RATE = 0.02;

/** Daily risk-free rate. */
const DAILY_RF = RISK_FREE_RATE / TRADING_DAYS_PER_YEAR;

// ── Metric labels (Chinese) ─────────────────────────────────────

const METRIC_LABELS: Record<PerformanceMetricId, { label: string; unit: PerformanceMetricEntry["unit"] }> = {
  total_return: { label: "总收益率", unit: "pct" },
  annualized_return: { label: "年化收益率", unit: "pct" },
  total_pnl: { label: "总盈亏", unit: "currency" },
  total_commission: { label: "总手续费", unit: "currency" },
  net_pnl: { label: "净盈亏", unit: "currency" },
  sharpe_ratio: { label: "夏普比率", unit: "ratio" },
  sortino_ratio: { label: "索提诺比率", unit: "ratio" },
  calmar_ratio: { label: "卡玛比率", unit: "ratio" },
  max_drawdown: { label: "最大回撤", unit: "currency" },
  max_drawdown_pct: { label: "最大回撤率", unit: "pct" },
  max_drawdown_duration: { label: "最大回撤持续天数", unit: "days" },
  volatility: { label: "年化波动率", unit: "pct" },
  win_rate: { label: "胜率", unit: "pct" },
  profit_factor: { label: "盈亏比", unit: "ratio" },
  avg_win: { label: "平均盈利", unit: "currency" },
  avg_loss: { label: "平均亏损", unit: "currency" },
  largest_win: { label: "最大单笔盈利", unit: "currency" },
  largest_loss: { label: "最大单笔亏损", unit: "currency" },
  avg_holding_days: { label: "平均持仓天数", unit: "days" },
  total_trades: { label: "总交易笔数", unit: "count" },
  win_trades: { label: "盈利笔数", unit: "count" },
  loss_trades: { label: "亏损笔数", unit: "count" },
  expectancy: { label: "期望收益", unit: "currency" },
  recovery_factor: { label: "恢复因子", unit: "ratio" },
  payoff_ratio: { label: "赔率", unit: "ratio" },
  value_at_risk_95: { label: "VaR(95%)", unit: "currency" },
  conditional_var_95: { label: "CVaR(95%)", unit: "currency" },
  beta: { label: "Beta", unit: "ratio" },
  alpha: { label: "Alpha", unit: "pct" },
  information_ratio: { label: "信息比率", unit: "ratio" },
  treynor_ratio: { label: "特雷诺比率", unit: "ratio" },
};

// ── File helpers ────────────────────────────────────────────────

function dataDir(cfg: QuantTradingConfig): string {
  return cfg.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
}

function tradesPath(cfg: QuantTradingConfig, mode: "paper" | "live"): string {
  return path.join(dataDir(cfg), `trades-${mode}.json`);
}

function equityPath(cfg: QuantTradingConfig, mode: "paper" | "live"): string {
  return path.join(dataDir(cfg), `equity-history-${mode}.json`);
}

async function loadJson<T>(filePath: string): Promise<T | null> {
  try {
    return JSON.parse(await fs.readFile(filePath, "utf-8")) as T;
  } catch {
    return null;
  }
}

// ── Formatting helpers ──────────────────────────────────────────

function formatMetric(value: number, unit: PerformanceMetricEntry["unit"]): string {
  switch (unit) {
    case "pct":
      return `${value.toFixed(2)}%`;
    case "currency":
      return `¥${value.toFixed(2)}`;
    case "ratio":
      return value === Infinity ? "∞" : value === -Infinity ? "-∞" : value.toFixed(2);
    case "days":
      return `${value.toFixed(1)}天`;
    case "count":
      return String(Math.round(value));
  }
}

function makeMetricEntry(id: PerformanceMetricId, value: number): PerformanceMetricEntry {
  const meta = METRIC_LABELS[id];
  return {
    id,
    label: meta.label,
    value,
    formatted: formatMetric(value, meta.unit),
    unit: meta.unit,
  };
}

// ── Core computation ────────────────────────────────────────────

function filterTrades(
  trades: TradeRecord[],
  query: PerformanceReportQuery,
): TradeRecord[] {
  let result = trades.filter((t) => t.exitTime != null);

  if (query.strategyIds && query.strategyIds.length > 0) {
    const set = new Set(query.strategyIds);
    result = result.filter((t) => t.strategyId != null && set.has(t.strategyId));
  }

  if (query.symbols && query.symbols.length > 0) {
    const set = new Set(query.symbols);
    result = result.filter((t) => set.has(t.symbol));
  }

  if (query.startDate) {
    result = result.filter((t) => (t.exitTime ?? "") >= query.startDate!);
  }
  if (query.endDate) {
    result = result.filter((t) => (t.exitTime ?? "") <= query.endDate! + "T23:59:59");
  }

  return result.sort((a, b) => (a.exitTime ?? "").localeCompare(b.exitTime ?? ""));
}

function filterEquitySnapshots(
  snapshots: EquitySnapshot[],
  query: PerformanceReportQuery,
): EquitySnapshot[] {
  let result = [...snapshots].sort((a, b) => a.date.localeCompare(b.date));

  if (query.startDate) {
    result = result.filter((s) => s.date >= query.startDate!);
  }
  if (query.endDate) {
    result = result.filter((s) => s.date <= query.endDate!);
  }

  return result;
}

/** Compute daily returns from equity snapshots. */
function dailyReturns(snapshots: EquitySnapshot[]): number[] {
  const returns: number[] = [];
  for (let i = 1; i < snapshots.length; i++) {
    const prev = snapshots[i - 1]!.equity;
    const curr = snapshots[i]!.equity;
    returns.push(prev > 0 ? (curr - prev) / prev : 0);
  }
  return returns;
}

/** Compute drawdown series from equity snapshots. */
function computeDrawdowns(snapshots: EquitySnapshot[]): {
  maxDrawdown: number;
  maxDrawdownPct: number;
  maxDrawdownDuration: number;
  series: number[];
} {
  let peak = -Infinity;
  let maxDd = 0;
  let maxDdPct = 0;
  let ddStartIdx = 0;
  let maxDdDuration = 0;
  let currentDdDuration = 0;
  const series: number[] = [];

  for (let i = 0; i < snapshots.length; i++) {
    const eq = snapshots[i]!.equity;
    if (eq > peak) {
      peak = eq;
      ddStartIdx = i;
      currentDdDuration = 0;
    }
    const dd = peak - eq;
    const ddPct = peak > 0 ? (dd / peak) * 100 : 0;
    series.push(ddPct);

    if (dd > 0) {
      currentDdDuration = i - ddStartIdx;
    }

    if (dd > maxDd) {
      maxDd = dd;
      maxDdPct = ddPct;
    }
    if (currentDdDuration > maxDdDuration) {
      maxDdDuration = currentDdDuration;
    }
  }

  return { maxDrawdown: maxDd, maxDrawdownPct: maxDdPct, maxDrawdownDuration: maxDdDuration, series };
}

/** Compute Sharpe ratio from daily returns. */
function computeSharpe(returns: number[]): number {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((s, r) => s + r, 0) / returns.length;
  const variance = returns.reduce((s, r) => s + (r - mean) ** 2, 0) / (returns.length - 1);
  const std = Math.sqrt(variance);
  return std > 0 ? ((mean - DAILY_RF) / std) * Math.sqrt(TRADING_DAYS_PER_YEAR) : 0;
}

/** Compute Sortino ratio (downside deviation only). */
function computeSortino(returns: number[]): number {
  if (returns.length < 2) return 0;
  const mean = returns.reduce((s, r) => s + r, 0) / returns.length;
  const downside = returns.filter((r) => r < DAILY_RF);
  if (downside.length === 0) return mean > DAILY_RF ? Infinity : 0;
  const downsideVar = downside.reduce((s, r) => s + (r - DAILY_RF) ** 2, 0) / downside.length;
  const downsideStd = Math.sqrt(downsideVar);
  return downsideStd > 0 ? ((mean - DAILY_RF) / downsideStd) * Math.sqrt(TRADING_DAYS_PER_YEAR) : 0;
}

/** Compute VaR and CVaR at 95% confidence from daily returns. */
function computeVaR(returns: number[], equity: number): { var95: number; cvar95: number } {
  if (returns.length < 5) return { var95: 0, cvar95: 0 };
  const sorted = [...returns].sort((a, b) => a - b);
  const idx = Math.floor(returns.length * 0.05);
  const var95 = -sorted[idx]! * equity;
  const tail = sorted.slice(0, idx + 1);
  const cvar95 = tail.length > 0
    ? -(tail.reduce((s, r) => s + r, 0) / tail.length) * equity
    : var95;
  return { var95, cvar95 };
}

/** Build trade aggregation for a group of trades. */
function buildAggregation(
  key: string,
  trades: TradeRecord[],
  totalNetPnl: number,
  label?: string,
): TradeAggregation {
  const wins = trades.filter((t) => t.netPnl > 0);
  const losses = trades.filter((t) => t.netPnl <= 0);
  const winTotal = wins.reduce((s, t) => s + t.netPnl, 0);
  const lossTotal = Math.abs(losses.reduce((s, t) => s + t.netPnl, 0));
  const grossPnl = trades.reduce((s, t) => s + t.grossPnl, 0);
  const netPnl = trades.reduce((s, t) => s + t.netPnl, 0);
  const commission = trades.reduce((s, t) => s + t.commission, 0);
  const holdingDaysSum = trades.reduce((s, t) => s + (t.holdingDays ?? 0), 0);

  return {
    key,
    label,
    trades: trades.length,
    wins: wins.length,
    losses: losses.length,
    winRate: trades.length > 0 ? (wins.length / trades.length) * 100 : 0,
    grossPnl,
    netPnl,
    commission,
    avgWin: wins.length > 0 ? winTotal / wins.length : 0,
    avgLoss: losses.length > 0 ? lossTotal / losses.length : 0,
    largestWin: wins.length > 0 ? Math.max(...wins.map((t) => t.netPnl)) : 0,
    largestLoss: losses.length > 0 ? Math.min(...losses.map((t) => t.netPnl)) : 0,
    profitFactor: lossTotal > 0 ? winTotal / lossTotal : winTotal > 0 ? Infinity : 0,
    avgHoldingDays: trades.length > 0 ? holdingDaysSum / trades.length : 0,
    contributionPct: totalNetPnl !== 0 ? (netPnl / Math.abs(totalNetPnl)) * 100 : 0,
  };
}

function groupBy<T>(items: T[], keyFn: (item: T) => string): Map<string, T[]> {
  const map = new Map<string, T[]>();
  for (const item of items) {
    const key = keyFn(item);
    if (!map.has(key)) map.set(key, []);
    map.get(key)!.push(item);
  }
  return map;
}

// ── Service ─────────────────────────────────────────────────────

export class TradingPerformanceReportService {
  private config: QuantTradingConfig;

  constructor(config: QuantTradingConfig) {
    this.config = config;
  }

  /**
   * Generate a comprehensive trading performance report.
   *
   * Loads trade records and equity snapshots, applies filters from the query,
   * then computes all requested performance metrics and aggregations.
   */
  async generateReport(query: PerformanceReportQuery = {}): Promise<TradingPerformanceReport> {
    const mode = query.mode ?? (this.config.tradingAccount?.mode === "live" ? "live" : "paper");

    // Load data
    const rawTrades = (await loadJson<TradeRecord[]>(tradesPath(this.config, mode))) ?? [];
    const rawEquity = (await loadJson<EquitySnapshot[]>(equityPath(this.config, mode))) ?? [];

    // Filter
    const trades = filterTrades(rawTrades, query);
    const equity = filterEquitySnapshots(rawEquity, query);

    // Date range
    const tradeDates = trades.map((t) => (t.exitTime ?? "").slice(0, 10)).filter(Boolean);
    const equityDates = equity.map((s) => s.date);
    const allDates = [...tradeDates, ...equityDates].sort();
    const from = allDates[0] ?? "";
    const to = allDates[allDates.length - 1] ?? "";

    // Trade-level stats
    const totalNetPnl = trades.reduce((s, t) => s + t.netPnl, 0);
    const totalGrossPnl = trades.reduce((s, t) => s + t.grossPnl, 0);
    const totalCommission = trades.reduce((s, t) => s + t.commission, 0);
    const winTrades = trades.filter((t) => t.netPnl > 0);
    const lossTrades = trades.filter((t) => t.netPnl <= 0);
    const totalWinAmount = winTrades.reduce((s, t) => s + t.netPnl, 0);
    const totalLossAmount = Math.abs(lossTrades.reduce((s, t) => s + t.netPnl, 0));
    const holdingDaysSum = trades.reduce((s, t) => s + (t.holdingDays ?? 0), 0);

    // Equity-curve metrics
    const returns = dailyReturns(equity);
    const dd = computeDrawdowns(equity);
    const startEquity = equity.length > 0 ? equity[0]!.equity : 0;
    const endEquity = equity.length > 0 ? equity[equity.length - 1]!.equity : 0;
    const totalReturnPct = startEquity > 0 ? ((endEquity - startEquity) / startEquity) * 100 : 0;

    const days = equity.length;
    const years = days / TRADING_DAYS_PER_YEAR;
    const annualizedReturn = years > 0 && startEquity > 0
      ? (Math.pow(endEquity / startEquity, 1 / years) - 1) * 100
      : 0;

    const volatility = returns.length > 1
      ? Math.sqrt(
          returns.reduce((s, r) => {
            const mean = returns.reduce((a, b) => a + b, 0) / returns.length;
            return s + (r - mean) ** 2;
          }, 0) / (returns.length - 1),
        ) * Math.sqrt(TRADING_DAYS_PER_YEAR) * 100
      : 0;

    const sharpe = computeSharpe(returns);
    const sortino = computeSortino(returns);
    const calmar = dd.maxDrawdownPct > 0 ? annualizedReturn / dd.maxDrawdownPct : 0;
    const { var95, cvar95 } = computeVaR(returns, endEquity);

    const expectancy = trades.length > 0 ? totalNetPnl / trades.length : 0;
    const recoveryFactor = dd.maxDrawdown > 0 ? totalNetPnl / dd.maxDrawdown : 0;
    const payoffRatio = totalLossAmount > 0 && lossTrades.length > 0 && winTrades.length > 0
      ? (totalWinAmount / winTrades.length) / (totalLossAmount / lossTrades.length)
      : 0;

    // Build all metric values
    const allMetrics: Record<PerformanceMetricId, number> = {
      total_return: totalReturnPct,
      annualized_return: annualizedReturn,
      total_pnl: totalGrossPnl,
      total_commission: totalCommission,
      net_pnl: totalNetPnl,
      sharpe_ratio: sharpe,
      sortino_ratio: sortino,
      calmar_ratio: calmar,
      max_drawdown: dd.maxDrawdown,
      max_drawdown_pct: dd.maxDrawdownPct,
      max_drawdown_duration: dd.maxDrawdownDuration,
      volatility,
      win_rate: trades.length > 0 ? (winTrades.length / trades.length) * 100 : 0,
      profit_factor: totalLossAmount > 0 ? totalWinAmount / totalLossAmount : totalWinAmount > 0 ? Infinity : 0,
      avg_win: winTrades.length > 0 ? totalWinAmount / winTrades.length : 0,
      avg_loss: lossTrades.length > 0 ? totalLossAmount / lossTrades.length : 0,
      largest_win: winTrades.length > 0 ? Math.max(...winTrades.map((t) => t.netPnl)) : 0,
      largest_loss: lossTrades.length > 0 ? Math.min(...lossTrades.map((t) => t.netPnl)) : 0,
      avg_holding_days: trades.length > 0 ? holdingDaysSum / trades.length : 0,
      total_trades: trades.length,
      win_trades: winTrades.length,
      loss_trades: lossTrades.length,
      expectancy,
      recovery_factor: recoveryFactor,
      payoff_ratio: payoffRatio,
      value_at_risk_95: var95,
      conditional_var_95: cvar95,
      // Beta/Alpha/IR/Treynor require benchmark data — set to 0 when unavailable
      beta: 0,
      alpha: 0,
      information_ratio: 0,
      treynor_ratio: 0,
    };

    // Filter to requested metrics
    const requestedIds = query.metrics ?? (Object.keys(METRIC_LABELS) as PerformanceMetricId[]);
    const metrics: PerformanceMetricEntry[] = requestedIds.map((id) =>
      makeMetricEntry(id, allMetrics[id] ?? 0),
    );

    // Aggregations
    const byStrategyMap = groupBy(trades, (t) => t.strategyId ?? "unknown");
    const byStrategy = [...byStrategyMap.entries()]
      .map(([key, group]) => buildAggregation(key, group, totalNetPnl))
      .sort((a, b) => Math.abs(b.netPnl) - Math.abs(a.netPnl));

    const bySymbolMap = groupBy(trades, (t) => t.symbol);
    const bySymbol = [...bySymbolMap.entries()]
      .map(([key, group]) => buildAggregation(key, group, totalNetPnl, group[0]?.name))
      .sort((a, b) => b.netPnl - a.netPnl);

    const byMonthMap = groupBy(trades, (t) => (t.exitTime ?? "").slice(0, 7));
    const byMonth = [...byMonthMap.entries()]
      .map(([key, group]) => buildAggregation(key, group, totalNetPnl))
      .sort((a, b) => a.key.localeCompare(b.key));

    // Equity curve with drawdown overlay
    const equityCurve: EquityCurvePoint[] = equity.map((s, i) => ({
      date: s.date,
      equity: s.equity,
      cash: s.cash,
      marketValue: s.marketValue,
      dailyPnl: s.dailyPnl,
      drawdownPct: dd.series[i] ?? 0,
      benchmarkValue: s.benchmark,
    }));

    // Monthly returns from equity snapshots
    const monthlyReturnsMap = new Map<string, { startEquity: number; endEquity: number }>();
    for (const snap of equity) {
      const month = snap.date.slice(0, 7);
      if (!monthlyReturnsMap.has(month)) {
        monthlyReturnsMap.set(month, { startEquity: snap.equity, endEquity: snap.equity });
      } else {
        monthlyReturnsMap.get(month)!.endEquity = snap.equity;
      }
    }
    const monthlyReturns = [...monthlyReturnsMap.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(([month, { startEquity: s, endEquity: e }]) => ({
        month,
        returnPct: s > 0 ? ((e - s) / s) * 100 : 0,
      }));

    return {
      generatedAt: new Date().toISOString(),
      query: { ...query, mode },
      dateRange: { from, to },
      metrics,
      byStrategy,
      bySymbol,
      byMonth,
      equityCurve,
      monthlyReturns,
      totalTradesAnalyzed: trades.length,
      totalEquitySnapshots: equity.length,
    };
  }

  /**
   * Get a quick summary of key metrics (suitable for dashboard display).
   * Returns a subset of the most important metrics without full aggregation.
   */
  async getQuickSummary(
    query: PerformanceReportQuery = {},
  ): Promise<Pick<TradingPerformanceReport, "generatedAt" | "dateRange" | "metrics" | "totalTradesAnalyzed">> {
    const keyMetrics: PerformanceMetricId[] = [
      "total_return",
      "annualized_return",
      "net_pnl",
      "sharpe_ratio",
      "max_drawdown_pct",
      "win_rate",
      "profit_factor",
      "total_trades",
    ];
    const report = await this.generateReport({ ...query, metrics: keyMetrics });
    return {
      generatedAt: report.generatedAt,
      dateRange: report.dateRange,
      metrics: report.metrics,
      totalTradesAnalyzed: report.totalTradesAnalyzed,
    };
  }

  /**
   * List available strategy IDs from trade records.
   * Useful for populating filter dropdowns.
   */
  async listStrategies(mode?: "paper" | "live"): Promise<string[]> {
    const m = mode ?? (this.config.tradingAccount?.mode === "live" ? "live" : "paper");
    const trades = (await loadJson<TradeRecord[]>(tradesPath(this.config, m))) ?? [];
    const set = new Set<string>();
    for (const t of trades) {
      if (t.strategyId) set.add(t.strategyId);
    }
    return [...set].sort();
  }

  /**
   * List available symbols from trade records.
   * Useful for populating filter dropdowns.
   */
  async listSymbols(mode?: "paper" | "live"): Promise<string[]> {
    const m = mode ?? (this.config.tradingAccount?.mode === "live" ? "live" : "paper");
    const trades = (await loadJson<TradeRecord[]>(tradesPath(this.config, m))) ?? [];
    const set = new Set<string>();
    for (const t of trades) {
      set.add(t.symbol);
    }
    return [...set].sort();
  }

  /**
   * Get the available date range for performance data.
   */
  async getAvailableDateRange(mode?: "paper" | "live"): Promise<{ from: string; to: string } | null> {
    const m = mode ?? (this.config.tradingAccount?.mode === "live" ? "live" : "paper");
    const trades = (await loadJson<TradeRecord[]>(tradesPath(this.config, m))) ?? [];
    const equity = (await loadJson<EquitySnapshot[]>(equityPath(this.config, m))) ?? [];

    const dates = [
      ...trades.map((t) => (t.exitTime ?? "").slice(0, 10)).filter(Boolean),
      ...equity.map((s) => s.date),
    ].sort();

    if (dates.length === 0) return null;
    return { from: dates[0]!, to: dates[dates.length - 1]! };
  }
}
