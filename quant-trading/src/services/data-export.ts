/**
 * DataExportService — exports trading data to CSV or XLSX files.
 *
 * Supports export targets: positions, orders, trades, backtest, screen, equity.
 * Files are stored in an `exports/` directory under dataDir and auto-cleaned after 24h.
 */
import fs from "node:fs/promises";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { BacktestStore } from "./backtest-store.js";
import type { EquityHistory } from "./equity-history.js";
import type { ScanHistoryStore } from "./scan-history-store.js";
import type { QuantTradingConfig } from "../types.js";

export type ExportTarget =
  | "positions"
  | "orders"
  | "trades"
  | "backtest"
  | "screen"
  | "equity";

export type ExportFormat = "csv" | "xlsx";

export interface ExportResult {
  ok: boolean;
  filename?: string;
  filePath?: string;
  rowCount?: number;
  error?: string;
}

export interface DataExportServiceDeps {
  api: OpenClawPluginApi;
  config: QuantTradingConfig;
  getEngine: () => LiveEngine;
  backtestStore: BacktestStore;
  scanHistoryStore: ScanHistoryStore;
  equityHistory: EquityHistory;
  equityHistoryPaper?: EquityHistory;
  equityHistoryLive?: EquityHistory;
}

export class DataExportService {
  private deps: DataExportServiceDeps;
  private exportDir: string;
  private cleanupTimer: ReturnType<typeof setInterval> | null = null;

  constructor(deps: DataExportServiceDeps) {
    this.deps = deps;
    const dataDir = deps.config.dataDir ?? ".";
    this.exportDir = path.join(dataDir, "exports");
  }

  /** Start 1-hour cleanup interval for expired export files. */
  start(): void {
    this.cleanupTimer = setInterval(() => void this.cleanup(), 60 * 60 * 1000);
  }

  stop(): void {
    if (this.cleanupTimer) {
      clearInterval(this.cleanupTimer);
      this.cleanupTimer = null;
    }
  }

  /** Export data to file. Returns filename and path for download. */
  async export(
    target: ExportTarget,
    format: ExportFormat,
    options?: Record<string, unknown>,
  ): Promise<ExportResult> {
    try {
      const rows = await this.fetchData(target, options);
      if (!rows || rows.length === 0) {
        return { ok: false, error: `${target} 无数据可导出` };
      }

      await fs.mkdir(this.exportDir, { recursive: true });
      const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
      const filename = `${target}_${timestamp}.${format}`;
      const filePath = path.join(this.exportDir, filename);

      if (format === "csv") {
        await this.writeCsv(filePath, rows);
      } else {
        await this.writeXlsx(filePath, rows, target);
      }

      return { ok: true, filename, filePath, rowCount: rows.length };
    } catch (err) {
      return { ok: false, error: (err as Error).message };
    }
  }

  /** Get the exports directory path for serving files. */
  getExportDir(): string {
    return this.exportDir;
  }

  /** Remove export files older than 24 hours. */
  async cleanup(): Promise<number> {
    let removed = 0;
    try {
      const files = await fs.readdir(this.exportDir);
      const cutoff = Date.now() - 24 * 60 * 60 * 1000;
      for (const file of files) {
        const fp = path.join(this.exportDir, file);
        try {
          const stat = await fs.stat(fp);
          if (stat.mtimeMs < cutoff) {
            await fs.unlink(fp);
            removed++;
          }
        } catch {
          // file may have been removed already
        }
      }
    } catch {
      // exports dir may not exist yet
    }
    if (removed > 0) {
      this.deps.api.logger.info(`[data-export] cleaned up ${removed} expired export files`);
    }
    return removed;
  }

  // ── Data fetching ──────────────────────────────────────────────

  private async fetchData(
    target: ExportTarget,
    options?: Record<string, unknown>,
  ): Promise<Record<string, unknown>[]> {
    switch (target) {
      case "positions":
        return this.fetchPositions(options);
      case "orders":
        return this.fetchOrders(options);
      case "trades":
        return this.fetchTrades(options);
      case "backtest":
        return this.fetchBacktest(options);
      case "screen":
        return this.fetchScreen(options);
      case "equity":
        return this.fetchEquity(options);
      default:
        throw new Error(`不支持的导出目标: ${target}`);
    }
  }

  private async fetchPositions(
    _options?: Record<string, unknown>,
  ): Promise<Record<string, unknown>[]> {
    const engine = this.deps.getEngine();
    const positions = engine.getPositions();
    if (positions.length > 0) {
      return positions.map((p: Record<string, unknown>) => ({
        symbol: p.symbol,
        name: p.name ?? "",
        market: p.market,
        quantity: p.quantity,
        availableQty: p.availableQty,
        avgCost: p.avgCost,
        currentPrice: p.currentPrice ?? "",
        marketValue: p.marketValue ?? "",
        unrealizedPnl: p.unrealizedPnl ?? "",
        unrealizedPnlPct: p.unrealizedPnlPct ?? "",
        source: p.source,
        lastSyncAt: p.lastSyncAt,
      }));
    }
    // Fallback: read from paper portfolio file
    const { config } = this.deps;
    const dataDir = config.dataDir;
    if (!dataDir) return [];
    try {
      const raw = await fs.readFile(path.join(dataDir, "portfolio.json"), "utf-8");
      const portfolio = JSON.parse(raw) as { positions?: Record<string, unknown>[] };
      return (portfolio.positions ?? []).map((p) => ({
        symbol: p.symbol,
        name: p.name ?? "",
        market: p.market,
        quantity: p.quantity,
        avgCost: p.avgCost,
        currentPrice: p.currentPrice ?? "",
        marketValue: p.marketValue ?? "",
        unrealizedPnl: p.unrealizedPnl ?? "",
        unrealizedPnlPct: p.unrealizedPnlPct ?? "",
      }));
    } catch {
      return [];
    }
  }

  private async fetchOrders(
    options?: Record<string, unknown>,
  ): Promise<Record<string, unknown>[]> {
    const engine = this.deps.getEngine();
    if (engine.orderManager) {
      const orders = await engine.orderManager.getOrders();
      return orders.map((o: Record<string, unknown>) => ({
        orderId: o.orderId,
        symbol: o.symbol,
        market: o.market,
        side: o.side,
        orderType: o.orderType,
        price: o.price ?? "",
        quantity: o.quantity,
        filledQty: o.filledQty,
        avgFillPrice: o.avgFillPrice ?? "",
        status: o.status,
        strategyId: o.strategyId ?? "",
        brokerType: o.brokerType,
        createdAt: o.createdAt,
        updatedAt: o.updatedAt,
        errorMsg: o.errorMsg ?? "",
      }));
    }
    // Fallback: read from file
    const mode = (options?.mode as string) ?? "paper";
    return this.readJsonArray(`orders-${mode}.json`);
  }

  private async fetchTrades(
    options?: Record<string, unknown>,
  ): Promise<Record<string, unknown>[]> {
    const engine = this.deps.getEngine();
    if (engine.orderManager) {
      const trades = await engine.orderManager.getTrades();
      return trades.map((t: Record<string, unknown>) => ({
        tradeId: t.tradeId,
        symbol: t.symbol,
        name: t.name ?? "",
        market: t.market,
        side: t.side,
        entryTime: t.entryTime,
        exitTime: t.exitTime ?? "",
        entryPrice: t.entryPrice,
        exitPrice: t.exitPrice ?? "",
        quantity: t.quantity,
        grossPnl: t.grossPnl,
        commission: t.commission,
        netPnl: t.netPnl,
        holdingDays: t.holdingDays ?? "",
        strategyId: t.strategyId ?? "",
      }));
    }
    const mode = (options?.mode as string) ?? "paper";
    return this.readJsonArray(`trades-${mode}.json`);
  }

  private async fetchBacktest(
    options?: Record<string, unknown>,
  ): Promise<Record<string, unknown>[]> {
    const id = options?.id as string | undefined;
    if (id) {
      // Export a specific backtest's signals/equity
      const record = this.deps.backtestStore.getFullResult(id);
      if (!record) return [];
      const result = record.result;
      // Export equity curve as rows
      if (options?.content === "equity" && result.equityCurve) {
        return result.equityCurve.map((p: { date: string; value: number }) => ({
          date: p.date,
          value: p.value,
        }));
      }
      // Export signals
      if (result.signals) {
        return result.signals.map((s: Record<string, unknown>) => ({
          timestamp: s.timestamp,
          symbol: s.symbol,
          direction: s.direction,
          price: s.price,
          quantity: s.quantity ?? "",
          reason: s.reason ?? "",
          confidence: s.confidence ?? "",
          strategyId: s.strategyId,
        }));
      }
      return [];
    }
    // Export backtest summary list
    const entries = this.deps.backtestStore.query({});
    return entries.map((e: Record<string, unknown>) => ({
      id: e.id,
      strategyId: e.strategyId,
      symbols: Array.isArray(e.symbols) ? (e.symbols as string[]).join(",") : "",
      startDate: e.startDate,
      endDate: e.endDate,
      totalReturn: e.totalReturn,
      annualizedReturn: e.annualizedReturn,
      maxDrawdown: e.maxDrawdown,
      sharpeRatio: e.sharpeRatio,
      winRate: e.winRate,
      totalTrades: e.totalTrades,
      engine: e.engine,
      createdAt: e.createdAt,
    }));
  }

  private async fetchScreen(
    options?: Record<string, unknown>,
  ): Promise<Record<string, unknown>[]> {
    const id = options?.id as string | undefined;
    if (id) {
      const record = this.deps.scanHistoryStore.getFullResult(id);
      if (!record) return [];
      return record.results.map((r: Record<string, unknown>) => ({
        code: r.code,
        name: r.name ?? "",
        score: r.score ?? "",
        ...((r.factors as Record<string, unknown>) ?? {}),
      }));
    }
    // Export scan index
    const entries = this.deps.scanHistoryStore.query({});
    return entries.map((e: Record<string, unknown>) => ({
      id: e.id,
      screenerId: e.screenerId,
      universe: e.universe,
      market: e.market,
      totalScreened: e.totalScreened,
      resultCount: e.resultCount,
      createdAt: e.createdAt,
      topCodes: Array.isArray(e.topCodes) ? (e.topCodes as string[]).join(",") : "",
    }));
  }

  private async fetchEquity(
    options?: Record<string, unknown>,
  ): Promise<Record<string, unknown>[]> {
    const mode = (options?.mode as string) ?? "paper";
    let eh = this.deps.equityHistory;
    if (mode === "live" && this.deps.equityHistoryLive) {
      eh = this.deps.equityHistoryLive;
    } else if (mode === "paper" && this.deps.equityHistoryPaper) {
      eh = this.deps.equityHistoryPaper;
    }
    const snapshots = eh.getAll();
    return snapshots.map((s) => ({
      date: s.date,
      equity: s.equity,
      cash: s.cash,
      marketValue: s.marketValue,
      dailyPnl: s.dailyPnl,
      positionCount: s.positionCount,
      benchmark: s.benchmark ?? "",
    }));
  }

  // ── File writing ──────────────────────────────────────────────

  private async writeCsv(filePath: string, rows: Record<string, unknown>[]): Promise<void> {
    if (rows.length === 0) return;
    const headers = Object.keys(rows[0]!);
    const lines = [headers.join(",")];
    for (const row of rows) {
      const values = headers.map((h) => {
        const v = row[h];
        if (v == null || v === "") return "";
        const s = String(v);
        // Escape CSV values containing commas, quotes, or newlines
        if (s.includes(",") || s.includes('"') || s.includes("\n")) {
          return `"${s.replace(/"/g, '""')}"`;
        }
        return s;
      });
      lines.push(values.join(","));
    }
    await fs.writeFile(filePath, lines.join("\n"), "utf-8");
  }

  private async writeXlsx(
    filePath: string,
    rows: Record<string, unknown>[],
    sheetName: string,
  ): Promise<void> {
    // Dynamic import to avoid hard dependency when xlsx is not installed
    try {
      const XLSX = await import("xlsx");
      const ws = XLSX.utils.json_to_sheet(rows);
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, sheetName.slice(0, 31));
      XLSX.writeFile(wb, filePath);
    } catch (err) {
      if ((err as Error).message?.includes("Cannot find module") ||
          (err as { code?: string }).code === "ERR_MODULE_NOT_FOUND") {
        // Fallback to CSV if xlsx is not installed
        const csvPath = filePath.replace(/\.xlsx$/, ".csv");
        await this.writeCsv(csvPath, rows);
        throw new Error(
          "xlsx 依赖未安装，已降级为 CSV 格式。请运行 pnpm add xlsx 安装 Excel 支持。",
        );
      }
      throw err;
    }
  }

  // ── Helpers ───────────────────────────────────────────────────

  private async readJsonArray(filename: string): Promise<Record<string, unknown>[]> {
    const { config } = this.deps;
    if (!config.dataDir) return [];
    try {
      const raw = await fs.readFile(path.join(config.dataDir, filename), "utf-8");
      const data = JSON.parse(raw);
      return Array.isArray(data) ? data : [];
    } catch {
      return [];
    }
  }
}
