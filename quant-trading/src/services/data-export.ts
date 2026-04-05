/**
 * DataExportService — export positions, orders, trades, backtest results to CSV/Excel.
 *
 * Supports UTF-8 BOM for proper Chinese character display in Excel.
 * Generates CSV files; Excel (xlsx) support is a future enhancement.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

// ── Types ────────────────────────────────────────────────────

export type ExportTarget =
  | { type: "positions"; accountId?: string }
  | { type: "orders"; accountId?: string; dateFrom?: string; dateTo?: string }
  | { type: "trades"; accountId?: string; dateFrom?: string; dateTo?: string }
  | { type: "backtest"; backtestId: string }
  | { type: "screen"; screenId: string }
  | { type: "equity"; accountId?: string; dateFrom?: string; dateTo?: string };

export type ExportFormat = "csv" | "xlsx";

export interface ExportResult {
  filename: string;
  format: ExportFormat;
  path: string;
  rowCount: number;
  size: number;
}

export interface ExportableRow {
  [key: string]: string | number | boolean | null | undefined;
}

// ── Data provider interfaces (duck-typed) ───────────────────

export interface DataSource {
  getPositions(accountId?: string): Promise<ExportableRow[]>;
  getOrders(accountId?: string, dateFrom?: string, dateTo?: string): Promise<ExportableRow[]>;
  getTrades(accountId?: string, dateFrom?: string, dateTo?: string): Promise<ExportableRow[]>;
  getBacktestResult(backtestId: string): Promise<ExportableRow[]>;
  getScreenResult(screenId: string): Promise<ExportableRow[]>;
  getEquityHistory(accountId?: string, dateFrom?: string, dateTo?: string): Promise<ExportableRow[]>;
}

// ── CSV helpers ─────────────────────────────────────────────

const BOM = "\uFEFF";

/** Escape a CSV field value (handles commas, quotes, newlines). */
function escapeCsvField(value: unknown): string {
  if (value === null || value === undefined) return "";
  const str = String(value);
  if (str.includes(",") || str.includes('"') || str.includes("\n") || str.includes("\r")) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

/** Convert rows to CSV string with BOM for UTF-8 Excel compatibility. */
export function toCsv(rows: ExportableRow[], columns?: string[]): string {
  if (rows.length === 0) return BOM;
  const cols = columns ?? Object.keys(rows[0]);
  const header = cols.map(escapeCsvField).join(",");
  const lines = rows.map((row) => cols.map((col) => escapeCsvField(row[col])).join(","));
  return BOM + header + "\n" + lines.join("\n") + "\n";
}

// ── Column definitions ──────────────────────────────────────

export const POSITION_COLUMNS = ["symbol", "name", "market", "quantity", "avgCost", "currentPrice", "marketValue", "unrealizedPnl", "unrealizedPnlPct"];
export const ORDER_COLUMNS = ["orderId", "symbol", "side", "orderType", "price", "quantity", "filledQty", "avgFillPrice", "status", "createdAt", "filledAt"];
export const TRADE_COLUMNS = ["tradeId", "symbol", "name", "side", "entryTime", "exitTime", "entryPrice", "exitPrice", "quantity", "grossPnl", "commission", "netPnl", "holdingDays", "strategyId"];
export const EQUITY_COLUMNS = ["date", "equity", "cash", "marketValue", "dailyPnl", "positionCount"];

// ── Service ─────────────────────────────────────────────────

export class DataExportService {
  private dataSource: DataSource;
  private exportDir: string;

  constructor(opts: { dataSource: DataSource; exportDir?: string }) {
    this.dataSource = opts.dataSource;
    this.exportDir = opts.exportDir ?? path.join(os.homedir(), ".openclaw", "quant-trading", "exports");
  }

  async export(target: ExportTarget, format: ExportFormat = "csv"): Promise<ExportResult> {
    if (format === "xlsx") {
      throw new Error("Excel format not yet supported. Use 'csv' format.");
    }

    let rows: ExportableRow[];
    let columns: string[] | undefined;
    let prefix: string;

    switch (target.type) {
      case "positions":
        rows = await this.dataSource.getPositions(target.accountId);
        columns = POSITION_COLUMNS;
        prefix = "positions";
        break;
      case "orders":
        rows = await this.dataSource.getOrders(target.accountId, target.dateFrom, target.dateTo);
        columns = ORDER_COLUMNS;
        prefix = "orders";
        break;
      case "trades":
        rows = await this.dataSource.getTrades(target.accountId, target.dateFrom, target.dateTo);
        columns = TRADE_COLUMNS;
        prefix = "trades";
        break;
      case "backtest":
        rows = await this.dataSource.getBacktestResult(target.backtestId);
        prefix = "backtest";
        break;
      case "screen":
        rows = await this.dataSource.getScreenResult(target.screenId);
        prefix = "screen";
        break;
      case "equity":
        rows = await this.dataSource.getEquityHistory(target.accountId, target.dateFrom, target.dateTo);
        columns = EQUITY_COLUMNS;
        prefix = "equity";
        break;
    }

    const date = new Date().toISOString().slice(0, 10);
    const filename = `${prefix}-${date}.csv`;
    const csvContent = toCsv(rows, columns);

    await fs.mkdir(this.exportDir, { recursive: true });
    const filePath = path.join(this.exportDir, filename);
    await fs.writeFile(filePath, csvContent, "utf-8");

    const stats = await fs.stat(filePath);

    return {
      filename,
      format: "csv",
      path: filePath,
      rowCount: rows.length,
      size: stats.size,
    };
  }
}
