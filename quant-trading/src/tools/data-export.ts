/**
 * data_export tool — AI-callable data export service (FR-7).
 *
 * Enables stock traders to export their data in CSV or JSON format:
 *   - portfolio    — current positions (paper & live)
 *   - trades       — closed trade history
 *   - signals      — trading signal log
 *   - orders       — order history
 *   - equity       — equity curve snapshots
 *   - journal      — trade journal notes
 *   - backtest     — backtest result details
 *   - klines       — cached K-line data for a symbol
 *
 * Exported files are written to a user-accessible directory and the
 * path is returned so the user can open / share / import into Excel.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import type { LiveEngine } from "../engine/live-engine.js";
import type { BacktestStore } from "../services/backtest-store.js";
import type { EquityHistory } from "../services/equity-history.js";
import type { FavoritesStore } from "../services/favorites-store.js";
import type { ScanHistoryStore } from "../services/scan-history-store.js";
import type { QuantStorage } from "../services/sqlite-storage.js";
import type { TradeJournal } from "../services/trade-journal.js";
import type { Portfolio, QuantTradingConfig } from "../types.js";

// ── CSV helpers ─────────────────────────────────────────────────

function escapeCsv(value: unknown): string {
  if (value == null) return "";
  const s = String(value);
  if (s.includes(",") || s.includes('"') || s.includes("\n")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function toCsv(headers: string[], rows: Record<string, unknown>[]): string {
  const lines = [headers.map(escapeCsv).join(",")];
  for (const row of rows) {
    lines.push(headers.map((h) => escapeCsv(row[h])).join(","));
  }
  return lines.join("\n");
}

// ── Export directory ────────────────────────────────────────────

function getExportDir(config: QuantTradingConfig): string {
  const base = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  return path.join(base, "exports");
}

function makeFilename(dataset: string, format: string): string {
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  return `${dataset}_${ts}.${format}`;
}

// ── Portfolio loader (mirrors portfolio tool logic) ─────────────

async function loadPaperPortfolio(config: QuantTradingConfig): Promise<Portfolio> {
  const dir = config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
  for (const name of ["portfolio-paper.json", "portfolio.json"]) {
    try {
      const raw = await fs.readFile(path.join(dir, name), "utf-8");
      return JSON.parse(raw) as Portfolio;
    } catch {
      continue;
    }
  }
  return { id: "default", name: "默认组合", cash: 0, totalValue: 0, positions: [], createdAt: "", updatedAt: "" };
}

// ── Tool factory ────────────────────────────────────────────────

export interface DataExportDeps {
  config: QuantTradingConfig;
  quantStorage: QuantStorage;
  equityHistory: EquityHistory;
  journal: TradeJournal;
  backtestStore?: BacktestStore;
  scanHistoryStore?: ScanHistoryStore;
  favoritesStore?: FavoritesStore;
  getEngine?: () => LiveEngine | null;
}

export function createDataExportTool(api: OpenClawPluginApi, deps: DataExportDeps) {
  const { config, quantStorage, equityHistory, journal, backtestStore, scanHistoryStore, favoritesStore, getEngine } = deps;

  return {
    name: "data_export",
    description:
      "数据导出服务。将交易数据导出为 CSV 或 JSON 文件，便于在 Excel、报税软件等外部工具中使用。\n" +
      "• action=portfolio — 导出当前持仓（模拟盘 & 实盘）\n" +
      "• action=trades — 导出已平仓交易记录\n" +
      "• action=signals — 导出交易信号历史\n" +
      "• action=orders — 导出订单历史\n" +
      "• action=equity — 导出权益曲线（资金变化历史）\n" +
      "• action=journal — 导出交易笔记/复盘日志\n" +
      "• action=backtest — 导出回测结果详情\n" +
      "• action=screen — 导出选股/筛选结果\n" +
      "• action=klines — 导出指定标的的K线缓存数据\n" +
      "• action=watchlist — 导出自选股/收藏列表\n" +
      "• action=batch — 批量导出多种数据（盘后复盘一键导出）\n" +
      "• action=list — 列出已导出的文件\n" +
      "• action=delete — 删除指定的导出文件\n" +
      "• action=clean — 清理所有导出文件",
    parameters: {
      type: "object" as const,
      properties: {
        action: {
          type: "string",
          enum: [
            "portfolio",
            "trades",
            "signals",
            "orders",
            "equity",
            "journal",
            "backtest",
            "screen",
            "klines",
            "watchlist",
            "batch",
            "list",
            "delete",
            "clean",
          ],
          description: "导出的数据类型",
        },
        format: {
          type: "string",
          enum: ["csv", "json"],
          description: "导出格式，默认 csv",
        },
        symbol: {
          type: "string",
          description: "标的代码过滤（trades/signals/orders/klines 可用）",
        },
        startDate: {
          type: "string",
          description: "起始日期 YYYY-MM-DD（trades/signals/orders/equity/klines 可用）",
        },
        endDate: {
          type: "string",
          description: "结束日期 YYYY-MM-DD",
        },
        backtestId: {
          type: "string",
          description: "回测记录ID（action=backtest 时使用）",
        },
        screenId: {
          type: "string",
          description: "选股记录ID（action=screen 时使用，不传则导出最近一次筛选结果）",
        },
        interval: {
          type: "string",
          description: "K线周期，默认 1d（action=klines 时使用）",
        },
        limit: {
          type: "number",
          description: "最大导出条数，默认 10000",
        },
        mode: {
          type: "string",
          enum: ["paper", "live", "all"],
          description: "持仓模式（action=portfolio 时使用），默认 all",
        },
        category: {
          type: "string",
          description: "自选股分类过滤（action=watchlist 时使用，如 watchlist/ideas/shortlist）",
        },
        batchTypes: {
          type: "array",
          items: { type: "string" },
          description: "批量导出的数据类型列表（action=batch 时使用，如 [\"portfolio\",\"trades\",\"signals\"]）",
        },
        filename: {
          type: "string",
          description: "要删除的导出文件名（action=delete 时使用）",
        },
      },
      required: ["action"],
    },

    async execute(args: Record<string, unknown>) {
      const action = args.action as string;
      const format = (args.format as string) ?? "csv";
      const symbol = args.symbol as string | undefined;
      const startDate = args.startDate as string | undefined;
      const endDate = args.endDate as string | undefined;
      const limit = (args.limit as number) ?? 10000;

      if (action === "list") {
        return listExports(config);
      }

      if (action === "delete") {
        return deleteExport(config, args.filename as string | undefined);
      }

      if (action === "clean") {
        return cleanExports(config);
      }

      if (action === "batch") {
        return batchExport(config, args, deps);
      }

      const exportDir = getExportDir(config);
      await fs.mkdir(exportDir, { recursive: true });

      let data: Record<string, unknown>[];
      let headers: string[];
      let dataset: string;

      switch (action) {
        case "portfolio": {
          const result = await exportPortfolio(config, getEngine, args.mode as string | undefined);
          data = result.rows;
          headers = result.headers;
          dataset = "portfolio";
          break;
        }
        case "trades": {
          const rows = await quantStorage.getTrades({ symbol, limit });
          headers = [
            "symbol", "side", "entryPrice", "exitPrice", "quantity",
            "entryTime", "exitTime", "netPnl", "pnlPct", "commission",
            "strategyId",
          ];
          data = rows.map((r) => ({ ...r }));
          dataset = "trades";
          break;
        }
        case "signals": {
          const rows = await quantStorage.getSignals({ symbol, strategyId: undefined, limit });
          headers = [
            "date", "timestamp", "symbol", "direction", "price",
            "quantity", "reason", "confidence", "strategyId",
          ];
          data = rows.map((r) => ({
            ...r,
            date: r.date ?? new Date(r.timestamp).toISOString().slice(0, 10),
          }));
          dataset = "signals";
          break;
        }
        case "orders": {
          const rows = await quantStorage.getOrders({ symbol, limit });
          headers = [
            "orderId", "symbol", "market", "side", "orderType",
            "quantity", "price", "status", "filledQty", "filledPrice",
            "strategyId", "createdAt", "updatedAt",
          ];
          data = rows.map((r) => ({ ...r }));
          dataset = "orders";
          break;
        }
        case "equity": {
          const snaps = await quantStorage.getEquitySnapshots({
            start: startDate,
            end: endDate,
            limit,
          });
          headers = ["date", "equity", "cash", "marketValue", "dailyPnl", "positionCount", "benchmark"];
          data = snaps.map((r) => ({ ...r }));
          dataset = "equity";
          break;
        }
        case "journal": {
          const notes = exportJournal(journal, symbol, startDate, endDate, limit);
          headers = [
            "id", "date", "symbol", "direction", "entryPrice", "exitPrice",
            "pnl", "rating", "tags", "content", "lesson", "strategyId",
            "createdAt",
          ];
          data = notes;
          dataset = "journal";
          break;
        }
        case "backtest": {
          const backtestId = args.backtestId as string | undefined;
          const result = await exportBacktest(backtestStore, backtestId);
          headers = result.headers;
          data = result.rows;
          dataset = "backtest";
          break;
        }
        case "klines": {
          if (!symbol) {
            return { status: "error", message: "action=klines 需要指定 symbol 参数" };
          }
          const interval = (args.interval as string) ?? "1d";
          const start = startDate ?? "2000-01-01";
          const end = endDate ?? new Date().toISOString().slice(0, 10);
          const rows = await quantStorage.getKlines(symbol, interval, start, end);
          headers = ["symbol", "date", "interval", "open", "high", "low", "close", "volume", "amount"];
          data = rows.slice(0, limit).map((r) => ({ ...r }));
          dataset = `klines_${symbol}`;
          break;
        }
        case "screen": {
          const result = await exportScreen(scanHistoryStore, args.screenId as string | undefined);
          headers = result.headers;
          data = result.rows;
          dataset = "screen";
          break;
        }
        case "watchlist": {
          const result = exportWatchlist(favoritesStore, args.category as string | undefined);
          headers = result.headers;
          data = result.rows;
          dataset = "watchlist";
          break;
        }
        default:
          return { status: "error", message: `未知的 action: ${action}` };
      }

      // Write file
      const filename = makeFilename(dataset, format);
      const filePath = path.join(exportDir, filename);

      if (format === "json") {
        await fs.writeFile(filePath, JSON.stringify(data, null, 2), "utf-8");
      } else {
        const csv = toCsv(headers, data);
        // BOM for Excel CJK compatibility
        await fs.writeFile(filePath, "\uFEFF" + csv, "utf-8");
      }

      return {
        status: "ok",
        dataset: action,
        format,
        rows: data.length,
        file: filePath,
        filename,
        message: `已导出 ${data.length} 条${datasetLabel(action)}记录到 ${filePath}`,
      };
    },
  };
}

// ── Export helpers ───────────────────────────────────────────────

function datasetLabel(action: string): string {
  const labels: Record<string, string> = {
    portfolio: "持仓",
    trades: "交易",
    signals: "信号",
    orders: "订单",
    equity: "权益",
    journal: "笔记",
    backtest: "回测",
    screen: "选股",
    klines: "K线",
    watchlist: "自选股",
  };
  return labels[action] ?? action;
}

async function listExports(config: QuantTradingConfig) {
  const dir = getExportDir(config);
  try {
    const files = await fs.readdir(dir);
    const exportFiles = files.filter((f) => f.endsWith(".csv") || f.endsWith(".json"));
    const details = await Promise.all(
      exportFiles.map(async (f) => {
        const stat = await fs.stat(path.join(dir, f));
        return {
          filename: f,
          size: stat.size,
          sizeLabel: stat.size < 1024 ? `${stat.size}B` : `${(stat.size / 1024).toFixed(1)}KB`,
          createdAt: stat.mtime.toISOString(),
        };
      }),
    );
    details.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return {
      status: "ok",
      exportDir: dir,
      files: details,
      total: details.length,
    };
  } catch {
    return { status: "ok", exportDir: dir, files: [], total: 0 };
  }
}

async function exportPortfolio(
  config: QuantTradingConfig,
  getEngine?: () => LiveEngine | null,
  mode?: string,
): Promise<{ headers: string[]; rows: Record<string, unknown>[] }> {
  const headers = [
    "source", "symbol", "name", "quantity", "avgCost",
    "currentPrice", "marketValue", "unrealizedPnl", "unrealizedPnlPct",
    "weight",
  ];
  const rows: Record<string, unknown>[] = [];

  // Paper portfolio
  if (!mode || mode === "all" || mode === "paper") {
    const paper = await loadPaperPortfolio(config);
    for (const pos of paper.positions) {
      const mv = (pos.currentPrice ?? pos.avgCost) * pos.quantity;
      const pnl = ((pos.currentPrice ?? pos.avgCost) - pos.avgCost) * pos.quantity;
      const pnlPct = pos.avgCost > 0 ? (pnl / (pos.avgCost * pos.quantity)) * 100 : 0;
      rows.push({
        source: "paper",
        symbol: pos.symbol,
        name: pos.name ?? "",
        quantity: pos.quantity,
        avgCost: pos.avgCost,
        currentPrice: pos.currentPrice ?? pos.avgCost,
        marketValue: mv,
        unrealizedPnl: pnl,
        unrealizedPnlPct: Number(pnlPct.toFixed(2)),
        weight: paper.totalValue > 0 ? Number(((mv / paper.totalValue) * 100).toFixed(2)) : 0,
      });
    }
  }

  // Live positions
  if ((!mode || mode === "all" || mode === "live") && getEngine) {
    try {
      const engine = getEngine();
      if (engine) {
        const api = (engine as unknown as { api?: { getPositions?: () => Promise<unknown[]> } }).api;
        if (api?.getPositions) {
          const positions = (await api.getPositions()) as Array<Record<string, unknown>>;
          for (const pos of positions) {
            rows.push({
              source: "live",
              symbol: pos.symbol ?? "",
              name: pos.name ?? "",
              quantity: pos.quantity ?? pos.totalQty ?? 0,
              avgCost: pos.avgCost ?? pos.costPrice ?? 0,
              currentPrice: pos.currentPrice ?? pos.lastPrice ?? 0,
              marketValue: pos.marketValue ?? 0,
              unrealizedPnl: pos.unrealizedPnl ?? pos.floatPnl ?? 0,
              unrealizedPnlPct: pos.unrealizedPnlPct ?? pos.floatPnlPct ?? 0,
              weight: 0,
            });
          }
        }
      }
    } catch {
      // Live engine not available — skip
    }
  }

  return { headers, rows };
}

function exportJournal(
  journal: TradeJournal,
  symbol?: string,
  startDate?: string,
  endDate?: string,
  limit = 10000,
): Record<string, unknown>[] {
  // TradeJournal exposes notes via the view/search/recent actions.
  // We access the internal method to get all notes for export.
  const allNotes = (journal as unknown as { notes: Array<Record<string, unknown>> }).notes ?? [];

  let filtered = allNotes;
  if (symbol) {
    filtered = filtered.filter((n) => n.symbol === symbol);
  }
  if (startDate) {
    filtered = filtered.filter((n) => (n.date as string) >= startDate);
  }
  if (endDate) {
    filtered = filtered.filter((n) => (n.date as string) <= endDate);
  }

  return filtered.slice(0, limit).map((n) => ({
    id: n.id,
    date: n.date,
    symbol: n.symbol,
    direction: n.direction ?? "",
    entryPrice: n.entryPrice ?? "",
    exitPrice: n.exitPrice ?? "",
    pnl: n.pnl ?? "",
    rating: n.rating ?? "",
    tags: Array.isArray(n.tags) ? (n.tags as string[]).join(";") : "",
    content: n.content ?? "",
    lesson: n.lesson ?? "",
    strategyId: n.strategyId ?? "",
    createdAt: n.createdAt ?? "",
  }));
}

async function exportBacktest(
  backtestStore?: BacktestStore,
  backtestId?: string,
): Promise<{ headers: string[]; rows: Record<string, unknown>[] }> {
  if (!backtestStore) {
    return { headers: ["message"], rows: [{ message: "回测存储不可用" }] };
  }

  const headers = [
    "id", "createdAt", "strategyId", "symbols", "startDate", "endDate",
    "engine", "initialCash", "finalValue", "totalReturn",
    "annualizedReturn", "maxDrawdown", "sharpeRatio", "winRate",
    "totalTrades", "tags", "notes",
  ];

  if (backtestId) {
    // Export single backtest detail
    const record = backtestStore.getFullResult(backtestId);
    if (!record) {
      return { headers: ["message"], rows: [{ message: `未找到回测记录: ${backtestId}` }] };
    }
    const r = record.result;
    return {
      headers,
      rows: [
        {
          id: record.id,
          createdAt: record.createdAt,
          strategyId: r.strategyId,
          symbols: r.symbols.join(","),
          startDate: r.startDate,
          endDate: r.endDate,
          engine: record.engine,
          initialCash: r.initialCash,
          finalValue: r.finalValue,
          totalReturn: r.totalReturn,
          annualizedReturn: r.annualizedReturn,
          maxDrawdown: r.maxDrawdown,
          sharpeRatio: r.sharpeRatio,
          winRate: r.winRate,
          totalTrades: r.totalTrades,
          tags: record.tags.join(";"),
          notes: record.notes ?? "",
        },
      ],
    };
  }

  // Export all backtest summaries
  const all = backtestStore.query();
  const rows = all.map((entry: import("../services/backtest-store.js").BacktestIndexEntry) => ({
    id: entry.id,
    createdAt: entry.createdAt,
    strategyId: entry.strategyId,
    symbols: entry.symbols.join(","),
    startDate: entry.startDate,
    endDate: entry.endDate,
    engine: entry.engine,
    initialCash: entry.initialCash,
    finalValue: entry.finalValue,
    totalReturn: entry.totalReturn,
    annualizedReturn: entry.annualizedReturn,
    maxDrawdown: entry.maxDrawdown,
    sharpeRatio: entry.sharpeRatio,
    winRate: entry.winRate,
    totalTrades: entry.totalTrades,
    tags: entry.tags.join(";"),
    notes: entry.notes ?? "",
  }));

  return { headers, rows };
}

function exportScreen(
  scanHistoryStore?: ScanHistoryStore,
  screenId?: string,
): Promise<{ headers: string[]; rows: Record<string, unknown>[] }> {
  if (!scanHistoryStore) {
    return Promise.resolve({ headers: ["message"], rows: [{ message: "选股历史存储不可用" }] });
  }

  const headers = [
    "rank", "code", "name", "score", "screenerId",
    "universe", "market", "date", "signals",
  ];

  if (screenId) {
    const record = scanHistoryStore.getFullResult(screenId);
    if (!record) {
      return Promise.resolve({ headers: ["message"], rows: [{ message: `未找到选股记录: ${screenId}` }] });
    }
    const rows = record.results.map((entry, i) => ({
      rank: entry.rank ?? i + 1,
      code: entry.code,
      name: entry.name ?? "",
      score: entry.score ?? "",
      screenerId: record.screenerId,
      universe: record.universe,
      market: record.market,
      date: record.date,
      signals: entry.signals?.join(";") ?? "",
    }));
    return Promise.resolve({ headers, rows });
  }

  // No screenId — export the latest scan result
  const index = scanHistoryStore.query({ limit: 1, sortBy: "createdAt", sortDir: "desc" });
  if (index.length === 0) {
    return Promise.resolve({ headers: ["message"], rows: [{ message: "暂无选股历史记录" }] });
  }
  const latest = scanHistoryStore.getFullResult(index[0].id);
  if (!latest) {
    return Promise.resolve({ headers: ["message"], rows: [{ message: "无法读取选股记录详情" }] });
  }
  const rows = latest.results.map((entry, i) => ({
    rank: entry.rank ?? i + 1,
    code: entry.code,
    name: entry.name ?? "",
    score: entry.score ?? "",
    screenerId: latest.screenerId,
    universe: latest.universe,
    market: latest.market,
    date: latest.date,
    signals: entry.signals?.join(";") ?? "",
  }));
  return Promise.resolve({ headers, rows });
}

function exportWatchlist(
  favoritesStore?: FavoritesStore,
  category?: string,
): { headers: string[]; rows: Record<string, unknown>[] } {
  if (!favoritesStore) {
    return { headers: ["message"], rows: [{ message: "自选股存储不可用" }] };
  }

  const headers = [
    "code", "name", "category", "priority", "priceTarget",
    "convictionScore", "tags", "notes", "addedAt", "updatedAt",
  ];

  const stocks = favoritesStore.listFavorites(category ? { category } : undefined);
  const rows = stocks.map((s) => ({
    code: s.code,
    name: s.name ?? "",
    category: s.category ?? "",
    priority: s.priority ?? "",
    priceTarget: s.priceTarget ?? "",
    convictionScore: s.convictionScore ?? "",
    tags: s.tags?.join(";") ?? "",
    notes: s.notes ?? "",
    addedAt: s.addedAt,
    updatedAt: s.updatedAt,
  }));

  return { headers, rows };
}

async function deleteExport(config: QuantTradingConfig, filename?: string) {
  if (!filename) {
    return { status: "error", message: "action=delete 需要指定 filename 参数" };
  }
  const dir = getExportDir(config);
  const safe = path.basename(filename);
  const filePath = path.join(dir, safe);
  try {
    await fs.unlink(filePath);
    return { status: "ok", message: `已删除导出文件: ${safe}` };
  } catch {
    return { status: "error", message: `文件不存在或删除失败: ${safe}` };
  }
}

async function cleanExports(config: QuantTradingConfig) {
  const dir = getExportDir(config);
  try {
    const files = await fs.readdir(dir);
    const exportFiles = files.filter((f) => f.endsWith(".csv") || f.endsWith(".json"));
    await Promise.all(exportFiles.map((f) => fs.unlink(path.join(dir, f))));
    return { status: "ok", deleted: exportFiles.length, message: `已清理 ${exportFiles.length} 个导出文件` };
  } catch {
    return { status: "ok", deleted: 0, message: "导出目录为空或不存在" };
  }
}

async function batchExport(
  config: QuantTradingConfig,
  args: Record<string, unknown>,
  deps: DataExportDeps,
) {
  const { quantStorage, journal, backtestStore, scanHistoryStore, favoritesStore, getEngine } = deps;
  const format = (args.format as string) ?? "csv";
  const symbol = args.symbol as string | undefined;
  const startDate = args.startDate as string | undefined;
  const endDate = args.endDate as string | undefined;
  const limit = (args.limit as number) ?? 10000;

  const batchTypes = (args.batchTypes as string[] | undefined) ?? ["portfolio", "trades", "signals"];
  const validBatchTypes = ["portfolio", "trades", "signals", "orders", "equity", "journal", "klines", "backtest", "screen", "watchlist"];

  const invalidTypes = batchTypes.filter((t) => !validBatchTypes.includes(t));
  if (invalidTypes.length > 0) {
    return { status: "error", message: `不支持的批量导出类型: ${invalidTypes.join(", ")}` };
  }

  const exportDir = getExportDir(config);
  await fs.mkdir(exportDir, { recursive: true });

  const results: Array<{ dataset: string; rows: number; file: string; filename: string }> = [];

  for (const type of batchTypes) {
    let data: Record<string, unknown>[];
    let headers: string[];
    let dataset = type;

    switch (type) {
      case "portfolio": {
        const result = await exportPortfolio(config, getEngine, args.mode as string | undefined);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "trades":
        data = (await quantStorage.getTrades({ symbol, limit })).map((r) => ({ ...r }));
        headers = ["symbol", "side", "entryPrice", "exitPrice", "quantity", "entryTime", "exitTime", "netPnl", "pnlPct", "commission", "strategyId"];
        break;
      case "signals":
        data = (await quantStorage.getSignals({ symbol, strategyId: undefined, limit })).map((r) => ({
          ...r,
          date: r.date ?? new Date(r.timestamp).toISOString().slice(0, 10),
        }));
        headers = ["date", "timestamp", "symbol", "direction", "price", "quantity", "reason", "confidence", "strategyId"];
        break;
      case "orders":
        data = (await quantStorage.getOrders({ symbol, limit })).map((r) => ({ ...r }));
        headers = ["orderId", "symbol", "market", "side", "orderType", "quantity", "price", "status", "filledQty", "filledPrice", "strategyId", "createdAt", "updatedAt"];
        break;
      case "equity":
        data = (await quantStorage.getEquitySnapshots({ start: startDate, end: endDate, limit })).map((r) => ({ ...r }));
        headers = ["date", "equity", "cash", "marketValue", "dailyPnl", "positionCount", "benchmark"];
        break;
      case "journal":
        data = exportJournal(journal, symbol, startDate, endDate, limit);
        headers = ["id", "date", "symbol", "direction", "entryPrice", "exitPrice", "pnl", "rating", "tags", "content", "lesson", "strategyId", "createdAt"];
        break;
      case "klines": {
        if (!symbol) {
          results.push({ dataset: "klines", rows: 0, file: "", filename: "(跳过: 未指定 symbol)" });
          continue;
        }
        const interval = (args.interval as string) ?? "1d";
        const start = startDate ?? "2000-01-01";
        const end = endDate ?? new Date().toISOString().slice(0, 10);
        data = (await quantStorage.getKlines(symbol, interval, start, end)).slice(0, limit).map((r) => ({ ...r }));
        headers = ["symbol", "date", "interval", "open", "high", "low", "close", "volume", "amount"];
        dataset = `klines_${symbol}`;
        break;
      }
      case "backtest": {
        const result = await exportBacktest(backtestStore, args.backtestId as string | undefined);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "screen": {
        const result = await exportScreen(scanHistoryStore, args.screenId as string | undefined);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "watchlist": {
        const result = exportWatchlist(favoritesStore, args.category as string | undefined);
        data = result.rows;
        headers = result.headers;
        break;
      }
      default:
        continue;
    }

    const filename = makeFilename(dataset, format);
    const filePath = path.join(exportDir, filename);

    if (format === "json") {
      await fs.writeFile(filePath, JSON.stringify(data, null, 2), "utf-8");
    } else {
      const csv = toCsv(headers, data);
      await fs.writeFile(filePath, "\uFEFF" + csv, "utf-8");
    }

    results.push({ dataset: type, rows: data.length, file: filePath, filename });
  }

  const totalRows = results.reduce((sum, r) => sum + r.rows, 0);
  return {
    status: "ok",
    batchTypes,
    format,
    results,
    totalFiles: results.length,
    totalRows,
    message: `批量导出完成: ${results.length} 个文件, 共 ${totalRows} 条记录`,
  };
}