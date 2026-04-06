/**
 * DataExportService — unified interface & data models for exporting
 * trading data (trades, positions, performance, equity, orders) to
 * CSV / Excel / JSON formats.
 *
 * This module defines the contracts only.  Concrete implementations
 * (file writer, HTTP stream, etc.) live in separate modules that
 * implement `IDataExportService`.
 */
import type {
  TradeRecord,
  Order,
  PortfolioPosition,
  LivePosition,
  BacktestResult,
  MarketType,
} from "../types.js";
import type { EquitySnapshot } from "./equity-history.js";
import type { AttributionResult } from "./performance-attribution.js";

// ── Export Format ───────────────────────────────────────────────

/** Supported export file formats. */
export type ExportFormat = "csv" | "excel" | "json";

// ── Data Source Identifiers ────────────────────────────────────

/** Identifies which category of data to export. */
export type ExportDataSource =
  | "trades"
  | "orders"
  | "positions"
  | "live_positions"
  | "equity"
  | "performance"
  | "backtest";

// ── Column Definition ──────────────────────────────────────────

/** Describes a single column in the exported table. */
export interface ExportColumnDef {
  /** Machine key — maps to a field on the data row. */
  key: string;
  /** Human-readable header label (supports Chinese). */
  label: string;
  /**
   * Optional value formatter.
   * Receives the raw cell value and should return a display string.
   */
  format?: (value: unknown) => string;
}

// ── Filter / Query Options ─────────────────────────────────────

/** Common time-range + symbol filter shared by most data sources. */
export interface ExportFilter {
  /** ISO date string lower bound (inclusive). */
  from?: string;
  /** ISO date string upper bound (inclusive). */
  to?: string;
  /** Restrict to these symbols. */
  symbols?: string[];
  /** Restrict to a specific market. */
  market?: MarketType;
  /** Restrict to a specific strategy. */
  strategyId?: string;
  /** Maximum number of rows to export. */
  limit?: number;
}

// ── Export Request ──────────────────────────────────────────────

/** Full specification for an export job. */
export interface ExportRequest {
  /** Which data source to pull from. */
  source: ExportDataSource;
  /** Desired output format. */
  format: ExportFormat;
  /** Optional row-level filters. */
  filter?: ExportFilter;
  /** Override the default column set for this source. */
  columns?: ExportColumnDef[];
  /**
   * Target file path (for file-based export).
   * If omitted the service returns the serialized content as a Buffer.
   */
  outputPath?: string;
  /**
   * Human-readable title embedded in the file header / sheet name.
   * Defaults to the data source name.
   */
  title?: string;
}

// ── Export Result ───────────────────────────────────────────────

/** Returned after a successful export. */
export interface ExportResult {
  /** True when the export completed without error. */
  success: boolean;
  /** Number of data rows exported. */
  rowCount: number;
  /** The format that was actually produced. */
  format: ExportFormat;
  /** Absolute path if written to disk; undefined for in-memory exports. */
  filePath?: string;
  /** In-memory content when no `outputPath` was specified. */
  content?: Buffer;
  /** File size in bytes (when written to disk). */
  fileSize?: number;
  /** Export duration in milliseconds. */
  durationMs: number;
}

// ── Source-specific Row Types ──────────────────────────────────

/**
 * Thin wrappers that normalise each data source into a flat row
 * suitable for tabular export.  The export service maps domain
 * objects to these rows before serialisation.
 */

export interface TradeExportRow {
  tradeId: string;
  symbol: string;
  name: string;
  market: MarketType;
  side: string;
  entryTime: string;
  exitTime: string;
  entryPrice: number;
  exitPrice: number;
  quantity: number;
  grossPnl: number;
  commission: number;
  netPnl: number;
  holdingDays: number;
  strategyId: string;
}

export interface OrderExportRow {
  orderId: string;
  symbol: string;
  market: MarketType;
  side: string;
  orderType: string;
  price: number;
  quantity: number;
  filledQty: number;
  avgFillPrice: number;
  status: string;
  strategyId: string;
  brokerType: string;
  createdAt: string;
  filledAt: string;
}

export interface PositionExportRow {
  symbol: string;
  name: string;
  market: MarketType;
  quantity: number;
  avgCost: number;
  currentPrice: number;
  marketValue: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
  weight: number;
}

export interface EquityExportRow {
  date: string;
  equity: number;
  cash: number;
  marketValue: number;
  dailyPnl: number;
  positionCount: number;
  benchmark: number;
}

export interface PerformanceExportRow {
  strategyId: string;
  trades: number;
  wins: number;
  losses: number;
  winRate: number;
  grossPnl: number;
  netPnl: number;
  avgWin: number;
  avgLoss: number;
  profitFactor: number;
  contributionPct: number;
}

export interface LivePositionExportRow {
  symbol: string;
  name: string;
  market: MarketType;
  quantity: number;
  availableQty: number;
  frozenQty: number;
  avgCost: number;
  currentPrice: number;
  marketValue: number;
  unrealizedPnl: number;
  unrealizedPnlPct: number;
}

export interface BacktestExportRow {
  strategyId: string;
  symbols: string;
  startDate: string;
  endDate: string;
  initialCash: number;
  finalValue: number;
  totalReturn: number;
  annualizedReturn: number;
  maxDrawdown: number;
  sharpeRatio: number;
  winRate: number;
  totalTrades: number;
}

// ── Union of all exportable row types ──────────────────────────

export type ExportRow =
  | TradeExportRow
  | OrderExportRow
  | PositionExportRow
  | LivePositionExportRow
  | EquityExportRow
  | PerformanceExportRow
  | BacktestExportRow;

// ── Default Column Definitions per Source ──────────────────────

export const DEFAULT_COLUMNS: Record<ExportDataSource, ExportColumnDef[]> = {
  trades: [
    { key: "tradeId", label: "交易ID" },
    { key: "symbol", label: "代码" },
    { key: "name", label: "名称" },
    { key: "market", label: "市场" },
    { key: "side", label: "方向" },
    { key: "entryTime", label: "买入时间" },
    { key: "exitTime", label: "卖出时间" },
    { key: "entryPrice", label: "买入价" },
    { key: "exitPrice", label: "卖出价" },
    { key: "quantity", label: "数量" },
    { key: "grossPnl", label: "毛利润" },
    { key: "commission", label: "佣金" },
    { key: "netPnl", label: "净利润" },
    { key: "holdingDays", label: "持仓天数" },
    { key: "strategyId", label: "策略" },
  ],
  orders: [
    { key: "orderId", label: "订单ID" },
    { key: "symbol", label: "代码" },
    { key: "market", label: "市场" },
    { key: "side", label: "方向" },
    { key: "orderType", label: "类型" },
    { key: "price", label: "价格" },
    { key: "quantity", label: "数量" },
    { key: "filledQty", label: "成交数量" },
    { key: "avgFillPrice", label: "成交均价" },
    { key: "status", label: "状态" },
    { key: "strategyId", label: "策略" },
    { key: "brokerType", label: "券商" },
    { key: "createdAt", label: "创建时间" },
    { key: "filledAt", label: "成交时间" },
  ],
  positions: [
    { key: "symbol", label: "代码" },
    { key: "name", label: "名称" },
    { key: "market", label: "市场" },
    { key: "quantity", label: "数量" },
    { key: "avgCost", label: "均价" },
    { key: "currentPrice", label: "现价" },
    { key: "marketValue", label: "市值" },
    { key: "unrealizedPnl", label: "浮盈" },
    { key: "unrealizedPnlPct", label: "浮盈%" },
    { key: "weight", label: "权重%" },
  ],
  live_positions: [
    { key: "symbol", label: "代码" },
    { key: "name", label: "名称" },
    { key: "market", label: "市场" },
    { key: "quantity", label: "持仓" },
    { key: "availableQty", label: "可用" },
    { key: "frozenQty", label: "冻结" },
    { key: "avgCost", label: "均价" },
    { key: "currentPrice", label: "现价" },
    { key: "marketValue", label: "市值" },
    { key: "unrealizedPnl", label: "浮盈" },
    { key: "unrealizedPnlPct", label: "浮盈%" },
  ],
  equity: [
    { key: "date", label: "日期" },
    { key: "equity", label: "净值" },
    { key: "cash", label: "现金" },
    { key: "marketValue", label: "市值" },
    { key: "dailyPnl", label: "日盈亏" },
    { key: "positionCount", label: "持仓数" },
    { key: "benchmark", label: "基准" },
  ],
  performance: [
    { key: "strategyId", label: "策略" },
    { key: "trades", label: "交易次数" },
    { key: "wins", label: "盈利次数" },
    { key: "losses", label: "亏损次数" },
    { key: "winRate", label: "胜率" },
    { key: "grossPnl", label: "毛利润" },
    { key: "netPnl", label: "净利润" },
    { key: "avgWin", label: "平均盈利" },
    { key: "avgLoss", label: "平均亏损" },
    { key: "profitFactor", label: "盈亏比" },
    { key: "contributionPct", label: "贡献度%" },
  ],
  backtest: [
    { key: "strategyId", label: "策略" },
    { key: "symbols", label: "标的" },
    { key: "startDate", label: "开始日期" },
    { key: "endDate", label: "结束日期" },
    { key: "initialCash", label: "初始资金" },
    { key: "finalValue", label: "最终市值" },
    { key: "totalReturn", label: "总收益%" },
    { key: "annualizedReturn", label: "年化收益%" },
    { key: "maxDrawdown", label: "最大回撤%" },
    { key: "sharpeRatio", label: "夏普比率" },
    { key: "winRate", label: "胜率" },
    { key: "totalTrades", label: "交易次数" },
  ],
};

// ── Row Mapping Helpers ────────────────────────────────────────

/** Map a domain TradeRecord to a flat export row. */
export function toTradeExportRow(t: TradeRecord): TradeExportRow {
  return {
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
    holdingDays: t.holdingDays ?? 0,
    strategyId: t.strategyId ?? "",
  };
}

/** Map a domain Order to a flat export row. */
export function toOrderExportRow(o: Order): OrderExportRow {
  return {
    orderId: o.orderId,
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
    brokerType: o.brokerType,
    createdAt: o.createdAt,
    filledAt: o.filledAt ?? "",
  };
}

/** Map a PortfolioPosition to a flat export row. */
export function toPositionExportRow(p: PortfolioPosition): PositionExportRow {
  return {
    symbol: p.symbol,
    name: p.name ?? "",
    market: p.market,
    quantity: p.quantity,
    avgCost: p.avgCost,
    currentPrice: p.currentPrice ?? 0,
    marketValue: p.marketValue ?? 0,
    unrealizedPnl: p.unrealizedPnl ?? 0,
    unrealizedPnlPct: p.unrealizedPnlPct ?? 0,
    weight: p.weight ?? 0,
  };
}

/** Map an EquitySnapshot to a flat export row. */
export function toEquityExportRow(e: EquitySnapshot): EquityExportRow {
  return {
    date: e.date,
    equity: e.equity,
    cash: e.cash,
    marketValue: e.marketValue,
    dailyPnl: e.dailyPnl,
    positionCount: e.positionCount,
    benchmark: e.benchmark ?? 0,
  };
}

/** Map a StrategyAttribution (from performance report) to a flat export row. */
export function toPerformanceExportRow(
  s: import("./performance-attribution.js").StrategyAttribution,
): PerformanceExportRow {
  return {
    strategyId: s.strategyId,
    trades: s.trades,
    wins: s.wins,
    losses: s.losses,
    winRate: s.winRate,
    grossPnl: s.grossPnl,
    netPnl: s.netPnl,
    avgWin: s.avgWin,
    avgLoss: s.avgLoss,
    profitFactor: s.profitFactor,
    contributionPct: s.contributionPct,
  };
}

/** Map a LivePosition to a flat export row. */
export function toLivePositionExportRow(p: LivePosition): LivePositionExportRow {
  return {
    symbol: p.symbol,
    name: p.name ?? "",
    market: p.market,
    quantity: p.quantity,
    availableQty: p.availableQty,
    frozenQty: p.frozenQty,
    avgCost: p.avgCost,
    currentPrice: p.currentPrice ?? 0,
    marketValue: p.marketValue ?? 0,
    unrealizedPnl: p.unrealizedPnl ?? 0,
    unrealizedPnlPct: p.unrealizedPnlPct ?? 0,
  };
}

/** Map a BacktestResult to a flat export row. */
export function toBacktestExportRow(b: BacktestResult): BacktestExportRow {
  return {
    strategyId: b.strategyId,
    symbols: b.symbols.join(", "),
    startDate: b.startDate,
    endDate: b.endDate,
    initialCash: b.initialCash,
    finalValue: b.finalValue,
    totalReturn: b.totalReturn,
    annualizedReturn: b.annualizedReturn,
    maxDrawdown: b.maxDrawdown,
    sharpeRatio: b.sharpeRatio,
    winRate: b.winRate,
    totalTrades: b.totalTrades,
  };
}

// ── Service Interface ──────────────────────────────────────────

/**
 * Unified data export service interface.
 *
 * Implementations handle the mechanics of serialising rows to the
 * requested format and writing to disk or returning a buffer.
 *
 * Usage:
 * ```ts
 * const result = await exporter.export({
 *   source: "trades",
 *   format: "csv",
 *   filter: { from: "2025-01-01", symbols: ["600519"] },
 *   outputPath: "/tmp/trades.csv",
 * });
 * ```
 */
export interface IDataExportService {
  /**
   * Execute a full export job.
   *
   * Fetches the data identified by `request.source`, applies
   * `request.filter`, maps to flat rows, and serialises to the
   * requested `request.format`.
   */
  export(request: ExportRequest): Promise<ExportResult>;

  /**
   * Return the list of formats the implementation supports.
   * (Not all implementations need to support Excel, for instance.)
   */
  supportedFormats(): ExportFormat[];

  /**
   * Return the default column definitions for a data source.
   * Callers can override these via `ExportRequest.columns`.
   */
  getDefaultColumns(source: ExportDataSource): ExportColumnDef[];
}
