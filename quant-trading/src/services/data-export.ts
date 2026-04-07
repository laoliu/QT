/**
 * DataExport — export trading data in various formats.
 *
 * Supports exporting:
 *   - Trade records (交易记录)
 *   - Portfolio positions (持仓)
 *   - Performance reports (绩效报告)
 *   - Equity history (净值曲线)
 *   - Trade journal notes (交易笔记)
 *
 * Output formats:
 *   - CSV  (comma-separated, UTF-8 with BOM for Excel compatibility)
 *   - JSON (formatted, portable)
 *   - XLSX-compatible TSV (tab-separated, for spreadsheet import)
 *
 * Designed for stock traders who need to:
 *   - Analyze trades offline in Excel / Google Sheets
 *   - Archive historical data for compliance
 *   - Share performance reports with advisors
 *   - Import data into third-party analysis tools
 */

import type {
  TradeRecord,
  LivePosition,
  BacktestResult,
  Portfolio,
  PortfolioPosition,
} from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import type { PerformanceReport } from "./performance-metrics.js";
import type { TradeNote } from "./trade-journal.js";

// ── Types ────────────────────────────────────────────────────

export type ExportFormat = "csv" | "json" | "tsv";

export interface ExportOptions {
  /** Output format (default: "csv") */
  format?: ExportFormat;
  /** Include UTF-8 BOM for CSV/TSV (default: true) */
  includeBom?: boolean;
  /** Date range filter */
  dateRange?: { from?: string; to?: string };
  /** Columns to include (default: all); only applies to CSV/TSV */
  columns?: string[];
  /** Custom column headers (mapping from field name to display name) */
  columnHeaders?: Record<string, string>;
  /** Number precision for decimal values (default: 4) */
  precision?: number;
}

export interface ExportResult {
  /** The exported data as a string */
  content: string;
  /** Suggested filename */
  filename: string;
  /** MIME type */
  mimeType: string;
  /** Number of rows exported (excluding header) */
  rowCount: number;
}

// ── CSV / TSV Helpers ────────────────────────────────────────

const UTF8_BOM = "\uFEFF";

function escapeCsvField(value: unknown): string {
  if (value == null) return "";
  const str = String(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n") || str.includes("\r")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

function escapeTsvField(value: unknown): string {
  if (value == null) return "";
  const str = String(value);
  // TSV: replace tabs and newlines
  return str.replace(/\t/g, " ").replace(/[\r\n]+/g, " ");
}

function formatNumber(value: number | undefined | null, precision: number): string {
  if (value == null || isNaN(value)) return "";
  return Number(value.toFixed(precision)).toString();
}

function formatPercent(value: number | undefined | null, precision: number): string {
  if (value == null || isNaN(value)) return "";
  return `${(value * 100).toFixed(Math.max(precision - 2, 2))}%`;
}

function toCsvRow(fields: unknown[], separator: string): string {
  const escapeFn = separator === "\t" ? escapeTsvField : escapeCsvField;
  return fields.map(escapeFn).join(separator);
}

function buildTable(
  rows: Array<Record<string, unknown>>,
  allColumns: string[],
  opts: ExportOptions,
  separator: string,
): string {
  const cols = opts.columns ?? allColumns;
  const headers = cols.map((c) => opts.columnHeaders?.[c] ?? c);
  const lines: string[] = [];

  // BOM
  const bom = opts.includeBom !== false ? UTF8_BOM : "";

  // Header
  lines.push(toCsvRow(headers, separator));

  // Data rows
  for (const row of rows) {
    const values = cols.map((c) => row[c]);
    lines.push(toCsvRow(values, separator));
  }

  return bom + lines.join("\n");
}

function filterByDateRange<T extends { date?: string }>(
  items: T[],
  range?: ExportOptions["dateRange"],
  dateField: keyof T = "date" as keyof T,
): T[] {
  if (!range) return items;
  return items.filter((item) => {
    const d = String(item[dateField] ?? "");
    if (!d) return true;
    const dateStr = d.slice(0, 10);
    if (range.from && dateStr < range.from) return false;
    if (range.to && dateStr > range.to) return false;
    return true;
  });
}

// ── Trade Records Export ─────────────────────────────────────

const TRADE_COLUMNS = [
  "tradeId",
  "symbol",
  "name",
  "market",
  "side",
  "entryTime",
  "exitTime",
  "entryPrice",
  "exitPrice",
  "quantity",
  "grossPnl",
  "commission",
  "netPnl",
  "holdingDays",
  "strategyId",
];

const TRADE_HEADERS: Record<string, string> = {
  tradeId: "交易ID",
  symbol: "代码",
  name: "名称",
  market: "市场",
  side: "方向",
  entryTime: "开仓时间",
  exitTime: "平仓时间",
  entryPrice: "开仓价",
  exitPrice: "平仓价",
  quantity: "数量",
  grossPnl: "毛利润",
  commission: "手续费",
  netPnl: "净利润",
  holdingDays: "持仓天数",
  strategyId: "策略ID",
};

export function exportTrades(trades: TradeRecord[], opts: ExportOptions = {}): ExportResult {
  const precision = opts.precision ?? 4;
  const format = opts.format ?? "csv";

  // Date range filter on entryTime
  const filtered = trades.filter((t) => {
    if (!opts.dateRange) return true;
    const d = t.entryTime.slice(0, 10);
    if (opts.dateRange.from && d < opts.dateRange.from) return false;
    if (opts.dateRange.to && d > opts.dateRange.to) return false;
    return true;
  });

  const rows = filtered.map((t) => ({
    tradeId: t.tradeId,
    symbol: t.symbol,
    name: t.name ?? "",
    market: t.market,
    side: t.side,
    entryTime: t.entryTime,
    exitTime: t.exitTime ?? "",
    entryPrice: formatNumber(t.entryPrice, precision),
    exitPrice: formatNumber(t.exitPrice, precision),
    quantity: t.quantity,
    grossPnl: formatNumber(t.grossPnl, precision),
    commission: formatNumber(t.commission, precision),
    netPnl: formatNumber(t.netPnl, precision),
    holdingDays: t.holdingDays ?? "",
    strategyId: t.strategyId ?? "",
  }));

  const mergedHeaders = { ...TRADE_HEADERS, ...opts.columnHeaders };
  const mergedOpts = { ...opts, columnHeaders: mergedHeaders };

  if (format === "json") {
    return {
      content: JSON.stringify(filtered, null, 2),
      filename: "trades.json",
      mimeType: "application/json",
      rowCount: filtered.length,
    };
  }

  const separator = format === "tsv" ? "\t" : ",";
  const ext = format === "tsv" ? "tsv" : "csv";

  return {
    content: buildTable(rows, TRADE_COLUMNS, mergedOpts, separator),
    filename: `trades.${ext}`,
    mimeType: format === "tsv" ? "text/tab-separated-values" : "text/csv",
    rowCount: filtered.length,
  };
}

// ── Positions Export ─────────────────────────────────────────

const POSITION_COLUMNS = [
  "symbol",
  "name",
  "market",
  "quantity",
  "availableQty",
  "frozenQty",
  "avgCost",
  "currentPrice",
  "marketValue",
  "unrealizedPnl",
  "unrealizedPnlPct",
  "source",
  "lastSyncAt",
];

const POSITION_HEADERS: Record<string, string> = {
  symbol: "代码",
  name: "名称",
  market: "市场",
  quantity: "持仓量",
  availableQty: "可用数量",
  frozenQty: "冻结数量",
  avgCost: "均价",
  currentPrice: "现价",
  marketValue: "市值",
  unrealizedPnl: "浮动盈亏",
  unrealizedPnlPct: "浮动盈亏%",
  source: "来源",
  lastSyncAt: "最后同步",
};

export function exportPositions(positions: LivePosition[], opts: ExportOptions = {}): ExportResult {
  const precision = opts.precision ?? 4;
  const format = opts.format ?? "csv";

  const rows = positions.map((p) => ({
    symbol: p.symbol,
    name: p.name ?? "",
    market: p.market,
    quantity: p.quantity,
    availableQty: p.availableQty,
    frozenQty: p.frozenQty,
    avgCost: formatNumber(p.avgCost, precision),
    currentPrice: formatNumber(p.currentPrice, precision),
    marketValue: formatNumber(p.marketValue, precision),
    unrealizedPnl: formatNumber(p.unrealizedPnl, precision),
    unrealizedPnlPct: formatPercent(p.unrealizedPnlPct, precision),
    source: p.source,
    lastSyncAt: p.lastSyncAt,
  }));

  const mergedHeaders = { ...POSITION_HEADERS, ...opts.columnHeaders };
  const mergedOpts = { ...opts, columnHeaders: mergedHeaders };

  if (format === "json") {
    return {
      content: JSON.stringify(positions, null, 2),
      filename: "positions.json",
      mimeType: "application/json",
      rowCount: positions.length,
    };
  }

  const separator = format === "tsv" ? "\t" : ",";
  const ext = format === "tsv" ? "tsv" : "csv";

  return {
    content: buildTable(rows, POSITION_COLUMNS, mergedOpts, separator),
    filename: `positions.${ext}`,
    mimeType: format === "tsv" ? "text/tab-separated-values" : "text/csv",
    rowCount: positions.length,
  };
}

// ── Equity History Export ────────────────────────────────────

const EQUITY_COLUMNS = ["date", "equity", "cash", "marketValue", "dailyPnl", "positionCount"];

const EQUITY_HEADERS: Record<string, string> = {
  date: "日期",
  equity: "总权益",
  cash: "现金",
  marketValue: "持仓市值",
  dailyPnl: "当日盈亏",
  positionCount: "持仓数",
};

export function exportEquityHistory(
  snapshots: EquitySnapshot[],
  opts: ExportOptions = {},
): ExportResult {
  const precision = opts.precision ?? 2;
  const format = opts.format ?? "csv";

  const filtered = filterByDateRange(snapshots, opts.dateRange);

  const rows = filtered.map((s) => ({
    date: s.date,
    equity: formatNumber(s.equity, precision),
    cash: formatNumber(s.cash, precision),
    marketValue: formatNumber(s.marketValue, precision),
    dailyPnl: formatNumber(s.dailyPnl, precision),
    positionCount: s.positionCount,
  }));

  const mergedHeaders = { ...EQUITY_HEADERS, ...opts.columnHeaders };
  const mergedOpts = { ...opts, columnHeaders: mergedHeaders };

  if (format === "json") {
    return {
      content: JSON.stringify(filtered, null, 2),
      filename: "equity-history.json",
      mimeType: "application/json",
      rowCount: filtered.length,
    };
  }

  const separator = format === "tsv" ? "\t" : ",";
  const ext = format === "tsv" ? "tsv" : "csv";

  return {
    content: buildTable(rows, EQUITY_COLUMNS, mergedOpts, separator),
    filename: `equity-history.${ext}`,
    mimeType: format === "tsv" ? "text/tab-separated-values" : "text/csv",
    rowCount: filtered.length,
  };
}

// ── Performance Report Export ────────────────────────────────

export function exportPerformanceReport(
  report: PerformanceReport,
  opts: ExportOptions = {},
): ExportResult {
  const format = opts.format ?? "json";

  if (format === "json") {
    return {
      content: JSON.stringify(report, null, 2),
      filename: "performance-report.json",
      mimeType: "application/json",
      rowCount: 1,
    };
  }

  // Flatten the report into key-value rows for CSV/TSV
  const rows = flattenPerformanceReport(report);
  const columns = ["category", "metric", "value"];
  const headers: Record<string, string> = {
    category: "分类",
    metric: "指标",
    value: "值",
  };

  const mergedOpts = { ...opts, columnHeaders: { ...headers, ...opts.columnHeaders } };
  const separator = format === "tsv" ? "\t" : ",";
  const ext = format === "tsv" ? "tsv" : "csv";

  return {
    content: buildTable(rows, columns, mergedOpts, separator),
    filename: `performance-report.${ext}`,
    mimeType: format === "tsv" ? "text/tab-separated-values" : "text/csv",
    rowCount: rows.length,
  };
}

function flattenPerformanceReport(
  report: PerformanceReport,
): Array<Record<string, unknown>> {
  const rows: Array<Record<string, unknown>> = [];

  rows.push({ category: "期间", metric: "开始日期", value: report.dateRange.from });
  rows.push({ category: "期间", metric: "结束日期", value: report.dateRange.to });
  rows.push({ category: "期间", metric: "天数", value: report.periodDays });

  rows.push({ category: "收益", metric: "总收益率", value: formatPercent(report.returns.totalReturn, 4) });
  rows.push({ category: "收益", metric: "年化收益率", value: formatPercent(report.returns.annualizedReturn, 4) });
  rows.push({ category: "收益", metric: "年化波动率", value: formatPercent(report.returns.annualizedVolatility, 4) });

  rows.push({ category: "回撤", metric: "最大回撤", value: formatPercent(report.drawdown.maxDrawdown, 4) });
  rows.push({ category: "回撤", metric: "最大回撤天数", value: report.drawdown.maxDrawdownDuration });
  rows.push({ category: "回撤", metric: "当前回撤", value: formatPercent(report.drawdown.currentDrawdown, 4) });

  rows.push({ category: "风险调整", metric: "夏普比率", value: report.riskAdjusted.sharpeRatio });
  rows.push({ category: "风险调整", metric: "索提诺比率", value: report.riskAdjusted.sortinoRatio });
  rows.push({ category: "风险调整", metric: "卡玛比率", value: report.riskAdjusted.calmarRatio });
  rows.push({ category: "风险调整", metric: "VaR(95%)", value: formatPercent(report.riskAdjusted.var95, 4) });

  rows.push({ category: "交易分析", metric: "总交易数", value: report.tradeAnalysis.totalTrades });
  rows.push({ category: "交易分析", metric: "胜率", value: formatPercent(report.tradeAnalysis.winRate, 4) });
  rows.push({ category: "交易分析", metric: "盈亏比", value: report.tradeAnalysis.payoffRatio });
  rows.push({ category: "交易分析", metric: "期望值", value: report.tradeAnalysis.expectancy });
  rows.push({ category: "交易分析", metric: "最大连胜", value: report.tradeAnalysis.maxConsecutiveWins });
  rows.push({ category: "交易分析", metric: "最大连亏", value: report.tradeAnalysis.maxConsecutiveLosses });

  rows.push({ category: "资金", metric: "初始资金", value: report.capital.initialCapital });
  rows.push({ category: "资金", metric: "最终净值", value: report.capital.finalValue });
  rows.push({ category: "资金", metric: "净利润", value: report.capital.netProfit });

  return rows;
}

// ── Trade Journal Export ─────────────────────────────────────

const JOURNAL_COLUMNS = [
  "id",
  "symbol",
  "date",
  "direction",
  "entryPrice",
  "exitPrice",
  "pnl",
  "rating",
  "tags",
  "content",
  "lesson",
  "strategyId",
];

const JOURNAL_HEADERS: Record<string, string> = {
  id: "笔记ID",
  symbol: "代码",
  date: "日期",
  direction: "方向",
  entryPrice: "买入价",
  exitPrice: "卖出价",
  pnl: "盈亏",
  rating: "评分",
  tags: "标签",
  content: "内容",
  lesson: "经验教训",
  strategyId: "策略ID",
};

export function exportTradeJournal(notes: TradeNote[], opts: ExportOptions = {}): ExportResult {
  const precision = opts.precision ?? 2;
  const format = opts.format ?? "csv";

  const filtered = filterByDateRange(notes, opts.dateRange);

  const rows = filtered.map((n) => ({
    id: n.id,
    symbol: n.symbol,
    date: n.date,
    direction: n.direction ?? "",
    entryPrice: formatNumber(n.entryPrice, precision),
    exitPrice: formatNumber(n.exitPrice, precision),
    pnl: formatNumber(n.pnl, precision),
    rating: n.rating ?? "",
    tags: n.tags.join("; "),
    content: n.content,
    lesson: n.lesson ?? "",
    strategyId: n.strategyId ?? "",
  }));

  const mergedHeaders = { ...JOURNAL_HEADERS, ...opts.columnHeaders };
  const mergedOpts = { ...opts, columnHeaders: mergedHeaders };

  if (format === "json") {
    return {
      content: JSON.stringify(filtered, null, 2),
      filename: "trade-journal.json",
      mimeType: "application/json",
      rowCount: filtered.length,
    };
  }

  const separator = format === "tsv" ? "\t" : ",";
  const ext = format === "tsv" ? "tsv" : "csv";

  return {
    content: buildTable(rows, JOURNAL_COLUMNS, mergedOpts, separator),
    filename: `trade-journal.${ext}`,
    mimeType: format === "tsv" ? "text/tab-separated-values" : "text/csv",
    rowCount: filtered.length,
  };
}

// ── Backtest Result Export ───────────────────────────────────

export function exportBacktestResult(result: BacktestResult, opts: ExportOptions = {}): ExportResult {
  const format = opts.format ?? "json";

  if (format === "json") {
    return {
      content: JSON.stringify(result, null, 2),
      filename: "backtest-result.json",
      mimeType: "application/json",
      rowCount: 1,
    };
  }

  // For CSV/TSV, export the equity curve as time series
  const precision = opts.precision ?? 2;
  const curve = result.equityCurve ?? [];
  const rows = curve.map((p) => ({
    date: p.date,
    value: formatNumber(p.value, precision),
  }));

  const columns = ["date", "value"];
  const headers: Record<string, string> = { date: "日期", value: "净值" };
  const mergedOpts = { ...opts, columnHeaders: { ...headers, ...opts.columnHeaders } };
  const separator = format === "tsv" ? "\t" : ",";
  const ext = format === "tsv" ? "tsv" : "csv";

  return {
    content: buildTable(rows, columns, mergedOpts, separator),
    filename: `backtest-result.${ext}`,
    mimeType: format === "tsv" ? "text/tab-separated-values" : "text/csv",
    rowCount: rows.length,
  };
}
