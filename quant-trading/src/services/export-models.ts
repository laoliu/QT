/**
 * Exportable Data Models — unified intermediate data models for data export.
 *
 * Defines a generic record structure with field metadata and type mappings,
 * plus concrete export models for:
 *   - Trade records (交易记录)
 *   - Positions (持仓)
 *   - Performance metrics (绩效)
 *
 * These models serve as the canonical intermediate representation between
 * in-memory domain objects and serialized export formats (CSV/Excel/JSON).
 */

import type {
  MarketType,
  OrderSide,
  OrderType,
  OrderStatus,
  TradeRecord,
  PortfolioPosition,
  LivePosition,
  Order,
  Fill,
} from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import type {
  AttributionResult,
  StrategyAttribution,
  SymbolAttribution,
  PeriodPnl,
} from "./performance-attribution.js";
import type { FavoriteStock } from "./favorites-store.js";
import type { DcaPlan } from "./dca-schedule.js";

// ── Export Format ────────────────────────────────────────────

export type ExportFormat = "csv" | "excel" | "json";

// ── Field Metadata ──────────────────────────────────────────

/** Primitive types supported in export columns. */
export type ExportFieldType = "string" | "number" | "boolean" | "date" | "datetime";

/**
 * Metadata describing a single exportable field.
 * Used by export renderers to format headers, align columns, and apply
 * locale-specific formatting (e.g. date patterns, decimal precision).
 */
export interface ExportFieldMeta {
  /** Machine-readable field key (matches the property name in the record). */
  key: string;
  /** Human-readable column header for display / CSV header row. */
  label: string;
  /** Chinese label (optional, for bilingual export). */
  labelZh?: string;
  /** Data type for formatting and alignment. */
  type: ExportFieldType;
  /** Number of decimal places for numeric fields (default: 2). */
  precision?: number;
  /** printf-style or dayjs-style format string (e.g. "YYYY-MM-DD HH:mm:ss"). */
  format?: string;
  /** Column width hint in characters (for Excel). */
  width?: number;
  /** Whether this field should be included by default (default: true). */
  defaultVisible?: boolean;
}

// ── Generic Exportable Record ───────────────────────────────

/**
 * A generic exportable record — pairs a flat data row with its field schema.
 *
 * `T` is a plain object whose keys correspond to the `key` values in `fields`.
 * Export renderers iterate `fields` to produce ordered columns and use `data`
 * to fill each row.
 */
export interface ExportableRecord<T extends object> {
  /** Ordered field descriptors — determines column order and formatting. */
  fields: ExportFieldMeta[];
  /** The data rows to export. */
  data: T[];
  /** Optional metadata about the export itself. */
  meta?: ExportMeta;
}

/** Metadata attached to an export batch. */
export interface ExportMeta {
  /** Human-readable title (e.g. "交易记录导出"). */
  title?: string;
  /** ISO datetime when the export was generated. */
  exportedAt: string;
  /** Total number of records. */
  totalRecords: number;
  /** Optional date range filter that was applied. */
  dateRange?: { from?: string; to?: string };
}

// ── Trade Record Export ─────────────────────────────────────

/** Flat representation of a trade record for export. */
export interface TradeRecordExportRow {
  tradeId: string;
  symbol: string;
  name: string;
  market: MarketType;
  side: OrderSide;
  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number;
  quantity: number;
  grossPnl: number;
  commission: number;
  netPnl: number;
  netPnlPct: number;
  holdingDays: number;
  strategyId: string;
}

export type TradeRecordExport = ExportableRecord<TradeRecordExportRow>;

/** Field metadata for trade record exports. */
export const TRADE_RECORD_FIELDS: ExportFieldMeta[] = [
  { key: "tradeId", label: "Trade ID", labelZh: "交易编号", type: "string", width: 16 },
  { key: "symbol", label: "Symbol", labelZh: "代码", type: "string", width: 10 },
  { key: "name", label: "Name", labelZh: "名称", type: "string", width: 12 },
  { key: "market", label: "Market", labelZh: "市场", type: "string", width: 10 },
  { key: "side", label: "Side", labelZh: "方向", type: "string", width: 8 },
  { key: "entryTime", label: "Entry Time", labelZh: "买入时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
  { key: "exitTime", label: "Exit Time", labelZh: "卖出时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
  { key: "entryPrice", label: "Entry Price", labelZh: "买入价", type: "number", precision: 3, width: 12 },
  { key: "exitPrice", label: "Exit Price", labelZh: "卖出价", type: "number", precision: 3, width: 12 },
  { key: "quantity", label: "Quantity", labelZh: "数量", type: "number", precision: 0, width: 10 },
  { key: "grossPnl", label: "Gross P&L", labelZh: "毛盈亏", type: "number", precision: 2, width: 12 },
  { key: "commission", label: "Commission", labelZh: "手续费", type: "number", precision: 2, width: 12 },
  { key: "netPnl", label: "Net P&L", labelZh: "净盈亏", type: "number", precision: 2, width: 12 },
  { key: "netPnlPct", label: "Net P&L %", labelZh: "净盈亏率", type: "number", precision: 2, format: "0.00%", width: 10 },
  { key: "holdingDays", label: "Holding Days", labelZh: "持仓天数", type: "number", precision: 0, width: 10 },
  { key: "strategyId", label: "Strategy", labelZh: "策略", type: "string", width: 16 },
];

/**
 * Convert domain TradeRecord[] into an exportable structure.
 */
export function toTradeRecordExport(
  trades: TradeRecord[],
  opts?: { from?: string; to?: string },
): TradeRecordExport {
  let filtered = trades;
  if (opts?.from) {
    filtered = filtered.filter((t) => t.entryTime >= opts.from!);
  }
  if (opts?.to) {
    const to = opts.to;
    filtered = filtered.filter((t) => t.entryTime <= to);
  }

  const data: TradeRecordExportRow[] = filtered.map((t) => ({
    tradeId: t.tradeId,
    symbol: t.symbol,
    name: t.name ?? "",
    market: t.market,
    side: t.side,
    entryTime: t.entryTime,
    exitTime: t.exitTime ?? "",
    entryPrice: t.entryPrice,
    exitPrice: t.exitPrice ?? 0,
    quantity: t.quantity,
    grossPnl: t.grossPnl,
    commission: t.commission,
    netPnl: t.netPnl,
    netPnlPct: t.entryPrice > 0 ? (t.netPnl / (t.entryPrice * t.quantity)) * 100 : 0,
    holdingDays: t.holdingDays ?? 0,
    strategyId: t.strategyId ?? "",
  }));

  return {
    fields: TRADE_RECORD_FIELDS,
    data,
    meta: {
      title: "交易记录导出",
      exportedAt: new Date().toISOString(),
      totalRecords: data.length,
      dateRange: opts,
    },
  };
}

// ── Position Export ──────────────────────────────────────────

/** Flat representation of a position for export. */
export interface PositionExportRow {
  symbol: string;
  name: string;
  market: MarketType;
  quantity: number;
  availableQty: number;
  avgCost: number;
  currentPrice: number;
  marketValue: number;
  cost: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
  weight: number;
  /** Source: "paper" for PortfolioPosition, "live" for LivePosition. */
  source: string;
}

export type PositionExport = ExportableRecord<PositionExportRow>;

/** Field metadata for position exports. */
export const POSITION_FIELDS: ExportFieldMeta[] = [
  { key: "symbol", label: "Symbol", labelZh: "代码", type: "string", width: 10 },
  { key: "name", label: "Name", labelZh: "名称", type: "string", width: 12 },
  { key: "market", label: "Market", labelZh: "市场", type: "string", width: 10 },
  { key: "quantity", label: "Quantity", labelZh: "持仓数量", type: "number", precision: 0, width: 10 },
  { key: "availableQty", label: "Available", labelZh: "可用数量", type: "number", precision: 0, width: 10 },
  { key: "avgCost", label: "Avg Cost", labelZh: "成本价", type: "number", precision: 3, width: 12 },
  { key: "currentPrice", label: "Current Price", labelZh: "现价", type: "number", precision: 3, width: 12 },
  { key: "marketValue", label: "Market Value", labelZh: "市值", type: "number", precision: 2, width: 14 },
  { key: "cost", label: "Cost", labelZh: "成本", type: "number", precision: 2, width: 14 },
  { key: "unrealizedPnl", label: "Unrealized P&L", labelZh: "浮动盈亏", type: "number", precision: 2, width: 14 },
  { key: "unrealizedPnlPct", label: "Unrealized P&L %", labelZh: "浮动盈亏率", type: "number", precision: 2, format: "0.00%", width: 12 },
  { key: "weight", label: "Weight %", labelZh: "仓位占比", type: "number", precision: 2, format: "0.00%", width: 10 },
  { key: "source", label: "Source", labelZh: "来源", type: "string", width: 8 },
];

/**
 * Convert paper PortfolioPosition[] into an exportable structure.
 */
export function toPositionExportFromPortfolio(
  positions: PortfolioPosition[],
  totalValue?: number,
): PositionExport {
  const total = totalValue ?? positions.reduce((s, p) => s + (p.marketValue ?? p.avgCost * p.quantity), 0);

  const data: PositionExportRow[] = positions.map((p) => {
    const mv = p.marketValue ?? (p.currentPrice ?? p.avgCost) * p.quantity;
    const cost = p.avgCost * p.quantity;
    const pnl = p.unrealizedPnl ?? mv - cost;
    return {
      symbol: p.symbol,
      name: p.name ?? "",
      market: p.market,
      quantity: p.quantity,
      availableQty: p.quantity, // paper positions are always fully available
      avgCost: p.avgCost,
      currentPrice: p.currentPrice ?? p.avgCost,
      marketValue: mv,
      cost,
      unrealizedPnl: pnl,
      unrealizedPnlPct: cost > 0 ? (pnl / cost) * 100 : 0,
      weight: total > 0 ? (mv / total) * 100 : 0,
      source: "paper",
    };
  });

  return {
    fields: POSITION_FIELDS,
    data,
    meta: {
      title: "持仓导出",
      exportedAt: new Date().toISOString(),
      totalRecords: data.length,
    },
  };
}

/**
 * Convert live LivePosition[] into an exportable structure.
 */
export function toPositionExportFromLive(
  positions: LivePosition[],
  totalValue?: number,
): PositionExport {
  const total = totalValue ?? positions.reduce((s, p) => s + (p.marketValue ?? p.avgCost * p.quantity), 0);

  const data: PositionExportRow[] = positions.map((p) => {
    const mv = p.marketValue ?? (p.currentPrice ?? p.avgCost) * p.quantity;
    const cost = p.avgCost * p.quantity;
    const pnl = p.unrealizedPnl ?? mv - cost;
    return {
      symbol: p.symbol,
      name: p.name ?? "",
      market: p.market,
      quantity: p.quantity,
      availableQty: p.availableQty,
      avgCost: p.avgCost,
      currentPrice: p.currentPrice ?? p.avgCost,
      marketValue: mv,
      cost,
      unrealizedPnl: pnl,
      unrealizedPnlPct: cost > 0 ? (pnl / cost) * 100 : 0,
      weight: total > 0 ? (mv / total) * 100 : 0,
      source: "live",
    };
  });

  return {
    fields: POSITION_FIELDS,
    data,
    meta: {
      title: "持仓导出",
      exportedAt: new Date().toISOString(),
      totalRecords: data.length,
    },
  };
}

// ── Performance Export ───────────────────────────────────────

/** Summary-level performance metrics row. */
export interface PerformanceSummaryRow {
  dateFrom: string;
  dateTo: string;
  totalTrades: number;
  totalNetPnl: number;
  totalGrossPnl: number;
  totalCommission: number;
  overallWinRate: number;
  avgWin: number;
  avgLoss: number;
  profitFactor: number;
}

/** Per-strategy attribution row. */
export interface StrategyAttributionRow {
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
  profitFactor: number;
  contributionPct: number;
}

/** Per-symbol attribution row. */
export interface SymbolAttributionRow {
  symbol: string;
  trades: number;
  netPnl: number;
  avgPnl: number;
  winRate: number;
  holdingDaysAvg: number;
  contributionPct: number;
}

/** Periodic P&L row (daily/weekly/monthly). */
export interface PeriodPnlRow {
  period: string;
  trades: number;
  netPnl: number;
  cumPnl: number;
  wins: number;
  losses: number;
}

/** Equity curve row. */
export interface EquityCurveRow {
  date: string;
  equity: number;
  cash: number;
  marketValue: number;
  dailyPnl: number;
  positionCount: number;
  benchmark: number;
}

/**
 * Composite performance export containing multiple related sheets/sections.
 * Each section is an ExportableRecord that can be rendered as a separate
 * sheet (Excel) or separate file/section (CSV/JSON).
 */
export interface PerformanceExport {
  summary: ExportableRecord<PerformanceSummaryRow>;
  byStrategy: ExportableRecord<StrategyAttributionRow>;
  bySymbol: ExportableRecord<SymbolAttributionRow>;
  byPeriod: ExportableRecord<PeriodPnlRow>;
  equityCurve: ExportableRecord<EquityCurveRow>;
  meta: ExportMeta;
}

/** Field metadata for each performance section. */
export const PERFORMANCE_SUMMARY_FIELDS: ExportFieldMeta[] = [
  { key: "dateFrom", label: "From", labelZh: "起始日期", type: "date", format: "YYYY-MM-DD", width: 12 },
  { key: "dateTo", label: "To", labelZh: "截止日期", type: "date", format: "YYYY-MM-DD", width: 12 },
  { key: "totalTrades", label: "Total Trades", labelZh: "总交易次数", type: "number", precision: 0, width: 12 },
  { key: "totalNetPnl", label: "Total Net P&L", labelZh: "总净盈亏", type: "number", precision: 2, width: 14 },
  { key: "totalGrossPnl", label: "Total Gross P&L", labelZh: "总毛盈亏", type: "number", precision: 2, width: 14 },
  { key: "totalCommission", label: "Total Commission", labelZh: "总手续费", type: "number", precision: 2, width: 14 },
  { key: "overallWinRate", label: "Win Rate %", labelZh: "胜率", type: "number", precision: 2, format: "0.00%", width: 10 },
  { key: "avgWin", label: "Avg Win", labelZh: "平均盈利", type: "number", precision: 2, width: 12 },
  { key: "avgLoss", label: "Avg Loss", labelZh: "平均亏损", type: "number", precision: 2, width: 12 },
  { key: "profitFactor", label: "Profit Factor", labelZh: "盈亏比", type: "number", precision: 2, width: 12 },
];

export const STRATEGY_ATTRIBUTION_FIELDS: ExportFieldMeta[] = [
  { key: "strategyId", label: "Strategy", labelZh: "策略", type: "string", width: 16 },
  { key: "trades", label: "Trades", labelZh: "交易次数", type: "number", precision: 0, width: 8 },
  { key: "wins", label: "Wins", labelZh: "盈利次数", type: "number", precision: 0, width: 8 },
  { key: "losses", label: "Losses", labelZh: "亏损次数", type: "number", precision: 0, width: 8 },
  { key: "winRate", label: "Win Rate %", labelZh: "胜率", type: "number", precision: 2, format: "0.00%", width: 10 },
  { key: "grossPnl", label: "Gross P&L", labelZh: "毛盈亏", type: "number", precision: 2, width: 12 },
  { key: "netPnl", label: "Net P&L", labelZh: "净盈亏", type: "number", precision: 2, width: 12 },
  { key: "avgWin", label: "Avg Win", labelZh: "平均盈利", type: "number", precision: 2, width: 12 },
  { key: "avgLoss", label: "Avg Loss", labelZh: "平均亏损", type: "number", precision: 2, width: 12 },
  { key: "largestWin", label: "Largest Win", labelZh: "最大盈利", type: "number", precision: 2, width: 12 },
  { key: "largestLoss", label: "Largest Loss", labelZh: "最大亏损", type: "number", precision: 2, width: 12 },
  { key: "profitFactor", label: "Profit Factor", labelZh: "盈亏比", type: "number", precision: 2, width: 12 },
  { key: "contributionPct", label: "Contribution %", labelZh: "贡献占比", type: "number", precision: 2, format: "0.00%", width: 12 },
];

export const SYMBOL_ATTRIBUTION_FIELDS: ExportFieldMeta[] = [
  { key: "symbol", label: "Symbol", labelZh: "代码", type: "string", width: 10 },
  { key: "trades", label: "Trades", labelZh: "交易次数", type: "number", precision: 0, width: 8 },
  { key: "netPnl", label: "Net P&L", labelZh: "净盈亏", type: "number", precision: 2, width: 12 },
  { key: "avgPnl", label: "Avg P&L", labelZh: "平均盈亏", type: "number", precision: 2, width: 12 },
  { key: "winRate", label: "Win Rate %", labelZh: "胜率", type: "number", precision: 2, format: "0.00%", width: 10 },
  { key: "holdingDaysAvg", label: "Avg Hold Days", labelZh: "平均持仓天数", type: "number", precision: 1, width: 12 },
  { key: "contributionPct", label: "Contribution %", labelZh: "贡献占比", type: "number", precision: 2, format: "0.00%", width: 12 },
];

export const PERIOD_PNL_FIELDS: ExportFieldMeta[] = [
  { key: "period", label: "Period", labelZh: "期间", type: "string", width: 12 },
  { key: "trades", label: "Trades", labelZh: "交易次数", type: "number", precision: 0, width: 8 },
  { key: "netPnl", label: "Net P&L", labelZh: "净盈亏", type: "number", precision: 2, width: 12 },
  { key: "cumPnl", label: "Cumulative P&L", labelZh: "累计盈亏", type: "number", precision: 2, width: 14 },
  { key: "wins", label: "Wins", labelZh: "盈利次数", type: "number", precision: 0, width: 8 },
  { key: "losses", label: "Losses", labelZh: "亏损次数", type: "number", precision: 0, width: 8 },
];

export const EQUITY_CURVE_FIELDS: ExportFieldMeta[] = [
  { key: "date", label: "Date", labelZh: "日期", type: "datetime", format: "YYYY-MM-DD", width: 12 },
  { key: "equity", label: "Equity", labelZh: "总权益", type: "number", precision: 2, width: 14 },
  { key: "cash", label: "Cash", labelZh: "现金", type: "number", precision: 2, width: 14 },
  { key: "marketValue", label: "Market Value", labelZh: "持仓市值", type: "number", precision: 2, width: 14 },
  { key: "dailyPnl", label: "Daily P&L", labelZh: "当日盈亏", type: "number", precision: 2, width: 12 },
  { key: "positionCount", label: "Positions", labelZh: "持仓数", type: "number", precision: 0, width: 10 },
  { key: "benchmark", label: "Benchmark", labelZh: "基准", type: "number", precision: 2, width: 14, defaultVisible: false },
];

/**
 * Convert an AttributionResult + equity snapshots into a composite PerformanceExport.
 */
export function toPerformanceExport(
  attribution: AttributionResult,
  equitySnapshots?: EquitySnapshot[],
  opts?: { periodGranularity?: "daily" | "weekly" | "monthly" },
): PerformanceExport {
  const now = new Date().toISOString();
  const dateRange = { from: attribution.dateRange.from, to: attribution.dateRange.to };

  const summaryRow: PerformanceSummaryRow = {
    dateFrom: attribution.dateRange.from,
    dateTo: attribution.dateRange.to,
    totalTrades: attribution.totalTrades,
    totalNetPnl: attribution.totalNetPnl,
    totalGrossPnl: attribution.totalGrossPnl,
    totalCommission: attribution.totalCommission,
    overallWinRate: attribution.overallWinRate,
    avgWin: attribution.avgWin,
    avgLoss: attribution.avgLoss,
    profitFactor: attribution.profitFactor,
  };

  const strategyRows: StrategyAttributionRow[] = attribution.byStrategy.map((s) => ({
    strategyId: s.strategyId,
    trades: s.trades,
    wins: s.wins,
    losses: s.losses,
    winRate: s.winRate,
    grossPnl: s.grossPnl,
    netPnl: s.netPnl,
    avgWin: s.avgWin,
    avgLoss: s.avgLoss,
    largestWin: s.largestWin,
    largestLoss: s.largestLoss,
    profitFactor: s.profitFactor,
    contributionPct: s.contributionPct,
  }));

  const symbolRows: SymbolAttributionRow[] = attribution.bySymbol.map((s) => ({
    symbol: s.symbol,
    trades: s.trades,
    netPnl: s.netPnl,
    avgPnl: s.avgPnl,
    winRate: s.winRate,
    holdingDaysAvg: s.holdingDaysAvg,
    contributionPct: s.contributionPct,
  }));

  // Period P&L — use requested granularity (default: monthly)
  const periodSource =
    opts?.periodGranularity === "daily"
      ? attribution.byDay
      : opts?.periodGranularity === "weekly"
        ? attribution.byWeek
        : attribution.byMonth;
  const periodRows: PeriodPnlRow[] = periodSource.map((p) => ({
    period: p.period,
    trades: p.trades,
    netPnl: p.netPnl,
    cumPnl: p.cumPnl,
    wins: p.wins,
    losses: p.losses,
  }));

  const equityRows: EquityCurveRow[] = (equitySnapshots ?? []).map((e) => ({
    date: e.date,
    equity: e.equity,
    cash: e.cash,
    marketValue: e.marketValue,
    dailyPnl: e.dailyPnl,
    positionCount: e.positionCount,
    benchmark: e.benchmark ?? 0,
  }));

  const meta: ExportMeta = {
    title: "绩效分析导出",
    exportedAt: now,
    totalRecords: attribution.totalTrades,
    dateRange,
  };

  return {
    summary: { fields: PERFORMANCE_SUMMARY_FIELDS, data: [summaryRow], meta },
    byStrategy: { fields: STRATEGY_ATTRIBUTION_FIELDS, data: strategyRows, meta },
    bySymbol: { fields: SYMBOL_ATTRIBUTION_FIELDS, data: symbolRows, meta },
    byPeriod: { fields: PERIOD_PNL_FIELDS, data: periodRows, meta },
    equityCurve: { fields: EQUITY_CURVE_FIELDS, data: equityRows, meta },
    meta,
  };
}

// ── Order Export ────────────────────────────────────────────────

/** Flat representation of an order for export. */
export interface OrderExportRow {
  orderId: string;
  brokerOrderId: string;
  symbol: string;
  market: MarketType;
  side: OrderSide;
  orderType: OrderType;
  price: number;
  quantity: number;
  filledQty: number;
  avgFillPrice: number;
  status: OrderStatus;
  strategyId: string;
  errorMsg: string;
  createdAt: string;
  submittedAt: string;
  filledAt: string;
}

export type OrderExport = ExportableRecord<OrderExportRow>;

/** Field metadata for order exports. */
export const ORDER_FIELDS: ExportFieldMeta[] = [
  { key: "orderId", label: "Order ID", labelZh: "订单编号", type: "string", width: 16 },
  { key: "brokerOrderId", label: "Broker Order ID", labelZh: "券商订单号", type: "string", width: 16 },
  { key: "symbol", label: "Symbol", labelZh: "代码", type: "string", width: 10 },
  { key: "market", label: "Market", labelZh: "市场", type: "string", width: 10 },
  { key: "side", label: "Side", labelZh: "方向", type: "string", width: 10 },
  { key: "orderType", label: "Order Type", labelZh: "订单类型", type: "string", width: 10 },
  { key: "price", label: "Price", labelZh: "委托价", type: "number", precision: 3, width: 12 },
  { key: "quantity", label: "Quantity", labelZh: "委托数量", type: "number", precision: 0, width: 10 },
  { key: "filledQty", label: "Filled Qty", labelZh: "成交数量", type: "number", precision: 0, width: 10 },
  { key: "avgFillPrice", label: "Avg Fill Price", labelZh: "成交均价", type: "number", precision: 3, width: 12 },
  { key: "status", label: "Status", labelZh: "状态", type: "string", width: 10 },
  { key: "strategyId", label: "Strategy", labelZh: "策略", type: "string", width: 16 },
  { key: "errorMsg", label: "Error", labelZh: "错误信息", type: "string", width: 20, defaultVisible: false },
  { key: "createdAt", label: "Created", labelZh: "创建时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
  { key: "submittedAt", label: "Submitted", labelZh: "提交时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
  { key: "filledAt", label: "Filled", labelZh: "成交时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
];

/**
 * Convert domain Order[] into an exportable structure.
 */
export function toOrderExport(
  orders: Order[],
  opts?: { from?: string; to?: string },
): OrderExport {
  let filtered = orders;
  if (opts?.from) {
    filtered = filtered.filter((o) => o.createdAt >= opts.from!);
  }
  if (opts?.to) {
    const to = opts.to;
    filtered = filtered.filter((o) => o.createdAt <= to);
  }

  const data: OrderExportRow[] = filtered.map((o) => ({
    orderId: o.orderId,
    brokerOrderId: o.brokerOrderId ?? "",
    symbol: o.symbol,
    market: o.market,
    side: o.side,
    orderType: o.orderType,
    price: o.price ?? 0,
    quantity: o.quantity,
    filledQty: o.filledQty,
    avgFillPrice: o.avgFillPrice ?? 0,
    status: o.status,
    strategyId: o.strategyId ?? "",
    errorMsg: o.errorMsg ?? "",
    createdAt: o.createdAt,
    submittedAt: o.submittedAt ?? "",
    filledAt: o.filledAt ?? "",
  }));

  return {
    fields: ORDER_FIELDS,
    data,
    meta: {
      title: "委托记录导出",
      exportedAt: new Date().toISOString(),
      totalRecords: data.length,
      dateRange: opts,
    },
  };
}

// ── Fill Export ─────────────────────────────────────────────────

/** Flat representation of a fill (execution) for export. */
export interface FillExportRow {
  fillId: string;
  orderId: string;
  symbol: string;
  side: OrderSide;
  price: number;
  quantity: number;
  amount: number;
  commission: number;
  tax: number;
  totalCost: number;
  timestamp: string;
}

export type FillExport = ExportableRecord<FillExportRow>;

/** Field metadata for fill exports. */
export const FILL_FIELDS: ExportFieldMeta[] = [
  { key: "fillId", label: "Fill ID", labelZh: "成交编号", type: "string", width: 16 },
  { key: "orderId", label: "Order ID", labelZh: "订单编号", type: "string", width: 16 },
  { key: "symbol", label: "Symbol", labelZh: "代码", type: "string", width: 10 },
  { key: "side", label: "Side", labelZh: "方向", type: "string", width: 8 },
  { key: "price", label: "Price", labelZh: "成交价", type: "number", precision: 3, width: 12 },
  { key: "quantity", label: "Quantity", labelZh: "成交数量", type: "number", precision: 0, width: 10 },
  { key: "amount", label: "Amount", labelZh: "成交金额", type: "number", precision: 2, width: 14 },
  { key: "commission", label: "Commission", labelZh: "手续费", type: "number", precision: 2, width: 12 },
  { key: "tax", label: "Tax", labelZh: "印花税", type: "number", precision: 2, width: 12 },
  { key: "totalCost", label: "Total Cost", labelZh: "总费用", type: "number", precision: 2, width: 12 },
  { key: "timestamp", label: "Time", labelZh: "成交时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
];

/**
 * Convert domain Fill[] into an exportable structure.
 */
export function toFillExport(
  fills: Fill[],
  opts?: { from?: string; to?: string },
): FillExport {
  let filtered = fills;
  if (opts?.from) {
    filtered = filtered.filter((f) => f.timestamp >= opts.from!);
  }
  if (opts?.to) {
    const to = opts.to;
    filtered = filtered.filter((f) => f.timestamp <= to);
  }

  const data: FillExportRow[] = filtered.map((f) => {
    const amount = f.price * f.quantity;
    return {
      fillId: f.fillId,
      orderId: f.orderId,
      symbol: f.symbol,
      side: f.side,
      price: f.price,
      quantity: f.quantity,
      amount,
      commission: f.commission,
      tax: f.tax ?? 0,
      totalCost: f.commission + (f.tax ?? 0),
      timestamp: f.timestamp,
    };
  });

  return {
    fields: FILL_FIELDS,
    data,
    meta: {
      title: "成交记录导出",
      exportedAt: new Date().toISOString(),
      totalRecords: data.length,
      dateRange: opts,
    },
  };
}

// ── Watchlist Export ────────────────────────────────────────────

/** Flat representation of a watchlist entry for export. */
export interface WatchlistExportRow {
  code: string;
  name: string;
  category: string;
  priority: number;
  priceTarget: number;
  convictionScore: number;
  tags: string;
  notes: string;
  addedAt: string;
  updatedAt: string;
}

export type WatchlistExport = ExportableRecord<WatchlistExportRow>;

/** Field metadata for watchlist exports. */
export const WATCHLIST_FIELDS: ExportFieldMeta[] = [
  { key: "code", label: "Symbol", labelZh: "代码", type: "string", width: 10 },
  { key: "name", label: "Name", labelZh: "名称", type: "string", width: 12 },
  { key: "category", label: "Category", labelZh: "分类", type: "string", width: 12 },
  { key: "priority", label: "Priority", labelZh: "优先级", type: "number", precision: 0, width: 8 },
  { key: "priceTarget", label: "Price Target", labelZh: "目标价", type: "number", precision: 2, width: 12 },
  { key: "convictionScore", label: "Conviction", labelZh: "信心度", type: "number", precision: 0, width: 10 },
  { key: "tags", label: "Tags", labelZh: "标签", type: "string", width: 20 },
  { key: "notes", label: "Notes", labelZh: "备注", type: "string", width: 30 },
  { key: "addedAt", label: "Added", labelZh: "添加时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
  { key: "updatedAt", label: "Updated", labelZh: "更新时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
];

/**
 * Convert FavoriteStock[] into an exportable structure.
 */
export function toWatchlistExport(
  favorites: FavoriteStock[],
  opts?: { category?: string },
): WatchlistExport {
  let filtered = favorites;
  if (opts?.category) {
    filtered = filtered.filter((f) => f.category === opts.category);
  }

  const data: WatchlistExportRow[] = filtered.map((f) => ({
    code: f.code,
    name: f.name ?? "",
    category: f.category ?? "watchlist",
    priority: f.priority ?? 0,
    priceTarget: f.priceTarget ?? 0,
    convictionScore: f.convictionScore ?? 0,
    tags: (f.tags ?? []).join(", "),
    notes: f.notes ?? "",
    addedAt: f.addedAt,
    updatedAt: f.updatedAt,
  }));

  return {
    fields: WATCHLIST_FIELDS,
    data,
    meta: {
      title: "自选股导出",
      exportedAt: new Date().toISOString(),
      totalRecords: data.length,
    },
  };
}

// ── DCA Plan Export ────────────────────────────────────────────

/** Flat representation of a DCA (定投) plan for export. */
export interface DcaPlanExportRow {
  id: string;
  symbol: string;
  amount: number;
  frequency: string;
  status: string;
  executionCount: number;
  totalInvested: number;
  avgCostPerExecution: number;
  lastExecutedAt: string;
  nextExecutionAt: string;
  createdAt: string;
  description: string;
}

export type DcaPlanExport = ExportableRecord<DcaPlanExportRow>;

/** Field metadata for DCA plan exports. */
export const DCA_PLAN_FIELDS: ExportFieldMeta[] = [
  { key: "id", label: "Plan ID", labelZh: "计划编号", type: "string", width: 12 },
  { key: "symbol", label: "Symbol", labelZh: "代码", type: "string", width: 10 },
  { key: "amount", label: "Amount", labelZh: "每期金额", type: "number", precision: 2, width: 12 },
  { key: "frequency", label: "Frequency", labelZh: "频率", type: "string", width: 10 },
  { key: "status", label: "Status", labelZh: "状态", type: "string", width: 10 },
  { key: "executionCount", label: "Executions", labelZh: "执行次数", type: "number", precision: 0, width: 10 },
  { key: "totalInvested", label: "Total Invested", labelZh: "累计投入", type: "number", precision: 2, width: 14 },
  { key: "avgCostPerExecution", label: "Avg Per Exec", labelZh: "每期均额", type: "number", precision: 2, width: 12 },
  { key: "lastExecutedAt", label: "Last Executed", labelZh: "上次执行", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
  { key: "nextExecutionAt", label: "Next Execution", labelZh: "下次执行", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
  { key: "createdAt", label: "Created", labelZh: "创建时间", type: "datetime", format: "YYYY-MM-DD HH:mm:ss", width: 20 },
  { key: "description", label: "Description", labelZh: "描述", type: "string", width: 24 },
];

/**
 * Convert DcaPlan[] into an exportable structure.
 */
export function toDcaPlanExport(
  plans: DcaPlan[],
  opts?: { status?: "active" | "paused" | "cancelled" },
): DcaPlanExport {
  let filtered = plans;
  if (opts?.status) {
    filtered = filtered.filter((p) => p.status === opts.status);
  }

  const data: DcaPlanExportRow[] = filtered.map((p) => ({
    id: p.id,
    symbol: p.symbol,
    amount: p.amount,
    frequency: p.frequency,
    status: p.status,
    executionCount: p.executionCount,
    totalInvested: p.totalInvested,
    avgCostPerExecution: p.executionCount > 0 ? p.totalInvested / p.executionCount : 0,
    lastExecutedAt: p.lastExecutedAt ?? "",
    nextExecutionAt: p.nextExecutionAt ?? "",
    createdAt: p.createdAt,
    description: p.description,
  }));

  return {
    fields: DCA_PLAN_FIELDS,
    data,
    meta: {
      title: "定投计划导出",
      exportedAt: new Date().toISOString(),
      totalRecords: data.length,
    },
  };
}
