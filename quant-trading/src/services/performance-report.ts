/**
 * PerformanceReport — consolidated performance report generator.
 *
 * Combines equity history, trade attribution, and risk metrics into a
 * single report with multiple output formats:
 *   - JSON (structured data for API consumers)
 *   - Chart data (pre-formatted series for equity curve, drawdown, PnL bars, strategy pie)
 *   - Text/Markdown summary (for push notifications, CLI, chat)
 *   - HTML (self-contained report page with inline SVG charts)
 *
 * Supports time-period filtering (daily, weekly, monthly, custom range)
 * and per-symbol drill-down.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { TradeRecord, QuantTradingConfig } from "../types.js";
import { computeAttribution } from "./performance-attribution.js";
import type { AttributionResult, PeriodPnl } from "./performance-attribution.js";
import { EquityHistory } from "./equity-history.js";
import type { EquitySnapshot } from "./equity-history.js";

// ── Types ────────────────────────────────────────────────────

export interface ReportPeriod {
  from: string;
  to: string;
}

export interface ChartSeries {
  labels: string[];
  values: number[];
}

export interface ChartData {
  equityCurve: ChartSeries & { benchmark?: number[] };
  drawdown: ChartSeries;
  dailyPnl: ChartSeries;
  monthlyPnl: ChartSeries;
  strategyPie: Array<{ label: string; value: number; pct: number }>;
  symbolPnlBar: Array<{ label: string; value: number }>;
  weekdayPnl: Array<{ label: string; avgPnl: number; count: number }>;
}

export interface ReportSummary {
  period: ReportPeriod;
  tradingDays: number;
  /** Starting equity */
  startEquity: number;
  /** Ending equity */
  endEquity: number;
  /** Absolute return */
  totalReturn: number;
  /** Percentage return */
  totalReturnPct: number;
  /** Annualized return percentage */
  annualizedReturnPct: number;
  /** Max drawdown percentage */
  maxDrawdownPct: number;
  /** Max drawdown absolute */
  maxDrawdown: number;
  /** Best single day */
  bestDay: { date: string; pnl: number } | null;
  /** Worst single day */
  worstDay: { date: string; pnl: number } | null;
  /** Average daily PnL */
  avgDailyPnl: number;
  /** Win days / loss days */
  winDays: number;
  lossDays: number;
  /** Day-level win rate */
  dayWinRate: number;
  /** Trade-level stats */
  totalTrades: number;
  tradeWinRate: number;
  profitFactor: number;
  avgWin: number;
  avgLoss: number;
  totalCommission: number;
  /** Per-strategy breakdown */
  byStrategy: Array<{
    strategyId: string;
    trades: number;
    netPnl: number;
    winRate: number;
    contributionPct: number;
  }>;
  /** Per-symbol top movers */
  topWinners: Array<{ symbol: string; netPnl: number; trades: number }>;
  topLosers: Array<{ symbol: string; netPnl: number; trades: number }>;
  /** Period-level P&L */
  byMonth: PeriodPnl[];
  byWeek: PeriodPnl[];
}

export interface PerformanceReportResult {
  summary: ReportSummary;
  charts: ChartData;
  text: string;
  html: string;
}

// ── Helpers ──────────────────────────────────────────────────

function pct(v: number, decimals = 1): string {
  return `${v >= 0 ? "+" : ""}${v.toFixed(decimals)}%`;
}

function yuan(v: number): string {
  return `${v >= 0 ? "+" : ""}¥${v.toFixed(2)}`;
}

function calcAnnualizedReturn(totalReturnPct: number, days: number): number {
  if (days <= 0) return 0;
  const years = days / 252;
  if (years <= 0) return 0;
  const r = totalReturnPct / 100;
  if (r <= -1) return -100;
  return ((1 + r) ** (1 / years) - 1) * 100;
}

function weekdayName(dateStr: string): string {
  const d = new Date(dateStr);
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][d.getDay()] ?? "";
}

// ── Core generator ──────────────────────────────────────────

export async function generatePerformanceReport(
  config: QuantTradingConfig,
  options?: {
    from?: string;
    to?: string;
    mode?: "paper" | "live";
    /** Limit top symbols in chart/text output */
    topN?: number;
  },
): Promise<PerformanceReportResult> {
  const dataDir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  const topN = options?.topN ?? 5;

  // Load trades
  let trades: TradeRecord[] = [];
  try {
    const raw = await fs.readFile(path.join(dataDir, "trades.json"), "utf-8");
    trades = JSON.parse(raw) as TradeRecord[];
  } catch {
    // no trades
  }

  // Load equity history
  const equityHistory = new EquityHistory({ dataDir, mode: options?.mode });
  await equityHistory.load();

  // Determine date range
  const allSnapshots = equityHistory.getAll();
  const from = options?.from ?? allSnapshots[0]?.date ?? new Date().toISOString().slice(0, 10);
  const to = options?.to ?? allSnapshots[allSnapshots.length - 1]?.date ?? new Date().toISOString().slice(0, 10);

  // Filter snapshots to range
  const snapshots = allSnapshots.filter((s) => s.date >= from && s.date <= to);

  // Compute attribution
  const attribution = computeAttribution(trades, { from, to });

  // Build summary
  const summary = buildSummary(snapshots, attribution, { from, to });

  // Build chart data
  const charts = buildChartData(snapshots, attribution, topN);

  // Build text summary
  const text = buildTextSummary(summary, topN);

  // Build HTML
  const html = buildHtmlReport(summary, charts);

  return { summary, charts, text, html };
}

// ── Summary builder ─────────────────────────────────────────

function buildSummary(
  snapshots: EquitySnapshot[],
  attribution: AttributionResult,
  period: ReportPeriod,
): ReportSummary {
  const tradingDays = snapshots.length;
  const startEquity = snapshots[0]?.equity ?? 0;
  const endEquity = snapshots[snapshots.length - 1]?.equity ?? 0;
  const totalReturn = endEquity - startEquity;
  const totalReturnPct = startEquity > 0 ? (totalReturn / startEquity) * 100 : 0;
  const annualizedReturnPct = calcAnnualizedReturn(totalReturnPct, tradingDays);

  // Max drawdown
  let peak = -Infinity;
  let maxDrawdown = 0;
  let maxDrawdownPct = 0;
  for (const s of snapshots) {
    if (s.equity > peak) peak = s.equity;
    const dd = peak - s.equity;
    if (dd > maxDrawdown) {
      maxDrawdown = dd;
      maxDrawdownPct = peak > 0 ? (dd / peak) * 100 : 0;
    }
  }

  // Best/worst day
  let bestDay: { date: string; pnl: number } | null = null;
  let worstDay: { date: string; pnl: number } | null = null;
  let winDays = 0;
  let lossDays = 0;
  let totalDailyPnl = 0;

  for (const s of snapshots) {
    totalDailyPnl += s.dailyPnl;
    if (!bestDay || s.dailyPnl > bestDay.pnl) bestDay = { date: s.date, pnl: s.dailyPnl };
    if (!worstDay || s.dailyPnl < worstDay.pnl) worstDay = { date: s.date, pnl: s.dailyPnl };
    if (s.dailyPnl > 0) winDays++;
    else if (s.dailyPnl < 0) lossDays++;
  }

  const avgDailyPnl = tradingDays > 0 ? totalDailyPnl / tradingDays : 0;
  const dayWinRate = winDays + lossDays > 0 ? (winDays / (winDays + lossDays)) * 100 : 0;

  // Strategy breakdown
  const byStrategy = attribution.byStrategy.map((s) => ({
    strategyId: s.strategyId,
    trades: s.trades,
    netPnl: s.netPnl,
    winRate: s.winRate,
    contributionPct: s.contributionPct,
  }));

  // Top winners & losers
  const sortedSymbols = [...attribution.bySymbol];
  const topWinners = sortedSymbols
    .filter((s) => s.netPnl > 0)
    .sort((a, b) => b.netPnl - a.netPnl)
    .slice(0, 5)
    .map((s) => ({ symbol: s.symbol, netPnl: s.netPnl, trades: s.trades }));
  const topLosers = sortedSymbols
    .filter((s) => s.netPnl < 0)
    .sort((a, b) => a.netPnl - b.netPnl)
    .slice(0, 5)
    .map((s) => ({ symbol: s.symbol, netPnl: s.netPnl, trades: s.trades }));

  return {
    period,
    tradingDays,
    startEquity,
    endEquity,
    totalReturn,
    totalReturnPct,
    annualizedReturnPct,
    maxDrawdownPct,
    maxDrawdown,
    bestDay,
    worstDay,
    avgDailyPnl,
    winDays,
    lossDays,
    dayWinRate,
    totalTrades: attribution.totalTrades,
    tradeWinRate: attribution.overallWinRate * 100,
    profitFactor: attribution.profitFactor,
    avgWin: attribution.avgWin,
    avgLoss: attribution.avgLoss,
    totalCommission: attribution.totalCommission,
    byStrategy,
    topWinners,
    topLosers,
    byMonth: attribution.byMonth,
    byWeek: attribution.byWeek,
  };
}

// ── Chart data builder ──────────────────────────────────────

function buildChartData(
  snapshots: EquitySnapshot[],
  attribution: AttributionResult,
  topN: number,
): ChartData {
  // Equity curve
  const equityCurve: ChartData["equityCurve"] = {
    labels: snapshots.map((s) => s.date),
    values: snapshots.map((s) => s.equity),
  };
  const benchmarks = snapshots.map((s) => s.benchmark).filter((b): b is number => b != null);
  if (benchmarks.length === snapshots.length && benchmarks.length > 0) {
    equityCurve.benchmark = benchmarks;
  }

  // Drawdown
  let peak = -Infinity;
  const drawdownValues: number[] = [];
  for (const s of snapshots) {
    if (s.equity > peak) peak = s.equity;
    drawdownValues.push(peak > 0 ? -((peak - s.equity) / peak) * 100 : 0);
  }
  const drawdown: ChartSeries = {
    labels: snapshots.map((s) => s.date),
    values: drawdownValues,
  };

  // Daily P&L
  const dailyPnl: ChartSeries = {
    labels: snapshots.map((s) => s.date),
    values: snapshots.map((s) => s.dailyPnl),
  };

  // Monthly P&L
  const monthlyPnl: ChartSeries = {
    labels: attribution.byMonth.map((p) => p.period),
    values: attribution.byMonth.map((p) => p.netPnl),
  };

  // Strategy pie
  const totalAbs = attribution.byStrategy.reduce((s, a) => s + Math.abs(a.netPnl), 0);
  const strategyPie = attribution.byStrategy.slice(0, topN).map((s) => ({
    label: s.strategyId,
    value: s.netPnl,
    pct: totalAbs > 0 ? (Math.abs(s.netPnl) / totalAbs) * 100 : 0,
  }));

  // Symbol PnL bar (top winners + losers)
  const sortedByAbsPnl = [...attribution.bySymbol]
    .sort((a, b) => Math.abs(b.netPnl) - Math.abs(a.netPnl))
    .slice(0, topN * 2);
  const symbolPnlBar = sortedByAbsPnl.map((s) => ({
    label: s.symbol,
    value: s.netPnl,
  }));

  // Weekday PnL distribution
  const weekdayMap = new Map<string, { total: number; count: number }>();
  for (const s of snapshots) {
    const wd = weekdayName(s.date);
    if (!wd) continue;
    const entry = weekdayMap.get(wd) ?? { total: 0, count: 0 };
    entry.total += s.dailyPnl;
    entry.count++;
    weekdayMap.set(wd, entry);
  }
  const weekdayOrder = ["周一", "周二", "周三", "周四", "周五"];
  const weekdayPnl = weekdayOrder
    .filter((wd) => weekdayMap.has(wd))
    .map((wd) => {
      const e = weekdayMap.get(wd)!;
      return { label: wd, avgPnl: e.count > 0 ? e.total / e.count : 0, count: e.count };
    });

  return { equityCurve, drawdown, dailyPnl, monthlyPnl, strategyPie, symbolPnlBar, weekdayPnl };
}

// ── Text/Markdown summary ───────────────────────────────────

function buildTextSummary(summary: ReportSummary, topN: number): string {
  const lines: string[] = [];
  const s = summary;

  lines.push("=== 交易绩效报告 ===");
  lines.push(`报告周期: ${s.period.from} ~ ${s.period.to} (${s.tradingDays}个交易日)`);
  lines.push("");

  // Overview
  lines.push("-- 账户概览 --");
  lines.push(`  期初权益: ¥${s.startEquity.toFixed(2)}`);
  lines.push(`  期末权益: ¥${s.endEquity.toFixed(2)}`);
  lines.push(`  总收益:   ${yuan(s.totalReturn)} (${pct(s.totalReturnPct)})`);
  lines.push(`  年化收益: ${pct(s.annualizedReturnPct)}`);
  lines.push(`  最大回撤: ${s.maxDrawdownPct.toFixed(2)}% (¥${s.maxDrawdown.toFixed(2)})`);
  lines.push("");

  // Daily stats
  lines.push("-- 日级统计 --");
  lines.push(`  盈利天数: ${s.winDays}天  亏损天数: ${s.lossDays}天  日胜率: ${s.dayWinRate.toFixed(1)}%`);
  lines.push(`  日均盈亏: ${yuan(s.avgDailyPnl)}`);
  if (s.bestDay) lines.push(`  最佳单日: ${s.bestDay.date} ${yuan(s.bestDay.pnl)}`);
  if (s.worstDay) lines.push(`  最差单日: ${s.worstDay.date} ${yuan(s.worstDay.pnl)}`);
  lines.push("");

  // Trade stats
  lines.push("-- 交易统计 --");
  lines.push(`  总交易: ${s.totalTrades}笔  胜率: ${s.tradeWinRate.toFixed(1)}%`);
  lines.push(`  盈亏比: ${s.profitFactor === Infinity ? "∞" : s.profitFactor.toFixed(2)}`);
  lines.push(`  平均盈利: ¥${s.avgWin.toFixed(2)}  平均亏损: ¥${s.avgLoss.toFixed(2)}`);
  lines.push(`  总手续费: ¥${s.totalCommission.toFixed(2)}`);
  lines.push("");

  // Strategy breakdown
  if (s.byStrategy.length > 0) {
    lines.push("-- 策略绩效 --");
    for (const st of s.byStrategy.slice(0, topN)) {
      lines.push(
        `  ${st.strategyId}: ${st.trades}笔 ${yuan(st.netPnl)} 胜率${(st.winRate * 100).toFixed(0)}% 贡献${st.contributionPct.toFixed(1)}%`,
      );
    }
    lines.push("");
  }

  // Top winners/losers
  if (s.topWinners.length > 0) {
    lines.push("-- 盈利标的 --");
    for (const w of s.topWinners.slice(0, topN)) {
      lines.push(`  ${w.symbol}: ${yuan(w.netPnl)} (${w.trades}笔)`);
    }
    lines.push("");
  }
  if (s.topLosers.length > 0) {
    lines.push("-- 亏损标的 --");
    for (const l of s.topLosers.slice(0, topN)) {
      lines.push(`  ${l.symbol}: ${yuan(l.netPnl)} (${l.trades}笔)`);
    }
    lines.push("");
  }

  // Monthly returns
  if (s.byMonth.length > 0) {
    lines.push("-- 月度收益 --");
    for (const m of s.byMonth) {
      const bar = m.netPnl >= 0 ? "+" : "-";
      lines.push(
        `  ${m.period}: ${yuan(m.netPnl)} (累计 ¥${m.cumPnl.toFixed(2)}) [${m.wins}胜/${m.losses}负]`,
      );
    }
    lines.push("");
  }

  return lines.join("\n");
}

// ── HTML report builder ─────────────────────────────────────

function buildHtmlReport(summary: ReportSummary, charts: ChartData): string {
  const s = summary;

  // SVG chart builders
  const equitySvg = buildLineSvg(charts.equityCurve.labels, charts.equityCurve.values, {
    width: 700,
    height: 200,
    color: "#58a6ff",
    benchmarkValues: charts.equityCurve.benchmark,
    benchmarkColor: "#8b949e",
  });

  const drawdownSvg = buildAreaSvg(charts.drawdown.labels, charts.drawdown.values, {
    width: 700,
    height: 120,
    color: "#f85149",
  });

  const dailyPnlSvg = buildBarSvg(charts.dailyPnl.labels, charts.dailyPnl.values, {
    width: 700,
    height: 150,
  });

  const monthlyPnlSvg = buildBarSvg(charts.monthlyPnl.labels, charts.monthlyPnl.values, {
    width: 700,
    height: 150,
  });

  const strategyRows = s.byStrategy
    .map(
      (st) =>
        `<tr>
      <td>${esc(st.strategyId)}</td>
      <td>${st.trades}</td>
      <td class="${st.netPnl >= 0 ? "pos" : "neg"}">${yuan(st.netPnl)}</td>
      <td>${(st.winRate * 100).toFixed(1)}%</td>
      <td>${st.contributionPct.toFixed(1)}%</td>
    </tr>`,
    )
    .join("\n");

  const winnerRows = s.topWinners
    .map(
      (w) =>
        `<tr><td>${esc(w.symbol)}</td><td>${w.trades}</td><td class="pos">${yuan(w.netPnl)}</td></tr>`,
    )
    .join("\n");

  const loserRows = s.topLosers
    .map(
      (l) =>
        `<tr><td>${esc(l.symbol)}</td><td>${l.trades}</td><td class="neg">${yuan(l.netPnl)}</td></tr>`,
    )
    .join("\n");

  const monthlyRows = s.byMonth
    .map(
      (m) =>
        `<tr>
      <td>${esc(m.period)}</td>
      <td>${m.trades}</td>
      <td class="${m.netPnl >= 0 ? "pos" : "neg"}">${yuan(m.netPnl)}</td>
      <td>¥${m.cumPnl.toFixed(2)}</td>
      <td>${m.wins}/${m.losses}</td>
    </tr>`,
    )
    .join("\n");

  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>交易绩效报告 ${esc(s.period.from)} ~ ${esc(s.period.to)}</title>
  <style>
    :root {
      --bg: #0d1117; --bg2: #161b22; --bg3: #21262d;
      --border: #30363d; --text: #c9d1d9; --text-dim: #8b949e;
      --accent: #58a6ff; --green: #3fb950; --red: #f85149;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
      background: var(--bg); color: var(--text); padding: 24px; max-width: 900px; margin: 0 auto;
    }
    h1 { font-size: 22px; margin-bottom: 4px; }
    h2 { font-size: 16px; color: var(--accent); margin: 20px 0 10px; border-bottom: 1px solid var(--border); padding-bottom: 4px; }
    .subtitle { color: var(--text-dim); font-size: 13px; margin-bottom: 16px; }
    .kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 16px 0; }
    .kpi { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
    .kpi .label { font-size: 12px; color: var(--text-dim); }
    .kpi .value { font-size: 20px; font-weight: 600; margin-top: 4px; }
    .pos { color: var(--green); }
    .neg { color: var(--red); }
    .chart-container { background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin: 12px 0; overflow-x: auto; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--border); }
    th { color: var(--text-dim); font-weight: 500; font-size: 12px; }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
    @media (max-width: 700px) { .two-col { grid-template-columns: 1fr; } }
    .footer { margin-top: 24px; text-align: center; color: var(--text-dim); font-size: 12px; }
    svg { max-width: 100%; height: auto; }
  </style>
</head>
<body>
  <h1>交易绩效报告</h1>
  <div class="subtitle">${esc(s.period.from)} ~ ${esc(s.period.to)} | ${s.tradingDays}个交易日 | ${s.totalTrades}笔交易</div>

  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">总收益</div>
      <div class="value ${s.totalReturn >= 0 ? "pos" : "neg"}">${yuan(s.totalReturn)}</div>
    </div>
    <div class="kpi">
      <div class="label">收益率</div>
      <div class="value ${s.totalReturnPct >= 0 ? "pos" : "neg"}">${pct(s.totalReturnPct)}</div>
    </div>
    <div class="kpi">
      <div class="label">年化收益</div>
      <div class="value ${s.annualizedReturnPct >= 0 ? "pos" : "neg"}">${pct(s.annualizedReturnPct)}</div>
    </div>
    <div class="kpi">
      <div class="label">最大回撤</div>
      <div class="value neg">${s.maxDrawdownPct.toFixed(2)}%</div>
    </div>
    <div class="kpi">
      <div class="label">交易胜率</div>
      <div class="value">${s.tradeWinRate.toFixed(1)}%</div>
    </div>
    <div class="kpi">
      <div class="label">盈亏比</div>
      <div class="value">${s.profitFactor === Infinity ? "∞" : s.profitFactor.toFixed(2)}</div>
    </div>
    <div class="kpi">
      <div class="label">日均盈亏</div>
      <div class="value ${s.avgDailyPnl >= 0 ? "pos" : "neg"}">${yuan(s.avgDailyPnl)}</div>
    </div>
    <div class="kpi">
      <div class="label">总手续费</div>
      <div class="value">¥${s.totalCommission.toFixed(2)}</div>
    </div>
  </div>

  <h2>权益曲线</h2>
  <div class="chart-container">${equitySvg}</div>

  <h2>回撤曲线</h2>
  <div class="chart-container">${drawdownSvg}</div>

  <h2>每日盈亏</h2>
  <div class="chart-container">${dailyPnlSvg}</div>

  <h2>月度盈亏</h2>
  <div class="chart-container">${monthlyPnlSvg}</div>

  <h2>策略绩效</h2>
  <table>
    <thead><tr><th>策略</th><th>交易数</th><th>净盈亏</th><th>胜率</th><th>贡献</th></tr></thead>
    <tbody>${strategyRows || '<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">暂无数据</td></tr>'}</tbody>
  </table>

  <div class="two-col">
    <div>
      <h2>盈利标的 Top</h2>
      <table>
        <thead><tr><th>标的</th><th>交易数</th><th>净盈亏</th></tr></thead>
        <tbody>${winnerRows || '<tr><td colspan="3" style="text-align:center;color:var(--text-dim)">暂无</td></tr>'}</tbody>
      </table>
    </div>
    <div>
      <h2>亏损标的 Top</h2>
      <table>
        <thead><tr><th>标的</th><th>交易数</th><th>净盈亏</th></tr></thead>
        <tbody>${loserRows || '<tr><td colspan="3" style="text-align:center;color:var(--text-dim)">暂无</td></tr>'}</tbody>
      </table>
    </div>
  </div>

  <h2>月度明细</h2>
  <table>
    <thead><tr><th>月份</th><th>交易数</th><th>净盈亏</th><th>累计</th><th>胜/负</th></tr></thead>
    <tbody>${monthlyRows || '<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">暂无数据</td></tr>'}</tbody>
  </table>

  <h2>日级统计</h2>
  <div class="kpi-grid">
    <div class="kpi">
      <div class="label">盈利天数 / 亏损天数</div>
      <div class="value">${s.winDays} / ${s.lossDays}</div>
    </div>
    <div class="kpi">
      <div class="label">日胜率</div>
      <div class="value">${s.dayWinRate.toFixed(1)}%</div>
    </div>
    <div class="kpi">
      <div class="label">最佳单日</div>
      <div class="value pos">${s.bestDay ? `${s.bestDay.date} ${yuan(s.bestDay.pnl)}` : "--"}</div>
    </div>
    <div class="kpi">
      <div class="label">最差单日</div>
      <div class="value neg">${s.worstDay ? `${s.worstDay.date} ${yuan(s.worstDay.pnl)}` : "--"}</div>
    </div>
  </div>

  <div class="footer">
    报告生成时间: ${new Date().toISOString().replace("T", " ").slice(0, 19)}
  </div>
</body>
</html>`;
}

// ── SVG chart helpers (inline, no external deps) ────────────

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

interface SvgOpts {
  width: number;
  height: number;
  color: string;
  benchmarkValues?: number[];
  benchmarkColor?: string;
}

function buildLineSvg(
  labels: string[],
  values: number[],
  opts: SvgOpts,
): string {
  if (values.length < 2) return '<svg viewBox="0 0 700 200"><text x="350" y="100" text-anchor="middle" fill="#8b949e" font-size="14">数据不足</text></svg>';

  const { width, height, color, benchmarkValues, benchmarkColor } = opts;
  const pad = { top: 20, right: 20, bottom: 30, left: 60 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  const allVals = benchmarkValues ? [...values, ...benchmarkValues] : values;
  const min = Math.min(...allVals);
  const max = Math.max(...allVals);
  const range = max - min || 1;

  const xStep = w / (values.length - 1);
  const toY = (v: number) => pad.top + h - ((v - min) / range) * h;
  const toX = (i: number) => pad.left + i * xStep;

  const points = values.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(" ");

  let benchLine = "";
  if (benchmarkValues && benchmarkValues.length === values.length && benchmarkColor) {
    const bp = benchmarkValues.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(" ");
    benchLine = `<polyline points="${bp}" fill="none" stroke="${benchmarkColor}" stroke-width="1.5" stroke-dasharray="4,3" opacity="0.6"/>`;
  }

  // X-axis labels (show ~5)
  const step = Math.max(1, Math.floor(labels.length / 5));
  const xLabels = labels
    .filter((_, i) => i % step === 0 || i === labels.length - 1)
    .map((l, idx) => {
      const origIdx = idx * step >= labels.length ? labels.length - 1 : idx * step;
      return `<text x="${toX(origIdx).toFixed(1)}" y="${height - 5}" text-anchor="middle" fill="#8b949e" font-size="10">${esc(l.slice(5))}</text>`;
    })
    .join("\n");

  // Y-axis labels
  const ySteps = 4;
  const yLabels = Array.from({ length: ySteps + 1 }, (_, i) => {
    const v = min + (range / ySteps) * i;
    return `<text x="${pad.left - 5}" y="${toY(v).toFixed(1)}" text-anchor="end" fill="#8b949e" font-size="10" dominant-baseline="middle">${(v / 1e4).toFixed(1)}万</text>
<line x1="${pad.left}" x2="${width - pad.right}" y1="${toY(v).toFixed(1)}" y2="${toY(v).toFixed(1)}" stroke="#30363d" stroke-width="0.5"/>`;
  }).join("\n");

  return `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
${yLabels}
${benchLine}
<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2"/>
${xLabels}
</svg>`;
}

function buildAreaSvg(
  labels: string[],
  values: number[],
  opts: { width: number; height: number; color: string },
): string {
  if (values.length < 2) return '<svg viewBox="0 0 700 120"><text x="350" y="60" text-anchor="middle" fill="#8b949e" font-size="14">数据不足</text></svg>';

  const { width, height, color } = opts;
  const pad = { top: 10, right: 20, bottom: 25, left: 60 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  const min = Math.min(...values, 0);
  const max = 0;
  const range = max - min || 1;

  const xStep = w / (values.length - 1);
  const toY = (v: number) => pad.top + h - ((v - min) / range) * h;
  const toX = (i: number) => pad.left + i * xStep;

  const baseline = toY(0).toFixed(1);
  const areaPoints = [`${toX(0).toFixed(1)},${baseline}`, ...values.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`), `${toX(values.length - 1).toFixed(1)},${baseline}`].join(" ");
  const linePoints = values.map((v, i) => `${toX(i).toFixed(1)},${toY(v).toFixed(1)}`).join(" ");

  return `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
<line x1="${pad.left}" x2="${width - pad.right}" y1="${baseline}" y2="${baseline}" stroke="#30363d" stroke-width="0.5"/>
<polygon points="${areaPoints}" fill="${color}" opacity="0.15"/>
<polyline points="${linePoints}" fill="none" stroke="${color}" stroke-width="1.5"/>
<text x="${pad.left - 5}" y="${toY(min).toFixed(1)}" text-anchor="end" fill="#8b949e" font-size="10" dominant-baseline="middle">${min.toFixed(1)}%</text>
<text x="${pad.left - 5}" y="${baseline}" text-anchor="end" fill="#8b949e" font-size="10" dominant-baseline="middle">0%</text>
</svg>`;
}

function buildBarSvg(
  labels: string[],
  values: number[],
  opts: { width: number; height: number },
): string {
  if (values.length === 0) return `<svg viewBox="0 0 ${opts.width} ${opts.height}"><text x="${opts.width / 2}" y="${opts.height / 2}" text-anchor="middle" fill="#8b949e" font-size="14">数据不足</text></svg>`;

  const { width, height } = opts;
  const pad = { top: 10, right: 20, bottom: 25, left: 60 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  const max = Math.max(...values.map(Math.abs), 1);
  const barW = Math.max(1, Math.min(8, w / values.length - 1));
  const xStep = w / values.length;
  const zeroY = pad.top + h / 2;

  const bars = values
    .map((v, i) => {
      const x = pad.left + i * xStep + (xStep - barW) / 2;
      const barH = (Math.abs(v) / max) * (h / 2);
      const y = v >= 0 ? zeroY - barH : zeroY;
      const fill = v >= 0 ? "#3fb950" : "#f85149";
      return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${barH.toFixed(1)}" fill="${fill}" opacity="0.8"/>`;
    })
    .join("\n");

  // Zero line
  const zeroLine = `<line x1="${pad.left}" x2="${width - pad.right}" y1="${zeroY}" y2="${zeroY}" stroke="#30363d" stroke-width="0.5"/>`;

  // Y labels
  const yLabels = `<text x="${pad.left - 5}" y="${pad.top}" text-anchor="end" fill="#8b949e" font-size="10" dominant-baseline="middle">${yuan(max)}</text>
<text x="${pad.left - 5}" y="${zeroY}" text-anchor="end" fill="#8b949e" font-size="10" dominant-baseline="middle">0</text>
<text x="${pad.left - 5}" y="${pad.top + h}" text-anchor="end" fill="#8b949e" font-size="10" dominant-baseline="middle">${yuan(-max)}</text>`;

  return `<svg viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">
${zeroLine}
${yLabels}
${bars}
</svg>`;
}
