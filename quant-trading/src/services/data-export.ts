/**
 * Data Export Service — export trading data in multiple formats.
 *
 * Supports exporting:
 *   - K-line / OHLCV data
 *   - Trading signals
 *   - Order history
 *   - Trade records (closed trades)
 *   - Equity curve / snapshots
 *
 * Output formats:
 *   - CSV  (RFC 4180 compliant)
 *   - JSON (pretty or compact)
 *   - TSV  (tab-separated for Excel paste)
 */

import type {
  QuantStorage,
  KlineRow,
  SignalRow,
  OrderRow,
  TradeRow,
  EquityRow,
} from "./sqlite-storage.js";

// ── Types ────────────────────────────────────────────────────

export type ExportFormat = "csv" | "json" | "tsv";

export type ExportDataType = "klines" | "signals" | "orders" | "trades" | "equity";

export interface ExportOptions {
  /** Output format (default: "csv") */
  format?: ExportFormat;
  /** Whether to include a header row for CSV/TSV (default: true) */
  includeHeader?: boolean;
  /** Pretty-print JSON with indentation (default: true) */
  prettyJson?: boolean;
  /** CSV field delimiter override (default: "," for csv, "\t" for tsv) */
  delimiter?: string;
  /** Maximum number of rows to export (default: unlimited) */
  maxRows?: number;
  /** Date format: "iso" for full ISO timestamp, "date" for YYYY-MM-DD only (default: "iso") */
  dateFormat?: "iso" | "date";
}

export interface KlineExportFilter {
  symbol: string;
  interval?: string;
  startDate?: string;
  endDate?: string;
}

export interface SignalExportFilter {
  symbol?: string;
  strategyId?: string;
  limit?: number;
}

export interface OrderExportFilter {
  symbol?: string;
  status?: string[];
  limit?: number;
}

export interface TradeExportFilter {
  symbol?: string;
  limit?: number;
}

export interface EquityExportFilter {
  startDate?: string;
  endDate?: string;
  limit?: number;
}

export interface ExportResult {
  /** The exported data as a string */
  content: string;
  /** Number of data rows exported (excluding header) */
  rowCount: number;
  /** The format used */
  format: ExportFormat;
  /** The data type exported */
  dataType: ExportDataType;
  /** Suggested file extension */
  extension: string;
  /** MIME type for HTTP responses */
  mimeType: string;
}

// ── CSV / TSV helpers ────────────────────────────────────────

/** Escape a value for CSV (RFC 4180): quote if it contains comma, quote, or newline */
function csvEscape(val: unknown, delimiter: string): string {
  if (val === null || val === undefined) return "";
  const s = String(val);
  if (s.includes(delimiter) || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function formatDelimited(
  headers: string[],
  rows: unknown[][],
  delimiter: string,
  includeHeader: boolean,
): string {
  const lines: string[] = [];
  if (includeHeader) {
    lines.push(headers.map((h) => csvEscape(h, delimiter)).join(delimiter));
  }
  for (const row of rows) {
    lines.push(row.map((v) => csvEscape(v, delimiter)).join(delimiter));
  }
  return lines.join("\n");
}

// ── Column definitions ───────────────────────────────────────

const KLINE_COLUMNS = ["symbol", "date", "interval", "open", "high", "low", "close", "volume", "amount"] as const;
const SIGNAL_COLUMNS = ["id", "timestamp", "date", "symbol", "direction", "price", "quantity", "reason", "confidence", "strategyId"] as const;
const ORDER_COLUMNS = ["orderId", "symbol", "market", "side", "orderType", "quantity", "price", "status", "strategyId", "filledQty", "filledPrice", "createdAt", "updatedAt"] as const;
const TRADE_COLUMNS = ["id", "symbol", "side", "entryPrice", "exitPrice", "quantity", "entryTime", "exitTime", "netPnl", "pnlPct", "commission", "strategyId"] as const;
const EQUITY_COLUMNS = ["date", "equity", "cash", "marketValue", "dailyPnl", "positionCount", "benchmark"] as const;

// ── Row extraction helpers ───────────────────────────────────

function klineToRow(r: KlineRow): unknown[] {
  return [r.symbol, r.date, r.interval, r.open, r.high, r.low, r.close, r.volume, r.amount ?? ""];
}

function signalToRow(r: SignalRow): unknown[] {
  return [r.id ?? "", r.timestamp, r.date ?? "", r.symbol, r.direction, r.price, r.quantity ?? "", r.reason ?? "", r.confidence ?? "", r.strategyId ?? ""];
}

function orderToRow(r: OrderRow): unknown[] {
  return [r.orderId, r.symbol, r.market ?? "", r.side, r.orderType, r.quantity, r.price ?? "", r.status, r.strategyId ?? "", r.filledQty ?? "", r.filledPrice ?? "", r.createdAt ?? "", r.updatedAt ?? ""];
}

function tradeToRow(r: TradeRow): unknown[] {
  return [r.id ?? "", r.symbol, r.side, r.entryPrice, r.exitPrice, r.quantity, r.entryTime ?? "", r.exitTime ?? "", r.netPnl, r.pnlPct ?? "", r.commission ?? "", r.strategyId ?? ""];
}

function equityToRow(r: EquityRow): unknown[] {
  return [r.date, r.equity, r.cash, r.marketValue, r.dailyPnl, r.positionCount, r.benchmark ?? ""];
}

// ── Format metadata ──────────────────────────────────────────

function getFormatMeta(format: ExportFormat): { extension: string; mimeType: string } {
  switch (format) {
    case "csv":
      return { extension: ".csv", mimeType: "text/csv; charset=utf-8" };
    case "tsv":
      return { extension: ".tsv", mimeType: "text/tab-separated-values; charset=utf-8" };
    case "json":
      return { extension: ".json", mimeType: "application/json; charset=utf-8" };
  }
}

// ── DataExportService ────────────────────────────────────────

export class DataExportService {
  private storage: QuantStorage;

  constructor(storage: QuantStorage) {
    this.storage = storage;
  }

  /**
   * Export K-line (OHLCV) data.
   */
  async exportKlines(filter: KlineExportFilter, opts: ExportOptions = {}): Promise<ExportResult> {
    const { format = "csv", includeHeader = true, prettyJson = true, maxRows } = opts;
    const delimiter = opts.delimiter ?? (format === "tsv" ? "\t" : ",");
    const interval = filter.interval ?? "1d";
    const startDate = filter.startDate ?? "1970-01-01";
    const endDate = filter.endDate ?? "2099-12-31";

    let rows = await this.storage.getKlines(filter.symbol, interval, startDate, endDate);
    if (maxRows !== undefined && rows.length > maxRows) {
      rows = rows.slice(0, maxRows);
    }

    const content = this.formatData(
      [...KLINE_COLUMNS],
      rows.map(klineToRow),
      format,
      delimiter,
      includeHeader,
      prettyJson,
    );

    const meta = getFormatMeta(format);
    return {
      content,
      rowCount: rows.length,
      format,
      dataType: "klines",
      ...meta,
    };
  }

  /**
   * Export trading signals.
   */
  async exportSignals(filter: SignalExportFilter = {}, opts: ExportOptions = {}): Promise<ExportResult> {
    const { format = "csv", includeHeader = true, prettyJson = true, maxRows } = opts;
    const delimiter = opts.delimiter ?? (format === "tsv" ? "\t" : ",");

    let rows = await this.storage.getSignals({
      symbol: filter.symbol,
      strategyId: filter.strategyId,
      limit: filter.limit,
    });
    if (maxRows !== undefined && rows.length > maxRows) {
      rows = rows.slice(0, maxRows);
    }

    const content = this.formatData(
      [...SIGNAL_COLUMNS],
      rows.map(signalToRow),
      format,
      delimiter,
      includeHeader,
      prettyJson,
    );

    const meta = getFormatMeta(format);
    return {
      content,
      rowCount: rows.length,
      format,
      dataType: "signals",
      ...meta,
    };
  }

  /**
   * Export order history.
   */
  async exportOrders(filter: OrderExportFilter = {}, opts: ExportOptions = {}): Promise<ExportResult> {
    const { format = "csv", includeHeader = true, prettyJson = true, maxRows } = opts;
    const delimiter = opts.delimiter ?? (format === "tsv" ? "\t" : ",");

    let rows = await this.storage.getOrders({
      symbol: filter.symbol,
      status: filter.status,
      limit: filter.limit,
    });
    if (maxRows !== undefined && rows.length > maxRows) {
      rows = rows.slice(0, maxRows);
    }

    const content = this.formatData(
      [...ORDER_COLUMNS],
      rows.map(orderToRow),
      format,
      delimiter,
      includeHeader,
      prettyJson,
    );

    const meta = getFormatMeta(format);
    return {
      content,
      rowCount: rows.length,
      format,
      dataType: "orders",
      ...meta,
    };
  }

  /**
   * Export closed trade records.
   */
  async exportTrades(filter: TradeExportFilter = {}, opts: ExportOptions = {}): Promise<ExportResult> {
    const { format = "csv", includeHeader = true, prettyJson = true, maxRows } = opts;
    const delimiter = opts.delimiter ?? (format === "tsv" ? "\t" : ",");

    let rows = await this.storage.getTrades({
      symbol: filter.symbol,
      limit: filter.limit,
    });
    if (maxRows !== undefined && rows.length > maxRows) {
      rows = rows.slice(0, maxRows);
    }

    const content = this.formatData(
      [...TRADE_COLUMNS],
      rows.map(tradeToRow),
      format,
      delimiter,
      includeHeader,
      prettyJson,
    );

    const meta = getFormatMeta(format);
    return {
      content,
      rowCount: rows.length,
      format,
      dataType: "trades",
      ...meta,
    };
  }

  /**
   * Export equity curve / snapshots.
   */
  async exportEquity(filter: EquityExportFilter = {}, opts: ExportOptions = {}): Promise<ExportResult> {
    const { format = "csv", includeHeader = true, prettyJson = true, maxRows } = opts;
    const delimiter = opts.delimiter ?? (format === "tsv" ? "\t" : ",");

    let rows = await this.storage.getEquitySnapshots({
      start: filter.startDate,
      end: filter.endDate,
      limit: filter.limit,
    });
    if (maxRows !== undefined && rows.length > maxRows) {
      rows = rows.slice(0, maxRows);
    }

    const content = this.formatData(
      [...EQUITY_COLUMNS],
      rows.map(equityToRow),
      format,
      delimiter,
      includeHeader,
      prettyJson,
    );

    const meta = getFormatMeta(format);
    return {
      content,
      rowCount: rows.length,
      format,
      dataType: "equity",
      ...meta,
    };
  }

  // ── Internal formatting ───────────────────────────────────

  private formatData(
    headers: string[],
    rows: unknown[][],
    format: ExportFormat,
    delimiter: string,
    includeHeader: boolean,
    prettyJson: boolean,
  ): string {
    if (format === "json") {
      const objects = rows.map((row) => {
        const obj: Record<string, unknown> = {};
        for (let i = 0; i < headers.length; i++) {
          const val = row[i];
          if (val !== "" && val !== undefined && val !== null) {
            obj[headers[i]!] = val;
          }
        }
        return obj;
      });
      return prettyJson ? JSON.stringify(objects, null, 2) : JSON.stringify(objects);
    }

    return formatDelimited(headers, rows, delimiter, includeHeader);
  }
}
