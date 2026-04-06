/**
 * DataExportService — export positions, orders, trades, signals, journal,
 * klines, backtest results and screen results to CSV or JSON.
 *
 * Supports UTF-8 BOM for proper Chinese character display in Excel.
 * Supports JSON output for programmatic consumption and external tools.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";

// ── Types ────────────────────────────────────────────────────

export type ExportTarget =
  | { type: "positions"; accountId?: string }
  | { type: "orders"; accountId?: string; dateFrom?: string; dateTo?: string }
  | { type: "trades"; accountId?: string; dateFrom?: string; dateTo?: string }
  | { type: "signals"; symbol?: string; strategyId?: string; dateFrom?: string; dateTo?: string; limit?: number }
  | { type: "journal"; symbol?: string; dateFrom?: string; dateTo?: string; limit?: number }
  | { type: "klines"; symbol: string; interval?: string; dateFrom?: string; dateTo?: string; limit?: number }
  | { type: "backtest"; backtestId: string }
  | { type: "screen"; screenId: string }
  | { type: "equity"; accountId?: string; dateFrom?: string; dateTo?: string }
  | { type: "watchlist"; category?: string }
  | { type: "alerts"; severity?: string; source?: string; dateFrom?: string; dateTo?: string; limit?: number }
  | { type: "hitrate"; strategyId?: string; limit?: number }
  | { type: "performance"; accountId?: string; dateFrom?: string; dateTo?: string };

export type ExportFormat = "csv" | "json";

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

export interface ExportFileInfo {
  filename: string;
  size: number;
  sizeLabel: string;
  createdAt: string;
}

// ── Data provider interfaces (duck-typed) ───────────────────

export interface DataSource {
  getPositions(accountId?: string): Promise<ExportableRow[]>;
  getOrders(accountId?: string, dateFrom?: string, dateTo?: string): Promise<ExportableRow[]>;
  getTrades(accountId?: string, dateFrom?: string, dateTo?: string): Promise<ExportableRow[]>;
  getSignals(opts?: { symbol?: string; strategyId?: string; limit?: number }): Promise<ExportableRow[]>;
  getJournalNotes(opts?: { symbol?: string; dateFrom?: string; dateTo?: string; limit?: number }): Promise<ExportableRow[]>;
  getKlines(symbol: string, interval: string, dateFrom?: string, dateTo?: string): Promise<ExportableRow[]>;
  getBacktestResult(backtestId: string): Promise<ExportableRow[]>;
  getScreenResult(screenId: string): Promise<ExportableRow[]>;
  getEquityHistory(accountId?: string, dateFrom?: string, dateTo?: string): Promise<ExportableRow[]>;
  getWatchlist(category?: string): Promise<ExportableRow[]>;
  getAlerts(opts?: { severity?: string; source?: string; dateFrom?: string; dateTo?: string; limit?: number }): Promise<ExportableRow[]>;
  getHitRateRecords(opts?: { strategyId?: string; limit?: number }): Promise<ExportableRow[]>;
  getPerformanceSummary(accountId?: string, dateFrom?: string, dateTo?: string): Promise<ExportableRow[]>;
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
export const SIGNAL_COLUMNS = ["date", "timestamp", "symbol", "direction", "price", "quantity", "reason", "confidence", "strategyId"];
export const JOURNAL_COLUMNS = ["id", "date", "symbol", "direction", "entryPrice", "exitPrice", "pnl", "rating", "tags", "content", "lesson", "strategyId", "createdAt"];
export const KLINE_COLUMNS = ["symbol", "date", "interval", "open", "high", "low", "close", "volume", "amount"];
export const EQUITY_COLUMNS = ["date", "equity", "cash", "marketValue", "dailyPnl", "positionCount", "benchmark"];
export const WATCHLIST_COLUMNS = ["code", "name", "category", "priority", "priceTarget", "convictionScore", "tags", "notes", "addedAt", "updatedAt"];
export const ALERT_COLUMNS = ["id", "timestamp", "severity", "source", "message", "acknowledged"];
export const HITRATE_COLUMNS = ["signalTimestamp", "symbol", "direction", "signalPrice", "strategyId", "reason", "confidence", "T+1_result", "T+1_price", "T+3_result", "T+3_price", "T+5_result", "T+5_price", "T+10_result", "T+10_price"];
export const PERFORMANCE_COLUMNS = ["metric", "value"];

// ── Service ─────────────────────────────────────────────────

export class DataExportService {
  private dataSource: DataSource;
  private exportDir: string;

  constructor(opts: { dataSource: DataSource; exportDir?: string }) {
    this.dataSource = opts.dataSource;
    this.exportDir = opts.exportDir ?? path.join(os.homedir(), ".openclaw", "quant-trading", "exports");
  }

  async export(target: ExportTarget, format: ExportFormat = "csv"): Promise<ExportResult> {
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
      case "signals":
        rows = await this.dataSource.getSignals({ symbol: target.symbol, strategyId: target.strategyId, limit: target.limit });
        columns = SIGNAL_COLUMNS;
        prefix = "signals";
        break;
      case "journal":
        rows = await this.dataSource.getJournalNotes({ symbol: target.symbol, dateFrom: target.dateFrom, dateTo: target.dateTo, limit: target.limit });
        columns = JOURNAL_COLUMNS;
        prefix = "journal";
        break;
      case "klines":
        rows = await this.dataSource.getKlines(target.symbol, target.interval ?? "1d", target.dateFrom, target.dateTo);
        columns = KLINE_COLUMNS;
        prefix = `klines_${target.symbol}`;
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
      case "watchlist":
        rows = await this.dataSource.getWatchlist(target.category);
        columns = WATCHLIST_COLUMNS;
        prefix = "watchlist";
        break;
      case "alerts":
        rows = await this.dataSource.getAlerts({ severity: target.severity, source: target.source, dateFrom: target.dateFrom, dateTo: target.dateTo, limit: target.limit });
        columns = ALERT_COLUMNS;
        prefix = "alerts";
        break;
      case "hitrate":
        rows = await this.dataSource.getHitRateRecords({ strategyId: target.strategyId, limit: target.limit });
        columns = HITRATE_COLUMNS;
        prefix = "hitrate";
        break;
      case "performance":
        rows = await this.dataSource.getPerformanceSummary(target.accountId, target.dateFrom, target.dateTo);
        columns = PERFORMANCE_COLUMNS;
        prefix = "performance";
        break;
    }

    const date = new Date().toISOString().slice(0, 10);
    const ts = Date.now();
    const ext = format === "json" ? "json" : "csv";
    const filename = `${prefix}-${date}-${ts}.${ext}`;

    await fs.mkdir(this.exportDir, { recursive: true });
    const filePath = path.join(this.exportDir, filename);

    if (format === "json") {
      const jsonContent = JSON.stringify(rows, null, 2);
      await fs.writeFile(filePath, jsonContent, "utf-8");
    } else {
      const csvContent = toCsv(rows, columns);
      await fs.writeFile(filePath, csvContent, "utf-8");
    }

    const stats = await fs.stat(filePath);

    return {
      filename,
      format,
      path: filePath,
      rowCount: rows.length,
      size: stats.size,
    };
  }

  async exportBatch(targets: ExportTarget[], format: ExportFormat = "csv"): Promise<ExportResult[]> {
    const results: ExportResult[] = [];
    for (const target of targets) {
      results.push(await this.export(target, format));
    }
    return results;
  }

  async listExports(): Promise<ExportFileInfo[]> {
    try {
      const files = await fs.readdir(this.exportDir);
      const exportFiles = files.filter((f) => f.endsWith(".csv") || f.endsWith(".json"));
      const details = await Promise.all(
        exportFiles.map(async (f) => {
          const stat = await fs.stat(path.join(this.exportDir, f));
          return {
            filename: f,
            size: stat.size,
            sizeLabel: stat.size < 1024 ? `${stat.size}B` : `${(stat.size / 1024).toFixed(1)}KB`,
            createdAt: stat.mtime.toISOString(),
          };
        }),
      );
      details.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
      return details;
    } catch {
      return [];
    }
  }

  async deleteExport(filename: string): Promise<boolean> {
    const safe = path.basename(filename);
    const filePath = path.join(this.exportDir, safe);
    try {
      await fs.unlink(filePath);
      return true;
    } catch {
      return false;
    }
  }

  async deleteAllExports(): Promise<number> {
    try {
      const files = await fs.readdir(this.exportDir);
      const exportFiles = files.filter((f) => f.endsWith(".csv") || f.endsWith(".json"));
      await Promise.all(exportFiles.map((f) => fs.unlink(path.join(this.exportDir, f))));
      return exportFiles.length;
    } catch {
      return 0;
    }
  }
}
