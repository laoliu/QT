/**
 * data_export tool 66AI-callable data export service (FR-7).
 *
 * Enables stock traders to export their data in CSV or JSON format:
 *   - portfolio    66current positions (paper & live)
 *   - trades       66closed trade history
 *   - signals      66trading signal log
 *   - orders       66order history
 *   - equity       66equity curve snapshots
 *   - journal      66trade journal notes
 *   - backtest     66backtest result details
 *   - klines       66cached K-line data for a symbol
 *
 * Exported files are written to a user-accessible directory and the
 * path is returned so the user can open / share / import into Excel.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { ConditionalOrderService } from "../services/conditional-order.js";
import type { DcaScheduleService } from "../services/dca-schedule.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { AlertStore } from "../services/alert-store.js";
import type { BacktestStore } from "../services/backtest-store.js";
import type { EquityHistory } from "../services/equity-history.js";
import type { FavoritesStore } from "../services/favorites-store.js";
import type { ScanHistoryStore } from "../services/scan-history-store.js";
import type { SignalHitRateTracker } from "../services/signal-hitrate.js";
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
  alertStore?: AlertStore;
  signalHitRateTracker?: SignalHitRateTracker;
  getEngine?: () => LiveEngine | null;
  conditionalOrderService?: ConditionalOrderService;
  dcaScheduleService?: DcaScheduleService;
}

export function createDataExportTool(api: OpenClawPluginApi, deps: DataExportDeps) {
  const { config, quantStorage, equityHistory, journal, backtestStore, scanHistoryStore, favoritesStore, alertStore, signalHitRateTracker, getEngine, conditionalOrderService, dcaScheduleService } = deps;

  return {
    name: "data_export",
    description:
      "数据导出服务。将交易数据导出一CSV 成JSON 文件，便于在 Excel、报税软件等外部工具中使用。\n" +
      "66action=portfolio 6602020700 & 0601\n" +
      "66action=trades 6609030604\n" +
      "66action=signals 660208\n" +
      "66action=orders 6608\n" +
      "66action=equity 6607130408\n" +
      "66action=journal 660503/复盘日志\n" +
      "66action=backtest 6610\n" +
      "66action=screen 6605/0505\n" +
      "66action=klines 660807K线缓存数据\n" +
      "66action=watchlist 6605/08\n" +
      "66action=alerts 66澯/000804\n" +
      "66action=hitrate 6602011404\n" +
      "66action=performance 660811\n" +
      "66action=fund_flow 66041507/北向资金）\n" +
      "66action=sector 661502040904\n" +
      "66action=institutional 660215QFII/_02\n" +
      "66action=conditional_orders 6609/0907/010902\n" +
      "66action=dca_plans 660407\n" +
      "66action=batch  批量导出多种数据（盘后复盘一键导出）\n" +
      "66action=list 66050504\n" +
      "66action=delete 6601080704\n" +
      "66action=clean 66е",
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
            "alerts",
            "hitrate",
            "performance",
            "fund_flow",
            "sector",
            "institutional",
            "conditional_orders",
            "dca_plans",
            "batch",
            "list",
            "delete",
            "clean",
          ],
          description: "导出的数据类",
        },
        format: {
          type: "string",
          enum: ["csv", "json"],
          description: "0408xcsv",
        },
        symbol: {
          type: "string",
          description: "标的代码过滤（trades/signals/orders/klines 可用",
        },
        startDate: {
          type: "string",
          description: "起始日期 YYYY-MM-DD（trades/signals/orders/equity/klines 可用",
        },
        endDate: {
          type: "string",
          description: "结束日期 YYYY-MM-DD",
        },
        backtestId: {
          type: "string",
          description: "1004ID（action=backtest 020000",
        },
        screenId: {
          type: "string",
          description: "050904ID（action=screen 02000091050505",
        },
        interval: {
          type: "string",
          description: "K1308 1d（action=klines 020000",
        },
        limit: {
          type: "number",
          description: "最大导出条数，默认 10000",
        },
        mode: {
          type: "string",
          enum: ["paper", "live", "all"],
          description: "020004action=portfolio 02000008xall",
        },
        category: {
          type: "string",
          description: "050407action=watchlist 020000jwatchlist/ideas/shortlist锛",
        },
        batchTypes: {
          type: "array",
          items: { type: "string" },
          description: "批量导出的数据类型列表（action=batch 020000j[\"portfolio\",\"trades\",\"signals\"]锛",
        },
        filename: {
          type: "string",
          description: "08010704action=delete 020000",
        },
        alertSeverity: {
          type: "string",
          enum: ["info", "warning", "critical"],
          description: "告警级别过滤（action=alerts 020000",
        },
        alertSource: {
          type: "string",
          description: "010607action=alerts 020000jpnl_monitor/risk_gate/price_alert/signal锛",
        },
        flowType: {
          type: "string",
          enum: ["rank", "stock", "sector", "northbound"],
          description: "资金流向类型（action=fund_flow 020000: rank=个股排名, stock=个股历史, sector=09, northbound=北向资金",
        },
        sectorName: {
          type: "string",
          description: "板块名称（action=sector 020000j'11, '08', '新能満锛",
        },
        sectorAction: {
          type: "string",
          enum: ["ranking", "detail"],
          description: "板块分析类型（action=sector 020000: ranking=板块排名, detail=0409",
        },
        instType: {
          type: "string",
          enum: ["fund", "qfii", "insurance", "social_security", "trust", "broker"],
          description: "机构类型（action=institutional 020000: fund=基金, qfii=QFII, insurance=保险, social_security=_, trust=信托, broker=04",
        },
        activeOnly: {
          type: "boolean",
          description: "是否仅导出活跃条件单（action=conditional_orders 02000008 false 导出全部",
        },
        dcaStatus: {
          type: "string",
          enum: ["active", "paused", "cancelled"],
          description: "0407080007action=dca_plans 0200009100",
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
            return { status: "error", message: "action=klines 0808symbol 参数" };
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
        case "alerts": {
          const result = exportAlerts(alertStore, args.alertSeverity as string | undefined, args.alertSource as string | undefined, startDate, endDate, limit);
          headers = result.headers;
          data = result.rows;
          dataset = "alerts";
          break;
        }
        case "hitrate": {
          const result = exportHitRate(signalHitRateTracker, symbol, limit);
          headers = result.headers;
          data = result.rows;
          dataset = "hitrate";
          break;
        }
        case "performance": {
          const result = await exportPerformance(quantStorage, startDate, endDate);
          headers = result.headers;
          data = result.rows;
          dataset = "performance";
          break;
        }
        case "fund_flow": {
          const result = await exportFundFlow(config, args.flowType as string | undefined, symbol, limit);
          headers = result.headers;
          data = result.rows;
          dataset = "fund_flow";
          break;
        }
        case "sector": {
          const result = await exportSector(config, args.sectorAction as string | undefined, args.sectorName as string | undefined, limit);
          headers = result.headers;
          data = result.rows;
          dataset = "sector";
          break;
        }
        case "institutional": {
          const result = await exportInstitutional(config, args.instType as string | undefined, symbol, limit);
          headers = result.headers;
          data = result.rows;
          dataset = "institutional";
          break;
        }
        case "conditional_orders": {
          const result = exportConditionalOrders(conditionalOrderService, args.activeOnly as boolean | undefined);
          headers = result.headers;
          data = result.rows;
          dataset = "conditional_orders";
          break;
        }
        case "dca_plans": {
          const result = exportDcaPlans(dcaScheduleService, args.dcaStatus as string | undefined);
          headers = result.headers;
          data = result.rows;
          dataset = "dca_plans";
          break;
        }
        default:
          return { status: "error", message: `ā04action: ${action}` };
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
        message: `05${data.length} 07{datasetLabel(action)}04${filePath}`,
      };
    },
  };
}

// ── Export helpers ───────────────────────────────────────────────

function datasetLabel(action: string): string {
  const labels: Record<string, string> = {
    portfolio: "02",
    trades: "交易",
    signals: "02",
    orders: "订单",
    equity: "07",
    journal: "03",
    backtest: "10",
    screen: "05",
    klines: "K绾",
    watchlist: "05",
    alerts: "澯",
    hitrate: "信号命中",
    performance: "绩效报告",
    fund_flow: "资金流向",
    sector: "板块分析",
    institutional: "机构持仓",
    conditional_orders: "条件",
    dca_plans: "0407",
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
      // Live engine not available 66skip
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
    return { headers: ["message"], rows: [{ message: "10" }] };
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
      return { headers: ["message"], rows: [{ message: `ā0910" ${backtestId}` }] };
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
    return Promise.resolve({ headers: ["message"], rows: [{ message: "0508" }] });
  }

  const headers = [
    "rank", "code", "name", "score", "screenerId",
    "universe", "market", "date", "signals",
  ];

  if (screenId) {
    const record = scanHistoryStore.getFullResult(screenId);
    if (!record) {
      return Promise.resolve({ headers: ["message"], rows: [{ message: `ā09050904: ${screenId}` }] });
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

  // No screenId 66export the latest scan result
  const index = scanHistoryStore.query({ limit: 1, sortBy: "createdAt", sortDir: "desc" });
  if (index.length === 0) {
    return Promise.resolve({ headers: ["message"], rows: [{ message: "050804" }] });
  }
  const latest = scanHistoryStore.getFullResult(index[0].id);
  if (!latest) {
    return Promise.resolve({ headers: ["message"], rows: [{ message: "1900050904" }] });
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
    return { headers: ["message"], rows: [{ message: "0501" }] };
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
    return { status: "error", message: "action=delete 0808filename 参数" };
  }
  const dir = getExportDir(config);
  const safe = path.basename(filename);
  const filePath = path.join(dir, safe);
  try {
    await fs.unlink(filePath);
    return { status: "ok", message: `已删除导出文" ${safe}` };
  } catch {
    return { status: "error", message: `04170102: ${safe}` };
  }
}

async function cleanExports(config: QuantTradingConfig) {
  const dir = getExportDir(config);
  try {
    const files = await fs.readdir(dir);
    const exportFiles = files.filter((f) => f.endsWith(".csv") || f.endsWith(".json"));
    await Promise.all(exportFiles.map((f) => fs.unlink(path.join(dir, f))));
    return { status: "ok", deleted: exportFiles.length, message: `已清理${exportFiles.length} 个导出文件` };
  } catch {
    return { status: "ok", deleted: 0, message: "0704020790" };
  }
}

function exportAlerts(
  alertStore?: AlertStore,
  severity?: string,
  source?: string,
  startDate?: string,
  endDate?: string,
  limit = 10000,
): { headers: string[]; rows: Record<string, unknown>[] } {
  if (!alertStore) {
    return { headers: ["message"], rows: [{ message: "07" }] };
  }

  const headers = ["id", "timestamp", "severity", "source", "message", "acknowledged"];

  const query: Record<string, unknown> = { limit };
  if (severity) query.severity = severity;
  if (source) query.source = source;
  if (startDate) query.after = startDate;
  if (endDate) query.before = endDate;

  const alerts = alertStore.query(query as Parameters<AlertStore["query"]>[0]);
  const rows = alerts.map((a) => ({
    id: a.id,
    timestamp: a.timestamp,
    severity: a.severity,
    source: a.source,
    message: a.message,
    acknowledged: a.acknowledged ? "鏄" : "鍚",
  }));

  return { headers, rows };
}

function exportHitRate(
  signalHitRateTracker?: SignalHitRateTracker,
  strategyFilter?: string,
  limit = 10000,
): { headers: string[]; rows: Record<string, unknown>[] } {
  if (!signalHitRateTracker) {
    return { headers: ["message"], rows: [{ message: "信号命中率追踪器不可" }] };
  }

  const headers = [
    "signalTimestamp", "symbol", "direction", "signalPrice", "strategyId",
    "reason", "confidence",
    "T+1_result", "T+1_price",
    "T+3_result", "T+3_price",
    "T+5_result", "T+5_price",
    "T+10_result", "T+10_price",
  ];

  let records = signalHitRateTracker.getRecent(limit);
  if (strategyFilter) {
    records = records.filter((r) => r.strategyId === strategyFilter);
  }

  const rows = records.map((r) => ({
    signalTimestamp: new Date(r.signalTimestamp).toISOString(),
    symbol: r.symbol,
    direction: r.direction,
    signalPrice: r.signalPrice,
    strategyId: r.strategyId,
    reason: r.reason ?? "",
    confidence: r.confidence ?? "",
    "T+1_result": r.checkpoints["T+1"]?.result ?? "",
    "T+1_price": r.checkpoints["T+1"]?.price ?? "",
    "T+3_result": r.checkpoints["T+3"]?.result ?? "",
    "T+3_price": r.checkpoints["T+3"]?.price ?? "",
    "T+5_result": r.checkpoints["T+5"]?.result ?? "",
    "T+5_price": r.checkpoints["T+5"]?.price ?? "",
    "T+10_result": r.checkpoints["T+10"]?.result ?? "",
    "T+10_price": r.checkpoints["T+10"]?.price ?? "",
  }));

  return { headers, rows };
}

async function exportPerformance(
  quantStorage: QuantStorage,
  startDate?: string,
  endDate?: string,
): Promise<{ headers: string[]; rows: Record<string, unknown>[] }> {
  const headers = ["metric", "value"];

  const dateFrom = startDate ?? "2000-01-01";
  const dateTo = endDate ?? new Date().toISOString().slice(0, 10);

  const trades = await quantStorage.getTrades({ limit: 100000 });
  const closedTrades = trades.filter((t) => {
    const exit = t.exitTime;
    if (!exit) return false;
    return exit >= dateFrom && exit <= dateTo;
  });

  const equitySnaps = await quantStorage.getEquitySnapshots({ start: dateFrom, end: dateTo, limit: 100000 });

  const totalTrades = closedTrades.length;
  const wins = closedTrades.filter((t) => t.netPnl > 0);
  const losses = closedTrades.filter((t) => t.netPnl <= 0);
  const totalNetPnl = closedTrades.reduce((sum, t) => sum + (t.netPnl ?? 0), 0);
  const totalGrossWin = wins.reduce((sum, t) => sum + (t.netPnl ?? 0), 0);
  const totalGrossLoss = Math.abs(losses.reduce((sum, t) => sum + (t.netPnl ?? 0), 0));
  const totalCommission = closedTrades.reduce((sum, t) => sum + (t.commission ?? 0), 0);
  const winRate = totalTrades > 0 ? wins.length / totalTrades : 0;
  const profitFactor = totalGrossLoss > 0 ? totalGrossWin / totalGrossLoss : totalGrossWin > 0 ? Infinity : 0;

  // Calculate max single profit/loss
  const pnls = closedTrades.map((t) => t.netPnl ?? 0);
  const maxSingleProfit = pnls.length > 0 ? Math.max(...pnls) : 0;
  const maxSingleLoss = pnls.length > 0 ? Math.min(...pnls) : 0;

  // Equity-based metrics
  let totalReturn = 0;
  let maxDrawdown = 0;
  if (equitySnaps.length > 1) {
    const firstEquity = equitySnaps[0].equity;
    const lastEquity = equitySnaps[equitySnaps.length - 1].equity;
    if (firstEquity > 0) totalReturn = (lastEquity - firstEquity) / firstEquity;

    let peak = firstEquity;
    for (const snap of equitySnaps) {
      const eq = snap.equity;
      if (eq > peak) peak = eq;
      const dd = peak > 0 ? (peak - eq) / peak : 0;
      if (dd > maxDrawdown) maxDrawdown = dd;
    }
  }

  const rows: Record<string, unknown>[] = [
    { metric: "统计区间", value: `${dateFrom} ~ ${dateTo}` },
    { metric: "1705", value: totalTrades },
    { metric: "盈利笔数", value: wins.length },
    { metric: "亏损笔数", value: losses.length },
    { metric: "09", value: `${(winRate * 100).toFixed(1)}%` },
    { metric: "07(Profit Factor)", value: profitFactor === Infinity ? "鈭" : profitFactor.toFixed(2) },
    { metric: "1807", value: totalNetPnl.toFixed(2) },
    { metric: "04", value: totalCommission.toFixed(2) },
    { metric: "6107", value: maxSingleProfit.toFixed(2) },
    { metric: "6106", value: maxSingleLoss.toFixed(2) },
    { metric: "累计收益", value: `${(totalReturn * 100).toFixed(2)}%` },
    { metric: "最大回", value: `${(maxDrawdown * 100).toFixed(2)}%` },
  ];

  return { headers, rows };
}

async function batchExport(
  config: QuantTradingConfig,
  args: Record<string, unknown>,
  deps: DataExportDeps,
) {
  const { quantStorage, journal, backtestStore, scanHistoryStore, favoritesStore, alertStore, signalHitRateTracker, getEngine } = deps;
  const format = (args.format as string) ?? "csv";
  const symbol = args.symbol as string | undefined;
  const startDate = args.startDate as string | undefined;
  const endDate = args.endDate as string | undefined;
  const limit = (args.limit as number) ?? 10000;

  const batchTypes = (args.batchTypes as string[] | undefined) ?? ["portfolio", "trades", "signals"];
  const validBatchTypes = ["portfolio", "trades", "signals", "orders", "equity", "journal", "klines", "backtest", "screen", "watchlist", "alerts", "hitrate", "performance", "fund_flow", "sector", "institutional", "conditional_orders", "dca_plans"];

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
          results.push({ dataset: "klines", rows: 0, file: "", filename: "(跳过: ā08symbol)" });
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
      case "alerts": {
        const result = exportAlerts(alertStore, args.alertSeverity as string | undefined, args.alertSource as string | undefined, startDate, endDate, limit);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "hitrate": {
        const result = exportHitRate(signalHitRateTracker, symbol, limit);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "performance": {
        const result = await exportPerformance(quantStorage, startDate, endDate);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "fund_flow": {
        const result = await exportFundFlow(config, args.flowType as string | undefined, symbol, limit);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "sector": {
        const result = await exportSector(config, args.sectorAction as string | undefined, args.sectorName as string | undefined, limit);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "institutional": {
        const result = await exportInstitutional(config, args.instType as string | undefined, symbol, limit);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "conditional_orders": {
        const result = exportConditionalOrders(conditionalOrderService, args.activeOnly as boolean | undefined);
        data = result.rows;
        headers = result.headers;
        break;
      }
      case "dca_plans": {
        const result = exportDcaPlans(dcaScheduleService, args.dcaStatus as string | undefined);
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
    message: `批量导出完成: ${results.length} 04 入${totalRows} 04`,
  };
}

// ── Fund flow export helper ─────────────────────────────────────

async function exportFundFlow(
  config: QuantTradingConfig,
  flowType?: string,
  symbol?: string,
  limit = 50,
): Promise<{ headers: string[]; rows: Record<string, unknown>[] }> {
  const action = flowType ?? "rank";

  const pyResult = await runPythonScript<{
    stocks?: Array<Record<string, unknown>>;
    sectors?: Array<Record<string, unknown>>;
    flows?: Array<Record<string, unknown>>;
    error?: string;
  }>("engines/fund_flow.py", { action, symbol, limit }, config);

  if (!pyResult.ok || !pyResult.data || pyResult.data.error) {
    return {
      headers: ["message"],
      rows: [{ message: pyResult.data?.error ?? pyResult.error ?? "04190002" }],
    };
  }

  const data = pyResult.data;

  if (action === "sector" && data.sectors) {
    const headers = ["sector", "changePct", "mainNetInflow", "superLargeInflow", "largeInflow", "mediumInflow", "smallInflow"];
    const rows = data.sectors.slice(0, limit).map((s) => ({
      sector: s.name ?? s.sector ?? "",
      changePct: s.changePct ?? s.change_pct ?? "",
      mainNetInflow: s.mainNetInflow ?? s.main_net_inflow ?? "",
      superLargeInflow: s.superLargeInflow ?? s.super_large_inflow ?? "",
      largeInflow: s.largeInflow ?? s.large_inflow ?? "",
      mediumInflow: s.mediumInflow ?? s.medium_inflow ?? "",
      smallInflow: s.smallInflow ?? s.small_inflow ?? "",
    }));
    return { headers, rows };
  }

  if (action === "northbound" && data.flows) {
    const headers = ["date", "shConnect", "szConnect", "total", "cumulative"];
    const rows = data.flows.slice(0, limit).map((f) => ({
      date: f.date ?? "",
      shConnect: f.shConnect ?? f.sh_connect ?? "",
      szConnect: f.szConnect ?? f.sz_connect ?? "",
      total: f.total ?? "",
      cumulative: f.cumulative ?? "",
    }));
    return { headers, rows };
  }

  // Default: stock flow ranking or individual stock flow
  const items = data.stocks ?? data.flows ?? [];
  const headers = ["symbol", "name", "changePct", "mainNetInflow", "superLargeInflow", "largeInflow", "mediumInflow", "smallInflow", "date"];
  const rows = items.slice(0, limit).map((s) => ({
    symbol: s.symbol ?? s.code ?? "",
    name: s.name ?? "",
    changePct: s.changePct ?? s.change_pct ?? "",
    mainNetInflow: s.mainNetInflow ?? s.main_net_inflow ?? "",
    superLargeInflow: s.superLargeInflow ?? s.super_large_inflow ?? "",
    largeInflow: s.largeInflow ?? s.large_inflow ?? "",
    mediumInflow: s.mediumInflow ?? s.medium_inflow ?? "",
    smallInflow: s.smallInflow ?? s.small_inflow ?? "",
    date: s.date ?? "",
  }));
  return { headers, rows };
}

// ── Sector analysis export helper ───────────────────────────────

async function exportSector(
  config: QuantTradingConfig,
  sectorAction?: string,
  sector?: string,
  limit = 50,
): Promise<{ headers: string[]; rows: Record<string, unknown>[] }> {
  const action = sectorAction ?? "ranking";

  const pyResult = await runPythonScript<{
    sectors?: Array<Record<string, unknown>>;
    stocks?: Array<Record<string, unknown>>;
    error?: string;
  }>("engines/sector_analysis.py", { action, sector, limit }, config);

  if (!pyResult.ok || !pyResult.data || pyResult.data.error) {
    return {
      headers: ["message"],
      rows: [{ message: pyResult.data?.error ?? pyResult.error ?? "190002" }],
    };
  }

  const data = pyResult.data;

  if (action === "detail" && data.stocks) {
    const headers = ["rank", "code", "name", "changePct", "price", "turnover", "amount", "pe", "sector"];
    const rows = data.stocks.slice(0, limit).map((s, i) => ({
      rank: s.rank ?? i + 1,
      code: s.code ?? s.symbol ?? "",
      name: s.name ?? "",
      changePct: s.changePct ?? s.change_pct ?? "",
      price: s.price ?? s.close ?? "",
      turnover: s.turnover ?? "",
      amount: s.amount ?? "",
      pe: s.pe ?? "",
      sector: sector ?? "",
    }));
    return { headers, rows };
  }

  // Default: sector ranking
  const headers = ["rank", "sector", "changePct", "turnover", "amount", "leadingStock", "leadingChangePct", "stockCount", "upCount", "downCount"];
  const sectors = data.sectors ?? [];
  const rows = sectors.slice(0, limit).map((s, i) => ({
    rank: s.rank ?? i + 1,
    sector: s.name ?? s.sector ?? "",
    changePct: s.changePct ?? s.change_pct ?? "",
    turnover: s.turnover ?? "",
    amount: s.amount ?? "",
    leadingStock: s.leadingStock ?? s.leading_stock ?? "",
    leadingChangePct: s.leadingChangePct ?? s.leading_change_pct ?? "",
    stockCount: s.stockCount ?? s.stock_count ?? "",
    upCount: s.upCount ?? s.up_count ?? "",
    downCount: s.downCount ?? s.down_count ?? "",
  }));
  return { headers, rows };
}

// ── Institutional flow export helper ────────────────────────────

async function exportInstitutional(
  config: QuantTradingConfig,
  instType?: string,
  symbol?: string,
  limit = 50,
): Promise<{ headers: string[]; rows: Record<string, unknown>[] }> {
  const action = symbol ? "stock" : "ranking";

  const pyResult = await runPythonScript<{
    holdings?: Array<Record<string, unknown>>;
    trades?: Array<Record<string, unknown>>;
    error?: string;
  }>("engines/institutional_flow.py", { action, instType: instType ?? "fund", symbol, limit }, config);

  if (!pyResult.ok || !pyResult.data || pyResult.data.error) {
    return {
      headers: ["message"],
      rows: [{ message: pyResult.data?.error ?? pyResult.error ?? "02190002" }],
    };
  }

  const data = pyResult.data;
  const items = data.holdings ?? data.trades ?? [];

  const headers = ["symbol", "name", "instType", "holdShares", "holdRatio", "changeShares", "changeRatio", "reportDate"];
  const rows = items.slice(0, limit).map((h) => ({
    symbol: h.symbol ?? h.code ?? "",
    name: h.name ?? "",
    instType: h.instType ?? h.inst_type ?? instType ?? "",
    holdShares: h.holdShares ?? h.hold_shares ?? "",
    holdRatio: h.holdRatio ?? h.hold_ratio ?? "",
    changeShares: h.changeShares ?? h.change_shares ?? "",
    changeRatio: h.changeRatio ?? h.change_ratio ?? "",
    reportDate: h.reportDate ?? h.report_date ?? "",
  }));
  return { headers, rows };
}

// ── Conditional orders export helper ──────────────────────────

function exportConditionalOrders(
  conditionalOrderService?: ConditionalOrderService,
  activeOnly?: boolean,
): { headers: string[]; rows: Record<string, unknown>[] } {
  if (!conditionalOrderService) {
    return { headers: ["message"], rows: [{ message: "条件单服务不可用" }] };
  }

  const headers = [
    "id", "symbol", "type", "entryPrice", "triggerValue", "quantity",
    "strategyId", "description", "createdAt", "triggered", "triggeredAt", "triggerResult",
  ];

  const orders = conditionalOrderService.listOrders(activeOnly ?? false);
  const rows = orders.map((o) => ({
    id: o.id,
    symbol: o.symbol,
    type: o.type,
    entryPrice: o.entryPrice,
    triggerValue: o.triggerValue,
    quantity: o.quantity,
    strategyId: o.strategyId ?? "",
    description: o.description,
    createdAt: o.createdAt,
    triggered: o.triggered ? "鏄" : "鍚",
    triggeredAt: o.triggeredAt ?? "",
    triggerResult: o.triggerResult ?? "",
  }));

  return { headers, rows };
}

// ── DCA plans export helper ───────────────────────────────────

function exportDcaPlans(
  dcaScheduleService?: DcaScheduleService,
  status?: string,
): { headers: string[]; rows: Record<string, unknown>[] } {
  if (!dcaScheduleService) {
    return { headers: ["message"], rows: [{ message: "0417" }] };
  }

  const headers = [
    "id", "symbol", "amount", "frequency", "dayOfPeriod", "hour",
    "status", "executionCount", "totalInvested", "lastExecutedAt",
    "nextExecutionAt", "maxExecutions", "createdAt", "description",
  ];

  const plans = dcaScheduleService.listPlans(status as "active" | "paused" | "cancelled" | undefined);
  const rows = plans.map((p) => ({
    id: p.id,
    symbol: p.symbol,
    amount: p.amount,
    frequency: p.frequency,
    dayOfPeriod: p.dayOfPeriod ?? "",
    hour: p.hour ?? "",
    status: p.status,
    executionCount: p.executionCount,
    totalInvested: p.totalInvested,
    lastExecutedAt: p.lastExecutedAt ?? "",
    nextExecutionAt: p.nextExecutionAt ?? "",
    maxExecutions: p.maxExecutions ?? "",
    createdAt: p.createdAt,
    description: p.description,
  }));

  return { headers, rows };
}
