/**
 * HTTP route registrations for the quant-trading plugin.
 *
 * Extracted from index.ts to keep the main entry under ~400 LOC.
 * All routes are prefixed with `/quant/`.
 */
import type { ServerResponse } from "node:http";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import { createDataProvider, detectMarketFromSymbol } from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import type { AlertStore } from "../services/alert-store.js";
import type { BacktestStore } from "../services/backtest-store.js";
import type { BenchmarkService } from "../services/benchmark.js";
import type { DataQualityValidator } from "../services/data-quality.js";
import type { HistoricalDataWarehouse } from "../services/data-warehouse.js";
import type { EquityHistory } from "../services/equity-history.js";
import type { EventBus } from "../services/event-bus.js";
import type { FavoritesStore } from "../services/favorites-store.js";
import type { PriceAlertService } from "../services/price-alert.js";
import type { ScanHistoryStore } from "../services/scan-history-store.js";
import type { QuantStorage } from "../services/sqlite-storage.js";
import type { UserStrategyStore } from "../services/strategy-template.js";
import type { TaskScheduler } from "../services/task-scheduler.js";
import type { TradeJournal } from "../services/trade-journal.js";
import type { QuantTradingConfig, TradingSignal } from "../types.js";
import type { HttpHandler } from "./auth-middleware.js";
import { timingSafeCompareStr } from "./auth-middleware.js";
import { parseJsonBody } from "./parse-body.js";

// ── Helpers ──────────────────────────────────────────────────────

/** Write a JSON response on a raw ServerResponse. */
function json(res: ServerResponse, data: unknown, status = 200): void {
  const body = JSON.stringify(data, null, 2);
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(body);
}

function methodNotAllowed(res: ServerResponse): void {
  res.writeHead(405, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ status: "error", message: "Method not allowed" }));
}

/** Parse URL search params from a request. */
function urlParams(req: { url?: string }): URLSearchParams {
  return new URL(req.url ?? "/", "http://localhost").searchParams;
}

/** Escape a CSV field value (handles commas, quotes, newlines). */
function escapeCsvField(value: unknown): string {
  if (value === null || value === undefined) return "";
  const s = String(value);
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

// ── Dependencies ─────────────────────────────────────────────────

export interface RouteDeps {
  api: OpenClawPluginApi;
  config: QuantTradingConfig;
  alertStore: AlertStore;
  backtestStore: BacktestStore;
  eventBus: EventBus;
  equityHistory: EquityHistory;
  equityHistoryPaper?: EquityHistory;
  equityHistoryLive?: EquityHistory;
  benchmarkService: BenchmarkService;
  dataWarehouse: HistoricalDataWarehouse;
  dataQualityValidator: DataQualityValidator;
  quantStorage: QuantStorage;
  userStrategyStore: UserStrategyStore;
  getEngine: () => LiveEngine;
  priceAlertService: PriceAlertService;
  taskScheduler: TaskScheduler;
  tradeJournal: TradeJournal;
  scanHistoryStore: ScanHistoryStore;
  favoritesStore?: FavoritesStore;
  /** Full security stack (auth + CORS + rate-limit). */
  secure: (handler: HttpHandler) => HttpHandler;
  /** Public route: CORS + rate-limit only. */
  securePublic: (handler: HttpHandler) => HttpHandler;
}

// ── Route registration ───────────────────────────────────────────

export function registerQuantRoutes(deps: RouteDeps): void {
  const {
    api,
    config,
    alertStore,
    backtestStore,
    eventBus,
    equityHistory,
    equityHistoryPaper,
    equityHistoryLive,
    benchmarkService,
    dataWarehouse,
    dataQualityValidator,
    quantStorage,
    userStrategyStore,
    getEngine,
    secure,
    securePublic,
  } = deps;

  /** Resolve equity history instance by ?mode= param (paper|live), fallback to active */
  function resolveEquityHistory(params: URLSearchParams): EquityHistory {
    const mode = params.get("mode");
    if (mode === "paper" && equityHistoryPaper) return equityHistoryPaper;
    if (mode === "live" && equityHistoryLive) return equityHistoryLive;
    return equityHistory;
  }

  // ── Dashboard ──────────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/dashboard",
    handler: secure(async (_req, res) => {
      const { handleDashboardRequest } = await import("./dashboard.js");
      json(res, handleDashboardRequest());
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/dashboard/ui",
    handler: securePublic(async (_req, res) => {
      const { getDashboardHtml } = await import("./dashboard.js");
      const html = getDashboardHtml("");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── SSE Event Stream ──────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/events",
    handler: securePublic((req, res) => {
      const params = urlParams(req);
      const filter = params.get("filter") ?? undefined;
      const lastId =
        (req.headers["last-event-id"] as string | undefined) ??
        params.get("lastEventId") ??
        undefined;
      eventBus.addSSEClient(res, filter, lastId);
    }),
  });

  // ── Recent Events (JSON) ──────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/events/recent",
    handler: secure((_req, res) => {
      const params = urlParams(_req);
      const category =
        (params.get("category") as import("../services/event-bus.js").EventCategory | undefined) ??
        undefined;
      const limit = params.get("limit") ? Number(params.get("limit")) : 50;
      json(res, { status: "ok", data: eventBus.getRecentEvents(category, limit) });
    }),
  });

  // ── Equity History ────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/equity-history",
    handler: secure((_req, res) => {
      const params = urlParams(_req);
      const eh = resolveEquityHistory(params);
      const start = params.get("start") ?? undefined;
      const end = params.get("end") ?? undefined;
      const limit = params.get("limit") ? Number(params.get("limit")) : undefined;
      const mode = params.get("mode") ?? (eh === equityHistoryLive ? "live" : "paper");

      let data;
      if (start && end) {
        data = eh.getRange(start, end);
      } else if (limit) {
        data = eh.getRecent(limit);
      } else {
        data = eh.getAll();
      }

      json(res, { status: "ok", mode, data, count: data.length });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/equity-history/summary",
    handler: secure((_req, res) => {
      const eh = resolveEquityHistory(urlParams(_req));
      json(res, { status: "ok", data: eh.getSummary() });
    }),
  });

  // ── Benchmark comparison ──────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/benchmarks",
    handler: secure((_req, res) => {
      json(res, { status: "ok", data: benchmarkService.listBenchmarks() });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/benchmark/compare",
    handler: secure(async (_req, res) => {
      const params = urlParams(_req);
      const benchmarkId = params.get("benchmark") ?? "csi300";
      try {
        const eh = resolveEquityHistory(params);
        const equitySnaps = eh.getAll();
        const result = await benchmarkService.compare(equitySnaps, benchmarkId);
        json(res, { status: "ok", data: result });
      } catch (err) {
        json(res, { status: "error", message: (err as Error).message }, 400);
      }
    }),
  });

  // ── Portfolio ──────────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/portfolio",
    handler: secure(async (_req, res) => {
      const { handlePortfolioApi, handlePaperPortfolioOnly } = await import("./portfolio-api.js");
      const params = urlParams(_req);
      const mode = params.get("mode");
      if (mode === "paper") {
        // Force reading paper portfolio from file, regardless of active engine
        json(res, await handlePaperPortfolioOnly(config));
      } else if (mode === "live") {
        // Force reading from live broker only
        json(res, await handlePortfolioApi(config, getEngine, true));
      } else {
        json(res, await handlePortfolioApi(config, getEngine));
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/stocklist",
    handler: secure(async (req, res) => {
      const { handleGetStocklistApi, handleSetStocklistApi } = await import("./portfolio-api.js");
      if (req.method === "POST") {
        const body = await parseJsonBody(req);
        json(res, await handleSetStocklistApi(config, body));
      } else {
        json(res, await handleGetStocklistApi(config));
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/stocklist/history",
    handler: secure(async (_req, res) => {
      const { handleGetScreenerHistoryApi } = await import("./portfolio-api.js");
      json(res, await handleGetScreenerHistoryApi(config));
    }),
  });

  // ── Data Bridge (Mac↔Windows QMT K-line relay) ────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-request",
    handler: secure(async (req, res) => {
      const { handleDataBridgePostRequest, handleDataBridgePollRequest } =
        await import("./data-bridge-api.js");
      if (req.method === "POST") {
        const body = await parseJsonBody(req);
        json(res, handleDataBridgePostRequest(body));
      } else {
        const pending = handleDataBridgePollRequest();
        if (!pending) {
          res.writeHead(204);
          res.end();
        } else {
          json(res, pending);
        }
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-response",
    handler: secure(async (req, res) => {
      const { handleDataBridgePostResponse, handleDataBridgePollResponse } =
        await import("./data-bridge-api.js");
      if (req.method === "POST") {
        const body = await parseJsonBody(req);
        json(res, handleDataBridgePostResponse(body));
      } else {
        const requestId = urlParams(req).get("requestId") ?? "";
        const response = handleDataBridgePollResponse(requestId);
        if (!response) {
          res.writeHead(204);
          res.end();
        } else {
          json(res, response);
        }
      }
    }),
  });

  // ── Trading Bridge (Mac↔Windows QMT remote order execution) ───
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/trading-request",
    handler: secure(async (req, res) => {
      const { handleTradingPostRequest, handleTradingPollRequest } =
        await import("./trading-bridge-api.js");
      if (req.method === "POST") {
        const body = await parseJsonBody(req);
        json(res, handleTradingPostRequest(body));
      } else {
        const pending = handleTradingPollRequest();
        if (!pending) {
          res.writeHead(204);
          res.end();
        } else {
          json(res, pending);
        }
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/trading-response",
    handler: secure(async (req, res) => {
      const { handleTradingPostResponse, handleTradingPollResponse } =
        await import("./trading-bridge-api.js");
      if (req.method === "POST") {
        const body = await parseJsonBody(req);
        json(res, handleTradingPostResponse(body));
      } else {
        const requestId = urlParams(req).get("requestId") ?? "";
        const response = handleTradingPollResponse(requestId);
        if (!response) {
          res.writeHead(204);
          res.end();
        } else {
          json(res, response);
        }
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/trading-events",
    handler: secure(async (req, res) => {
      const { handleTradingEventsPost, handleTradingEventsPoll } =
        await import("./trading-bridge-api.js");
      if (req.method === "POST") {
        const body = await parseJsonBody(req);
        json(res, handleTradingEventsPost(body));
      } else {
        const afterSeq = Number(urlParams(req).get("afterSeq")) || 0;
        const since = Number(urlParams(req).get("since")) || 0;
        const result = handleTradingEventsPoll(afterSeq, since);
        json(res, result);
      }
    }),
  });

  // ── Live Engine Status ─────────────────────────────────────────

  // ── Data Warehouse ─────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-warehouse/stats",
    handler: secure(async (_req, res) => {
      const stats = await dataWarehouse.getStats();
      json(res, { status: "ok", data: stats });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-warehouse/klines",
    handler: secure(async (req, res) => {
      const params = urlParams(req);
      const symbol = params.get("symbol");
      const interval = params.get("interval") ?? "1d";
      const start = params.get("start");
      const end = params.get("end");
      if (!symbol || !start || !end) {
        json(res, { status: "error", message: "symbol, start, end required" }, 400);
        return;
      }
      try {
        const bars = await dataWarehouse.getKlines(symbol, interval as "1d", start, end);
        json(res, { status: "ok", data: bars, count: bars.length });
      } catch (err) {
        json(
          res,
          { status: "error", message: err instanceof Error ? err.message : String(err) },
          500,
        );
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-warehouse/warmup",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = (await parseJsonBody(req)) as {
        symbols?: string[];
        interval?: string;
        start?: string;
        end?: string;
      };
      if (!body.symbols?.length || !body.start || !body.end) {
        json(res, { status: "error", message: "symbols[], start, end required" }, 400);
        return;
      }
      const results = await dataWarehouse.warmup(
        body.symbols,
        (body.interval ?? "1d") as "1d",
        body.start,
        body.end,
      );
      json(res, { status: "ok", data: results });
    }),
  });

  // ── Data Quality Validation ────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-quality/validate",
    handler: secure(async (req, res) => {
      const params = urlParams(req);
      const symbol = params.get("symbol");
      const interval = params.get("interval") ?? "1d";
      if (!symbol) {
        json(res, { status: "error", message: "symbol required" }, 400);
        return;
      }
      const range = await quantStorage.getKlineDateRange(symbol, interval);
      if (!range) {
        json(res, { status: "ok", data: dataQualityValidator.validate([], symbol, interval) });
        return;
      }
      const rows = await quantStorage.getKlines(symbol, interval, range.min, range.max);
      json(res, { status: "ok", data: dataQualityValidator.validate(rows, symbol, interval) });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/live/status",
    handler: secure((_req, res) => {
      json(res, { status: "ok", data: getEngine().getStatus() });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/health",
    handler: secure((_req, res) => {
      json(res, { status: "ok", data: getEngine().getConnectionHealth() });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/staleness",
    handler: secure((_req, res) => {
      json(res, { status: "ok", data: getEngine().getDataStalenessReport() });
    }),
  });

  // ── Alert Store API ────────────────────────────────────────────
  registerAlertRoutes(deps);

  // ── Backtest History API ───────────────────────────────────────
  registerBacktestRoutes(deps);

  // ── Orders / Trades / PnL ──────────────────────────────────────
  registerTradingRoutes(deps);

  // ── Signal Webhook ─────────────────────────────────────────────
  registerSignalWebhook(deps);

  // ── Engine Control ─────────────────────────────────────────────
  registerEngineRoutes(deps);

  // ── Live Strategies ────────────────────────────────────────────
  registerStrategyRoutes(deps);

  // ── Strategy Templates & User Strategies ───────────────────────
  registerTemplateRoutes(deps);

  // ── Strategy Ecosystem (comparison, ranking, import/export) ────
  registerStrategyEcosystemRoutes(deps);

  // ── Signals Feed ───────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/signals",
    handler: secure(async (req, res) => {
      const { handleSignalsApi } = await import("./portfolio-api.js");
      const params = urlParams(req);
      json(
        res,
        await handleSignalsApi(config, {
          strategyId: params.get("strategyId") ?? undefined,
          symbol: params.get("symbol") ?? undefined,
          limit: parseInt(params.get("limit") ?? "50", 10),
        }),
      );
    }),
  });

  // ── Pending Confirmations ──────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/confirmations",
    handler: secure(async (_req, res) => {
      const engine = getEngine();
      if (!engine.orderManager) {
        json(res, { status: "ok", data: [] });
        return;
      }
      const items = await engine.orderManager.confirmationManager.listPending();
      json(res, { status: "ok", data: items });
    }),
  });

  // ── Price Alert CRUD ──────────────────────────────────────────
  registerPriceAlertRoutes(deps);

  // ── Scheduled Task API ────────────────────────────────────────
  registerScheduledTaskRoutes(deps);

  // ── Market Data API ───────────────────────────────────────────
  registerMarketDataRoutes(deps);

  // ── Sector / Fund-Flow / Dragon-Tiger APIs ────────────────────
  registerPythonToolRoutes(deps);

  // ── Data Export API (FR-7) ────────────────────────────────────
  registerDataExportRoutes(deps);
}

// ── Alert sub-routes ─────────────────────────────────────────────

function registerAlertRoutes({ api, alertStore, secure }: RouteDeps): void {
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/alerts",
    handler: secure(async (req, res) => {
      if (req.method === "GET") {
        const p = urlParams(req);
        const q: Record<string, unknown> = {};
        const severity = p.get("severity");
        if (severity) q.severity = severity.split(",");
        const source = p.get("source");
        if (source) q.source = source.split(",");
        const after = p.get("after");
        if (after) q.after = after;
        const before = p.get("before");
        if (before) q.before = before;
        const acked = p.get("acknowledged");
        if (acked !== null) q.acknowledged = acked === "true";
        const limit = p.get("limit");
        if (limit) q.limit = parseInt(limit, 10);
        const offset = p.get("offset");
        if (offset) q.offset = parseInt(offset, 10);

        json(res, {
          status: "ok",
          data: alertStore.query(q as Parameters<typeof alertStore.query>[0]),
        });
      } else if (req.method === "DELETE") {
        alertStore.clear();
        json(res, { status: "ok" });
      } else {
        methodNotAllowed(res);
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/alerts/summary",
    handler: secure((_req, res) => {
      json(res, { status: "ok", data: alertStore.getSummary() });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/alerts/acknowledge",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = await parseJsonBody<Record<string, unknown>>(req);
      if (body.all === true) {
        json(res, { status: "ok", acknowledged: alertStore.acknowledgeAll() });
      } else if (typeof body.id === "string") {
        const ok = alertStore.acknowledge(body.id);
        json(res, { status: ok ? "ok" : "error", message: ok ? undefined : "Alert not found" });
      } else {
        json(res, { status: "error", message: "Provide { id: string } or { all: true }" });
      }
    }),
  });
}

// ── Backtest sub-routes ──────────────────────────────────────────

function registerBacktestRoutes({ api, config, backtestStore, secure }: RouteDeps): void {
  const requireDataDir = (res: ServerResponse): boolean => {
    if (!config.dataDir) {
      json(res, { status: "error", message: "dataDir not configured" }, 400);
      return false;
    }
    return true;
  };

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/backtests",
    handler: secure(async (req, res) => {
      if (!requireDataDir(res)) return;
      if (req.method === "GET") {
        const p = urlParams(req);
        const q: Record<string, unknown> = {};
        for (const key of ["strategyId", "symbol", "engine", "tag"] as const) {
          const v = p.get(key);
          if (v) q[key] = v;
        }
        const starred = p.get("starred");
        if (starred !== null) q.starred = starred === "true";
        for (const key of ["after", "before", "sortBy", "sortDir"] as const) {
          const v = p.get(key);
          if (v) q[key] = v;
        }
        for (const key of ["limit", "offset"] as const) {
          const v = p.get(key);
          if (v) q[key] = parseInt(v, 10);
        }
        json(res, {
          status: "ok",
          data: backtestStore.query(q as Parameters<typeof backtestStore.query>[0]),
          total: backtestStore.size,
        });
      } else if (req.method === "DELETE") {
        const id = urlParams(req).get("id");
        if (!id) {
          json(res, { status: "error", message: "缺少 id 参数" }, 400);
          return;
        }
        const ok = await backtestStore.delete(id);
        json(res, { status: ok ? "ok" : "error", message: ok ? undefined : "未找到" });
      } else {
        methodNotAllowed(res);
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/backtests/detail",
    handler: secure(async (_req, res) => {
      if (!requireDataDir(res)) return;
      const id = urlParams(_req).get("id");
      if (!id) {
        json(res, { status: "error", message: "缺少 id 参数" }, 400);
        return;
      }
      const record = backtestStore.getFullResult(id);
      if (!record) {
        json(res, { status: "error", message: "未找到" }, 404);
        return;
      }
      json(res, { status: "ok", data: record });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/backtests/compare",
    handler: secure(async (_req, res) => {
      if (!requireDataDir(res)) return;
      const idsParam = urlParams(_req).get("ids");
      if (!idsParam) {
        json(res, { status: "error", message: "缺少 ids 参数 (逗号分隔)" }, 400);
        return;
      }
      const ids = idsParam
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const comparison = backtestStore.compare(ids);
      if (!comparison) {
        json(res, { status: "error", message: "需要至少 2 个有效 ID" }, 400);
        return;
      }
      json(res, { status: "ok", data: comparison });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/backtests/summary",
    handler: secure(async (_req, res) => {
      if (!requireDataDir(res)) return;
      const strategyId = urlParams(_req).get("strategyId") ?? undefined;
      json(res, { status: "ok", data: backtestStore.getPerformanceSummary(strategyId) });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/backtests/star",
    handler: secure(async (req, res) => {
      if (!requireDataDir(res)) return;
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = await parseJsonBody<Record<string, unknown>>(req);
      const id = body.id as string;
      if (!id) {
        json(res, { status: "error", message: "缺少 id" }, 400);
        return;
      }
      const ok = await backtestStore.toggleStar(id);
      json(res, { status: ok ? "ok" : "error", message: ok ? undefined : "未找到" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/backtests/tags",
    handler: secure(async (req, res) => {
      if (!requireDataDir(res)) return;
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = await parseJsonBody<Record<string, unknown>>(req);
      const id = body.id as string;
      const tags = body.tags as string[];
      if (!id || !Array.isArray(tags)) {
        json(res, { status: "error", message: "需要 id 和 tags[]" }, 400);
        return;
      }
      const ok = await backtestStore.setTags(id, tags);
      json(res, { status: ok ? "ok" : "error", message: ok ? undefined : "未找到" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/backtests/notes",
    handler: secure(async (req, res) => {
      if (!requireDataDir(res)) return;
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = await parseJsonBody<Record<string, unknown>>(req);
      const id = body.id as string;
      const notes = body.notes as string;
      if (!id || typeof notes !== "string") {
        json(res, { status: "error", message: "需要 id 和 notes" }, 400);
        return;
      }
      const ok = await backtestStore.setNotes(id, notes);
      json(res, { status: ok ? "ok" : "error", message: ok ? undefined : "未找到" });
    }),
  });
}

// ── Trading sub-routes ───────────────────────────────────────────

function registerTradingRoutes({ api, config, getEngine, secure }: RouteDeps): void {
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/orders",
    handler: secure(async (req, res) => {
      const {
        handlePlaceOrderApi,
        handleCancelOrderApi,
        handleOrdersApi,
        readOrdersFromFile,
      } = await import("./portfolio-api.js");

      if (req.method === "POST") {
        const body = await parseJsonBody<Record<string, unknown>>(req);
        json(res, await handlePlaceOrderApi(getEngine, body));
        return;
      }

      if (req.method === "DELETE") {
        const orderId = urlParams(req).get("id") ?? "";
        if (!orderId) {
          json(res, { status: "error", message: "缺少 id 参数" });
          return;
        }
        json(res, await handleCancelOrderApi(getEngine, orderId));
        return;
      }

      // GET — list orders (mode-aware)
      const params = urlParams(req);
      const mode = params.get("mode") as "paper" | "live" | null;
      const statusFilter = params.get("status") ?? undefined;

      // If mode specified and differs from active engine's broker, read from file
      const engine = getEngine();
      const engineMode = engine?.broker?.brokerType === "paper" ? "paper" : "live";
      if (mode && mode !== engineMode) {
        json(res, await readOrdersFromFile(config, mode, statusFilter));
        return;
      }

      json(res, await handleOrdersApi(getEngine, statusFilter));
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/trades",
    handler: secure(async (_req, res) => {
      const params = urlParams(_req);
      const mode = params.get("mode") as "paper" | "live" | null;
      const engine = getEngine();
      const engineMode = engine?.broker?.brokerType === "paper" ? "paper" : "live";

      if (mode && mode !== engineMode) {
        // Read from mode-specific file
        const { readTradesFromFile } = await import("./portfolio-api.js");
        json(res, await readTradesFromFile(config, mode));
        return;
      }
      const trades = engine.orderManager ? await engine.orderManager.getTrades() : [];
      json(res, { status: "ok", data: trades });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/pnl",
    handler: secure(async (_req, res) => {
      const params = urlParams(_req);
      const mode = params.get("mode") as "paper" | "live" | null;
      const engine = getEngine();
      const engineMode = engine?.broker?.brokerType === "paper" ? "paper" : "live";

      if (mode && mode !== engineMode) {
        // Compute PnL from mode-specific trades file
        const { readDailyPnlFromFile } = await import("./portfolio-api.js");
        json(res, await readDailyPnlFromFile(config, mode));
        return;
      }

      if (!engine.orderManager) {
        json(res, { status: "ok", data: null });
        return;
      }
      const pnl = await engine.orderManager.getDailyPnl();
      json(res, {
        status: "ok",
        data: { date: new Date().toISOString().slice(0, 10), netPnl: pnl },
      });
    }),
  });
}

// ── Signal Webhook ───────────────────────────────────────────────

function registerSignalWebhook({ api, config, securePublic }: RouteDeps): void {
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/signal",
    handler: securePublic(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { status: "error", message: "POST only" }, 405);
        return;
      }
      // Optional shared-secret authentication (timing-safe comparison)
      const secret = config.signalWebhook?.secret;
      if (secret) {
        const provided = req.headers["x-signal-secret"];
        if (typeof provided !== "string" || !timingSafeCompareStr(secret, provided)) {
          json(res, { status: "error", message: "Unauthorized" }, 401);
          return;
        }
      }
      try {
        const { appendSignals } = await import("../services/signal-monitor.js");
        const signal = await parseJsonBody<TradingSignal>(req);
        if (!signal.symbol || !signal.direction || signal.price == null) {
          json(res, { status: "error", message: "缺少必填字段 symbol/direction/price" }, 400);
          return;
        }
        const validDirections = ["buy", "sell", "short", "cover"];
        if (!validDirections.includes(signal.direction)) {
          json(
            res,
            {
              status: "error",
              message: `direction "${signal.direction}" 无效，有效值: ${validDirections.join(", ")}`,
            },
            400,
          );
          return;
        }
        if (
          typeof signal.price !== "number" ||
          !Number.isFinite(signal.price) ||
          signal.price <= 0
        ) {
          json(res, { status: "error", message: "price 必须为正数" }, 400);
          return;
        }
        signal.timestamp = signal.timestamp ?? Date.now();
        await appendSignals([signal], config);
        api.logger.info(`SignalWebhook: ${signal.direction} ${signal.symbol} @ ${signal.price}`);
        json(res, { status: "ok", signal });
      } catch (e) {
        json(res, { status: "error", message: (e as Error).message }, 400);
      }
    }),
  });
}

// ── Engine Control ───────────────────────────────────────────────

function registerEngineRoutes({ api, getEngine, secure }: RouteDeps): void {
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/engine/start",
    handler: secure(async (_req, res) => {
      const engine = getEngine();
      if (engine.getStatus() === "running") {
        json(res, { status: "ok", message: "已在运行" });
        return;
      }
      void engine.start();
      json(res, { status: "ok", message: "引擎启动中" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/engine/stop",
    handler: secure(async (_req, res) => {
      await getEngine().stop();
      json(res, { status: "ok", message: "引擎已停止" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/engine/pause",
    handler: secure((_req, res) => {
      getEngine().pause();
      json(res, { status: "ok", message: "引擎已暂停" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/engine/resume",
    handler: secure((_req, res) => {
      getEngine().resume();
      json(res, { status: "ok", message: "引擎已恢复" });
    }),
  });
}

// ── Strategy CRUD ────────────────────────────────────────────────

function registerStrategyRoutes({ api, getEngine, secure }: RouteDeps): void {
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/strategies",
    handler: secure(async (req, res) => {
      const engine = getEngine();
      if (req.method === "POST") {
        const body = await parseJsonBody<Record<string, unknown>>(req);
        const strategyId = body.strategyId as string;
        const symbols = (body.symbols as string[]) ?? [];
        if (!strategyId || symbols.length === 0) {
          json(res, { status: "error", message: "strategyId 和 symbols 必填" }, 400);
          return;
        }
        try {
          const entry = await engine.addStrategy(
            strategyId,
            symbols,
            body.parameters as Record<string, unknown> | undefined,
          );
          json(res, { status: "ok", data: entry });
        } catch (e) {
          json(res, { status: "error", message: (e as Error).message }, 500);
        }
        return;
      }
      // GET — list all
      json(res, { status: "ok", data: engine.strategyRegistry.list() });
    }),
  });
}

// ── Strategy Templates & User Strategies ─────────────────────────

function registerTemplateRoutes({ api, userStrategyStore, secure }: RouteDeps): void {
  // GET /quant/api/templates — list all built-in templates
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/templates",
    handler: secure(async (_req, res) => {
      const { STRATEGY_TEMPLATES } = await import("../services/strategy-template.js");
      json(res, { status: "ok", data: STRATEGY_TEMPLATES });
    }),
  });

  // /quant/api/user-strategies — CRUD for user strategy configs
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/user-strategies",
    handler: secure(async (req, res) => {
      const params = urlParams(req);

      if (req.method === "POST") {
        const body = await parseJsonBody<Record<string, unknown>>(req);
        const templateId = body.templateId as string | undefined;

        if (templateId) {
          const result = await userStrategyStore.createFromTemplate(templateId, {
            name: body.name as string | undefined,
            symbols: body.symbols as string[] | undefined,
            paramOverrides: body.parameters as Record<string, unknown> | undefined,
          });
          if (result.errors) {
            json(res, { status: "error", errors: result.errors }, 400);
            return;
          }
          json(res, { status: "ok", data: result.strategy }, 201);
          return;
        }

        const result = await userStrategyStore.create({
          name: (body.name as string) ?? "Unnamed",
          strategyId: body.strategyId as string,
          parameters: body.parameters as Record<string, unknown> | undefined,
          symbols: body.symbols as string[] | undefined,
          description: body.description as string | undefined,
          tags: body.tags as string[] | undefined,
        });
        if (result.errors) {
          json(res, { status: "error", errors: result.errors }, 400);
          return;
        }
        json(res, { status: "ok", data: result.strategy }, 201);
        return;
      }

      // GET — list with optional filters
      const tag = params.get("tag") ?? undefined;
      const strategyId = params.get("strategyId") ?? undefined;
      const starred = params.has("starred") ? params.get("starred") === "true" : undefined;
      json(res, { status: "ok", data: userStrategyStore.list({ tag, strategyId, starred }) });
    }),
  });

  // /quant/api/user-strategies/:id — get/update/delete
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/user-strategy",
    handler: secure(async (req, res) => {
      const params = urlParams(req);
      const id = params.get("id");
      if (!id) {
        json(res, { status: "error", message: "Missing ?id= parameter" }, 400);
        return;
      }

      if (req.method === "DELETE") {
        const deleted = await userStrategyStore.delete(id);
        if (!deleted) {
          json(res, { status: "error", message: "Not found" }, 404);
          return;
        }
        json(res, { status: "ok", message: "Deleted" });
        return;
      }

      if (req.method === "PUT" || req.method === "PATCH") {
        const body = await parseJsonBody<Record<string, unknown>>(req);
        const result = await userStrategyStore.update(id, {
          name: body.name as string | undefined,
          description: body.description as string | undefined,
          parameters: body.parameters as Record<string, unknown> | undefined,
          symbols: body.symbols as string[] | undefined,
          tags: body.tags as string[] | undefined,
          starred: body.starred as boolean | undefined,
        });
        if (result.errors) {
          json(res, { status: "error", errors: result.errors }, 400);
          return;
        }
        json(res, { status: "ok", data: result.strategy });
        return;
      }

      // GET
      const strategy = userStrategyStore.get(id);
      if (!strategy) {
        json(res, { status: "error", message: "Not found" }, 404);
        return;
      }
      json(res, { status: "ok", data: strategy });
    }),
  });
}

// ── Strategy Ecosystem (comparison, ranking, import/export) ──────

function registerStrategyEcosystemRoutes({
  api,
  config,
  backtestStore,
  userStrategyStore,
  secure,
}: RouteDeps): void {
  // POST /quant/api/strategy-compare — run backtests and compare
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/strategy-compare",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = await parseJsonBody<Record<string, unknown>>(req);
      const { StrategyComparisonService } = await import("../services/strategy-comparison.js");
      const svc = new StrategyComparisonService(config, backtestStore);
      const result = svc.buildComparison(
        ((body.results as Array<Record<string, unknown>>) ?? []).map((r) => ({
          strategyId: r.strategyId as string,
          result: r.result as import("../types.js").BacktestResult,
        })),
      );
      json(res, { status: "ok", data: result });
    }),
  });

  // GET /quant/api/strategy-ranking — leaderboard from backtest store
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/strategy-ranking",
    handler: secure(async (req, res) => {
      const params = urlParams(req);
      const { PerformanceRanking } = await import("../services/performance-ranking.js");
      const ranking = new PerformanceRanking();
      const entries = backtestStore
        .query({})
        .map((entry) => {
          const full = backtestStore.getFullResult(entry.id);
          return full ? { id: entry.id, strategyId: entry.strategyId, result: full.result } : null;
        })
        .filter(Boolean) as Array<{
        id: string;
        strategyId: string;
        result: import("../types.js").BacktestResult;
      }>;

      const result = ranking.rank(entries, {
        sortBy:
          (params.get("sortBy") as
            | import("../services/strategy-comparison.js").ComparisonMetric
            | "composite") ?? "composite",
        direction: (params.get("direction") as "asc" | "desc") ?? "desc",
        limit: parseInt(params.get("limit") ?? "50"),
        offset: parseInt(params.get("offset") ?? "0"),
        strategyId: params.get("strategyId") ?? undefined,
        minTrades: params.has("minTrades") ? parseInt(params.get("minTrades")!) : undefined,
      });
      json(res, { status: "ok", data: result });
    }),
  });

  // POST /quant/api/strategy-export — export user strategies
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/strategy-export",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = await parseJsonBody<Record<string, unknown>>(req);
      const ids = (body.ids as string[]) ?? [];
      const { exportStrategies } = await import("../services/strategy-io.js");

      const strategies =
        ids.length > 0
          ? (ids
              .map((id) => userStrategyStore.get(id))
              .filter(Boolean) as import("../services/strategy-template.js").UserStrategy[])
          : userStrategyStore.list();

      const pkg = exportStrategies(strategies, {
        exportedBy: body.exportedBy as string | undefined,
      });
      json(res, { status: "ok", data: pkg });
    }),
  });

  // POST /quant/api/strategy-import — import strategies from package
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/strategy-import",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = await parseJsonBody<Record<string, unknown>>(req);
      const { validatePackage, importStrategies } = await import("../services/strategy-io.js");

      const pkg = body.package as import("../services/strategy-io.js").StrategyPackage;
      if (!pkg) {
        json(res, { status: "error", message: "Missing package field" }, 400);
        return;
      }

      const validation = validatePackage(pkg);
      if (!validation.valid) {
        json(
          res,
          { status: "error", errors: validation.errors, warnings: validation.warnings },
          400,
        );
        return;
      }

      const result = await importStrategies(userStrategyStore, pkg, {
        skipDuplicates: (body.skipDuplicates as boolean) ?? true,
        symbolOverride: body.symbolOverride as string[] | undefined,
        addTag: body.addTag as string | undefined,
      });
      json(res, {
        status: "ok",
        data: result,
        warnings: validation.warnings,
      });
    }),
  });
}

// ── Price Alert CRUD routes ──────────────────────────────────────

function registerPriceAlertRoutes({ api, priceAlertService, secure }: RouteDeps): void {
  // GET /quant/api/price-alerts — list all price alerts
  // POST /quant/api/price-alerts — add a new alert
  // DELETE /quant/api/price-alerts?id=xxx — remove an alert
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/price-alerts",
    handler: secure(async (req, res) => {
      if (req.method === "GET") {
        const p = urlParams(req);
        const activeOnly = p.get("active") === "true";
        json(res, {
          status: "ok",
          data: priceAlertService.listAlerts(activeOnly),
          activeCount: priceAlertService.activeCount,
        });
        return;
      }

      if (req.method === "POST") {
        const body = await parseJsonBody<Record<string, unknown>>(req);
        const symbol = body.symbol as string;
        const type = body.type as string;
        const threshold = body.threshold as number;
        if (!symbol || !type || threshold == null) {
          json(res, { status: "error", message: "需要 symbol, type, threshold" }, 400);
          return;
        }
        try {
          const id = priceAlertService.addAlert({
            symbol,
            type: type as import("../services/price-alert.js").AlertConditionType,
            threshold,
            description: body.description as string | undefined,
            repeating: (body.repeating as boolean) ?? false,
          });
          json(res, { status: "ok", id }, 201);
        } catch (err) {
          json(res, { status: "error", message: (err as Error).message }, 400);
        }
        return;
      }

      if (req.method === "DELETE") {
        const id = urlParams(req).get("id");
        if (!id) {
          json(res, { status: "error", message: "缺少 ?id= 参数" }, 400);
          return;
        }
        const ok = priceAlertService.removeAlert(id);
        json(res, { status: ok ? "ok" : "error", message: ok ? undefined : "未找到" });
        return;
      }

      methodNotAllowed(res);
    }),
  });
}

// ── Market Data routes ───────────────────────────────────────────

function registerMarketDataRoutes({ api, config, secure }: RouteDeps): void {
  const provider = createDataProvider(config);

  // GET /quant/api/quote?symbol=600519[&market=cn_stock]
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/quote",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") return methodNotAllowed(res);
      const p = urlParams(req);
      const symbol = p.get("symbol");
      if (!symbol) {
        json(res, { status: "error", message: "缺少 ?symbol= 参数" }, 400);
        return;
      }
      const market = p.get("market") ?? detectMarketFromSymbol(symbol);
      try {
        const quote = await provider.getQuote(symbol, market as never);
        if (!quote) {
          json(res, { status: "error", message: `无法获取 ${symbol} 的行情` }, 404);
          return;
        }
        json(res, { status: "ok", data: quote });
      } catch (err) {
        json(res, { status: "error", message: (err as Error).message }, 500);
      }
    }),
  });

  // GET /quant/api/klines?symbol=600519&interval=1d&startDate=2025-01-01&endDate=2026-03-03
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/klines",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") return methodNotAllowed(res);
      const p = urlParams(req);
      const symbol = p.get("symbol");
      if (!symbol) {
        json(res, { status: "error", message: "缺少 ?symbol= 参数" }, 400);
        return;
      }
      const market = p.get("market") ?? detectMarketFromSymbol(symbol);
      const interval = p.get("interval") ?? "1d";
      const endDate = p.get("endDate") ?? new Date().toISOString().slice(0, 10);
      const startDate =
        p.get("startDate") ??
        (() => {
          const d = new Date();
          d.setMonth(d.getMonth() - 6);
          return d.toISOString().slice(0, 10);
        })();
      try {
        const bars = await provider.getKlines(
          symbol,
          interval as never,
          startDate,
          endDate,
          market as never,
        );
        json(res, { status: "ok", symbol, interval, count: bars.length, data: bars });
      } catch (err) {
        json(res, { status: "error", message: (err as Error).message }, 500);
      }
    }),
  });
}

// ── Python-backed tool routes ────────────────────────────────────

function registerPythonToolRoutes({ api, config, secure }: RouteDeps): void {
  // GET /quant/api/sectors?limit=10
  // GET /quant/api/sectors?name=半导体
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/sectors",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") return methodNotAllowed(res);
      const p = urlParams(req);
      const name = p.get("name") ?? p.get("sector");
      const limit = parseInt(p.get("limit") ?? "10", 10);
      try {
        if (name) {
          const result = await runPythonScript(
            "engines/sector_analysis.py",
            { action: "detail", sector: name },
            config,
          );
          if (!result.ok) {
            json(res, { status: "error", message: result.error ?? "板块数据获取失败" }, 500);
            return;
          }
          json(res, { status: "ok", data: result.data });
        } else {
          const result = await runPythonScript(
            "engines/sector_analysis.py",
            { action: "ranking", limit },
            config,
          );
          if (!result.ok) {
            json(res, { status: "error", message: result.error ?? "板块数据获取失败" }, 500);
            return;
          }
          json(res, { status: "ok", data: result.data });
        }
      } catch (err) {
        json(res, { status: "error", message: (err as Error).message }, 500);
      }
    }),
  });

  // GET /quant/api/fund-flow?symbol=600519[&limit=10]
  // GET /quant/api/fund-flow (ranking)
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/fund-flow",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") return methodNotAllowed(res);
      const p = urlParams(req);
      const symbol = p.get("symbol");
      const limit = parseInt(p.get("limit") ?? "20", 10);
      try {
        const action = symbol ? "stock" : "ranking";
        const result = await runPythonScript(
          "engines/fund_flow.py",
          { action, ...(symbol ? { symbol } : { limit }) },
          config,
        );
        if (!result.ok) {
          json(res, { status: "error", message: result.error ?? "资金数据获取失败" }, 500);
          return;
        }
        json(res, { status: "ok", data: result.data });
      } catch (err) {
        json(res, { status: "error", message: (err as Error).message }, 500);
      }
    }),
  });

  // GET /quant/api/dragon-tiger[?symbol=600519]
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/dragon-tiger",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") return methodNotAllowed(res);
      const p = urlParams(req);
      const symbol = p.get("symbol");
      try {
        const action = symbol ? "stock" : "daily";
        const result = await runPythonScript(
          "engines/dragon_tiger.py",
          { action, ...(symbol ? { symbol } : {}) },
          config,
        );
        if (!result.ok) {
          json(res, { status: "error", message: result.error ?? "龙虎榜数据获取失败" }, 500);
          return;
        }
        json(res, { status: "ok", data: result.data });
      } catch (err) {
        json(res, { status: "error", message: (err as Error).message }, 500);
      }
    }),
  });
}

// ── Scheduled Task routes ────────────────────────────────────────

function registerScheduledTaskRoutes({ api, taskScheduler, secure }: RouteDeps): void {
  // GET /quant/api/scheduled-tasks — list all tasks
  // DELETE /quant/api/scheduled-tasks?id=xxx — cancel a task
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/scheduled-tasks",
    handler: secure(async (req, res) => {
      if (req.method === "GET") {
        const p = urlParams(req);
        const status = p.get("status") as
          | "pending"
          | "completed"
          | "failed"
          | "cancelled"
          | undefined;
        const tasks = taskScheduler.list(status ?? undefined).map((t) => ({
          id: t.id,
          description: t.description,
          status: t.status,
          scheduledAt: t.scheduledAt,
          createdAt: t.createdAt,
          ...(t.intervalMs ? { repeatEveryMinutes: t.intervalMs / 60_000 } : {}),
          ...(t.lastError ? { lastError: t.lastError } : {}),
          ...(t.meta ? { meta: t.meta } : {}),
        }));
        json(res, { status: "ok", data: tasks, pending: taskScheduler.pendingCount });
        return;
      }

      if (req.method === "DELETE") {
        const id = urlParams(req).get("id");
        if (!id) {
          json(res, { status: "error", message: "缺少 ?id= 参数" }, 400);
          return;
        }
        const ok = taskScheduler.cancel(id);
        json(res, {
          status: ok ? "ok" : "error",
          message: ok ? undefined : "无法取消（可能已完成或不存在）",
        });
        return;
      }

      methodNotAllowed(res);
    }),
  });
}

// ── Data Export sub-routes (FR-7) ───────────────────────────────

function registerDataExportRoutes({ api, config, secure, quantStorage, equityHistory, backtestStore, tradeJournal, scanHistoryStore, favoritesStore, getEngine }: RouteDeps): void {
  // GET /quant/api/data-export/list — list exported files
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-export/list",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") {
        methodNotAllowed(res);
        return;
      }
      const { existsSync, readdirSync, statSync } = await import("node:fs");
      const pathMod = await import("node:path");
      const { homedir } = await import("node:os");
      const base = config.dataDir ?? pathMod.join(homedir(), ".openclaw", "quant-trading");
      const dir = pathMod.join(base, "exports");
      if (!existsSync(dir)) {
        json(res, { status: "ok", files: [], total: 0 });
        return;
      }
      const files = readdirSync(dir)
        .filter((f) => f.endsWith(".csv") || f.endsWith(".json"))
        .map((f) => {
          const st = statSync(pathMod.join(dir, f));
          return { filename: f, size: st.size, createdAt: st.mtime.toISOString() };
        })
        .sort((a, b) => b.createdAt.localeCompare(a.createdAt));
      json(res, { status: "ok", files, total: files.length, dir });
    }),
  });

  // GET /quant/api/data-export/download?file=xxx — download an exported file
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-export/download",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") {
        methodNotAllowed(res);
        return;
      }
      const { existsSync, readFileSync } = await import("node:fs");
      const pathMod = await import("node:path");
      const { homedir } = await import("node:os");
      const filename = urlParams(req).get("file");
      if (!filename) {
        json(res, { status: "error", message: "缺少 ?file= 参数" }, 400);
        return;
      }
      // Sanitize filename to prevent path traversal
      const safe = pathMod.basename(filename);
      const base = config.dataDir ?? pathMod.join(homedir(), ".openclaw", "quant-trading");
      const filePath = pathMod.join(base, "exports", safe);
      if (!existsSync(filePath)) {
        json(res, { status: "error", message: `文件不存在: ${safe}` }, 404);
        return;
      }
      const content = readFileSync(filePath);
      const isJson = safe.endsWith(".json");
      res.writeHead(200, {
        "Content-Type": isJson ? "application/json; charset=utf-8" : "text/csv; charset=utf-8",
        "Content-Disposition": `attachment; filename="${encodeURIComponent(safe)}"`,
        "Content-Length": content.length,
      });
      res.end(content);
    }),
  });

  // DELETE /quant/api/data-export/delete?file=xxx — delete an exported file
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-export/delete",
    handler: secure(async (req, res) => {
      if (req.method !== "DELETE" && req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const { existsSync, unlinkSync } = await import("node:fs");
      const pathMod = await import("node:path");
      const { homedir } = await import("node:os");

      let filename: string | undefined;
      if (req.method === "DELETE") {
        filename = urlParams(req).get("file") ?? undefined;
      } else {
        const body = await parseJsonBody<Record<string, unknown>>(req);
        filename = body.filename as string | undefined;
      }

      if (!filename) {
        json(res, { status: "error", message: "缺少文件名参数" }, 400);
        return;
      }

      const safe = pathMod.basename(filename);
      const base = config.dataDir ?? pathMod.join(homedir(), ".openclaw", "quant-trading");
      const filePath = pathMod.join(base, "exports", safe);
      if (!existsSync(filePath)) {
        json(res, { status: "error", message: `文件不存在: ${safe}` }, 404);
        return;
      }
      unlinkSync(filePath);
      json(res, { status: "ok", message: `已删除: ${safe}` });
    }),
  });

  // POST /quant/api/data-export/clean — delete all exported files
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-export/clean",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const fsMod = await import("node:fs");
      const pathMod = await import("node:path");
      const { homedir } = await import("node:os");
      const base = config.dataDir ?? pathMod.join(homedir(), ".openclaw", "quant-trading");
      const dir = pathMod.join(base, "exports");
      try {
        const files = fsMod.readdirSync(dir).filter((f) => f.endsWith(".csv") || f.endsWith(".json"));
        for (const f of files) {
          fsMod.unlinkSync(pathMod.join(dir, f));
        }
        json(res, { status: "ok", deleted: files.length, message: `已清理 ${files.length} 个导出文件` });
      } catch {
        json(res, { status: "ok", deleted: 0, message: "导出目录为空或不存在" });
      }
    }),
  });

  // POST /quant/api/data-export/export — trigger a data export
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/api/data-export/export",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        methodNotAllowed(res);
        return;
      }
      const body = await parseJsonBody<Record<string, unknown>>(req);
      const action = body.action as string | undefined;
      const format = ((body.format as string) ?? "csv") === "json" ? "json" : "csv";
      const symbol = body.symbol as string | undefined;
      const startDate = body.startDate as string | undefined;
      const endDate = body.endDate as string | undefined;
      const limit = (body.limit as number) ?? 10000;

      if (!action) {
        json(res, { status: "error", message: "缺少 action 参数" }, 400);
        return;
      }

      const validActions = ["portfolio", "trades", "signals", "orders", "equity", "journal", "klines", "backtest", "screen", "watchlist", "batch"];
      if (!validActions.includes(action)) {
        json(res, { status: "error", message: `不支持的 action: ${action}，可用: ${validActions.join(", ")}` }, 400);
        return;
      }

      // Batch export: export multiple dataset types in one request
      if (action === "batch") {
        const batchTypes = (body.batchTypes as string[] | undefined) ?? ["portfolio", "trades", "signals"];
        const validBatchTypes = ["portfolio", "trades", "signals", "orders", "equity", "journal", "klines", "backtest", "screen", "watchlist"];
        const invalidTypes = batchTypes.filter((t: string) => !validBatchTypes.includes(t));
        if (invalidTypes.length > 0) {
          json(res, { status: "error", message: `不支持的批量导出类型: ${invalidTypes.join(", ")}` }, 400);
          return;
        }
        // Re-use the single-export endpoint by calling the tool logic for each type
        const results: Array<{ dataset: string; rows: number; filename: string }> = [];
        for (const batchAction of batchTypes) {
          const batchBody: Record<string, unknown> = { ...body, action: batchAction };
          try {
            // Inline single-export logic for each batch type
            const batchReq = new Promise<{ dataset: string; rows: number; filename: string }>((resolve, reject) => {
              void (async () => {
                try {
                  const fsMod = await import("node:fs");
                  const pathMod = await import("node:path");
                  const { homedir } = await import("node:os");
                  const base2 = config.dataDir ?? pathMod.join(homedir(), ".openclaw", "quant-trading");
                  const dir2 = pathMod.join(base2, "exports");
                  fsMod.mkdirSync(dir2, { recursive: true });

                  let bRows: Record<string, unknown>[] = [];
                  let bHeaders: string[] = [];
                  let bDataset = batchAction;

                  switch (batchAction) {
                    case "portfolio": {
                      bHeaders = ["source", "symbol", "name", "quantity", "avgCost", "currentPrice", "marketValue", "unrealizedPnl", "unrealizedPnlPct", "weight"];
                      const portfolioDir = config.dataDir ?? pathMod.join(homedir(), ".openclaw", "quant-trading");
                      for (const name of ["portfolio-paper.json", "portfolio.json"]) {
                        try {
                          const raw = fsMod.readFileSync(pathMod.join(portfolioDir, name), "utf-8");
                          const paper = JSON.parse(raw) as { totalValue?: number; positions: Array<Record<string, unknown>> };
                          for (const pos of paper.positions) {
                            const qty = (pos.quantity as number) ?? 0;
                            const cost = (pos.avgCost as number) ?? 0;
                            const cur = (pos.currentPrice as number) ?? cost;
                            const mv = cur * qty;
                            const pnl = (cur - cost) * qty;
                            const pnlPct = cost > 0 ? (pnl / (cost * qty)) * 100 : 0;
                            bRows.push({ source: "paper", symbol: pos.symbol ?? "", name: pos.name ?? "", quantity: qty, avgCost: cost, currentPrice: cur, marketValue: mv, unrealizedPnl: pnl, unrealizedPnlPct: Number(pnlPct.toFixed(2)), weight: (paper.totalValue ?? 0) > 0 ? Number(((mv / (paper.totalValue ?? 1)) * 100).toFixed(2)) : 0 });
                          }
                          break;
                        } catch { continue; }
                      }
                      break;
                    }
                    case "trades":
                      bRows = (await quantStorage.getTrades({ symbol, limit })).map((r) => ({ ...r }));
                      bHeaders = ["symbol", "side", "entryPrice", "exitPrice", "quantity", "entryTime", "exitTime", "netPnl", "pnlPct", "commission", "strategyId"];
                      break;
                    case "signals":
                      bRows = (await quantStorage.getSignals({ symbol, limit })).map((r) => ({ ...r }));
                      bHeaders = ["date", "timestamp", "symbol", "direction", "price", "quantity", "reason", "confidence", "strategyId"];
                      break;
                    case "orders":
                      bRows = (await quantStorage.getOrders({ symbol, limit })).map((r) => ({ ...r }));
                      bHeaders = ["orderId", "symbol", "market", "side", "orderType", "quantity", "price", "status", "filledQty", "filledPrice", "strategyId", "createdAt", "updatedAt"];
                      break;
                    case "equity":
                      bRows = (await quantStorage.getEquitySnapshots({ start: startDate, end: endDate, limit })).map((r) => ({ ...r }));
                      bHeaders = ["date", "equity", "cash", "marketValue", "dailyPnl", "positionCount", "benchmark"];
                      break;
                    case "journal": {
                      bHeaders = ["id", "date", "symbol", "direction", "entryPrice", "exitPrice", "pnl", "rating", "tags", "content", "lesson", "strategyId", "createdAt"];
                      const allNotes = (tradeJournal as unknown as { notes: Array<Record<string, unknown>> }).notes ?? [];
                      let filtered = allNotes;
                      if (symbol) filtered = filtered.filter((n) => n.symbol === symbol);
                      if (startDate) filtered = filtered.filter((n) => (n.date as string) >= startDate);
                      if (endDate) filtered = filtered.filter((n) => (n.date as string) <= endDate);
                      bRows = filtered.slice(0, limit).map((n) => ({ id: n.id, date: n.date, symbol: n.symbol, direction: n.direction ?? "", entryPrice: n.entryPrice ?? "", exitPrice: n.exitPrice ?? "", pnl: n.pnl ?? "", rating: n.rating ?? "", tags: Array.isArray(n.tags) ? (n.tags as string[]).join(";") : "", content: n.content ?? "", lesson: n.lesson ?? "", strategyId: n.strategyId ?? "", createdAt: n.createdAt ?? "" }));
                      break;
                    }
                    case "klines": {
                      if (!symbol) { resolve({ dataset: "klines", rows: 0, filename: "(跳过: 未指定 symbol)" }); return; }
                      const interval = (batchBody.interval as string) ?? "1d";
                      const start = startDate ?? "2000-01-01";
                      const end = endDate ?? new Date().toISOString().slice(0, 10);
                      bRows = (await quantStorage.getKlines(symbol, interval, start, end)).slice(0, limit).map((r) => ({ ...r }));
                      bHeaders = ["symbol", "date", "interval", "open", "high", "low", "close", "volume", "amount"];
                      bDataset = `klines_${symbol}`;
                      break;
                    }
                    case "backtest": {
                      if (!backtestStore) { resolve({ dataset: "backtest", rows: 0, filename: "(回测存储不可用)" }); return; }
                      bHeaders = ["id", "createdAt", "strategyId", "symbols", "startDate", "endDate", "engine", "initialCash", "finalValue", "totalReturn", "annualizedReturn", "maxDrawdown", "sharpeRatio", "winRate", "totalTrades", "tags", "notes"];
                      bRows = backtestStore.query().map((e: Record<string, unknown>) => ({ ...e, symbols: Array.isArray(e.symbols) ? (e.symbols as string[]).join(",") : e.symbols, tags: Array.isArray(e.tags) ? (e.tags as string[]).join(";") : e.tags }));
                      break;
                    }
                    case "screen": {
                      if (!scanHistoryStore) { resolve({ dataset: "screen", rows: 0, filename: "(选股历史不可用)" }); return; }
                      bHeaders = ["rank", "code", "name", "score", "screenerId", "universe", "market", "date", "signals"];
                      const index = scanHistoryStore.query({ limit: 1, sortBy: "createdAt", sortDir: "desc" });
                      if (index.length > 0) {
                        const latest = scanHistoryStore.getFullResult(index[0].id);
                        if (latest) {
                          bRows = latest.results.map((entry: Record<string, unknown>, i: number) => ({ rank: entry.rank ?? i + 1, code: entry.code, name: entry.name ?? "", score: entry.score ?? "", screenerId: latest.screenerId, universe: latest.universe, market: latest.market, date: latest.date, signals: Array.isArray(entry.signals) ? (entry.signals as string[]).join(";") : "" }));
                        }
                      }
                      break;
                    }
                    case "watchlist": {
                      bHeaders = ["code", "name", "category", "priority", "priceTarget", "convictionScore", "tags", "notes", "addedAt", "updatedAt"];
                      if (favoritesStore) {
                        const category = batchBody.category as string | undefined;
                        const stocks = favoritesStore.listFavorites(category ? { category } : undefined);
                        bRows = stocks.map((s) => ({ code: s.code, name: s.name ?? "", category: s.category ?? "", priority: s.priority ?? "", priceTarget: s.priceTarget ?? "", convictionScore: s.convictionScore ?? "", tags: s.tags?.join(";") ?? "", notes: s.notes ?? "", addedAt: s.addedAt, updatedAt: s.updatedAt }));
                      }
                      break;
                    }
                  }

                  const ts2 = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
                  const fn = `${bDataset}_${ts2}.${format}`;
                  const fp = pathMod.join(dir2, fn);
                  if (format === "json") {
                    fsMod.writeFileSync(fp, JSON.stringify(bRows, null, 2), "utf-8");
                  } else {
                    const csvHeader = bHeaders.map(escapeCsvField).join(",");
                    const csvRows = bRows.map((row) => bHeaders.map((h) => escapeCsvField(row[h])).join(","));
                    fsMod.writeFileSync(fp, "\uFEFF" + csvHeader + "\n" + csvRows.join("\n") + "\n", "utf-8");
                  }
                  resolve({ dataset: batchAction, rows: bRows.length, filename: fn });
                } catch (err) {
                  reject(err);
                }
              })();
            });
            results.push(await batchReq);
          } catch (err) {
            results.push({ dataset: batchAction, rows: 0, filename: `(错误: ${(err as Error).message})` });
          }
        }
        const totalRows = results.reduce((sum, r) => sum + r.rows, 0);
        json(res, { status: "ok", batchTypes, format, results, totalFiles: results.length, totalRows, message: `批量导出完成: ${results.length} 个文件, 共 ${totalRows} 条记录` });
        return;
      }

      try {
        const fsMod = await import("node:fs");
        const pathMod = await import("node:path");
        const { homedir } = await import("node:os");
        const base = config.dataDir ?? pathMod.join(homedir(), ".openclaw", "quant-trading");
        const dir = pathMod.join(base, "exports");
        fsMod.mkdirSync(dir, { recursive: true });

        let rows: Record<string, unknown>[] = [];
        let headers: string[] = [];
        let dataset = action;

        switch (action) {
          case "portfolio": {
            headers = ["source", "symbol", "name", "quantity", "avgCost", "currentPrice", "marketValue", "unrealizedPnl", "unrealizedPnlPct", "weight"];
            const mode = body.mode as string | undefined;
            // Paper portfolio
            if (!mode || mode === "all" || mode === "paper") {
              const portfolioDir = config.dataDir ?? pathMod.join(homedir(), ".openclaw", "quant-trading");
              for (const name of ["portfolio-paper.json", "portfolio.json"]) {
                try {
                  const raw = fsMod.readFileSync(pathMod.join(portfolioDir, name), "utf-8");
                  const paper = JSON.parse(raw) as { totalValue?: number; positions: Array<Record<string, unknown>> };
                  for (const pos of paper.positions) {
                    const qty = (pos.quantity as number) ?? 0;
                    const cost = (pos.avgCost as number) ?? 0;
                    const cur = (pos.currentPrice as number) ?? cost;
                    const mv = cur * qty;
                    const pnl = (cur - cost) * qty;
                    const pnlPct = cost > 0 ? (pnl / (cost * qty)) * 100 : 0;
                    rows.push({
                      source: "paper", symbol: pos.symbol ?? "", name: pos.name ?? "",
                      quantity: qty, avgCost: cost, currentPrice: cur, marketValue: mv,
                      unrealizedPnl: pnl, unrealizedPnlPct: Number(pnlPct.toFixed(2)),
                      weight: (paper.totalValue ?? 0) > 0 ? Number(((mv / (paper.totalValue ?? 1)) * 100).toFixed(2)) : 0,
                    });
                  }
                  break; // found file, stop trying
                } catch {
                  continue;
                }
              }
            }
            // Live positions
            if ((!mode || mode === "all" || mode === "live") && getEngine) {
              try {
                const engine = getEngine();
                if (engine) {
                  const engineApi = (engine as unknown as { api?: { getPositions?: () => Promise<unknown[]> } }).api;
                  if (engineApi?.getPositions) {
                    const positions = (await engineApi.getPositions()) as Array<Record<string, unknown>>;
                    for (const pos of positions) {
                      rows.push({
                        source: "live", symbol: pos.symbol ?? "", name: pos.name ?? "",
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
            dataset = "portfolio";
            break;
          }
          case "trades":
            rows = (await quantStorage.getTrades({ symbol, limit })).map((r) => ({ ...r }));
            headers = ["symbol", "side", "entryPrice", "exitPrice", "quantity", "entryTime", "exitTime", "netPnl", "pnlPct", "commission", "strategyId"];
            break;
          case "signals":
            rows = (await quantStorage.getSignals({ symbol, limit })).map((r) => ({ ...r }));
            headers = ["date", "timestamp", "symbol", "direction", "price", "quantity", "reason", "confidence", "strategyId"];
            break;
          case "orders":
            rows = (await quantStorage.getOrders({ symbol, limit })).map((r) => ({ ...r }));
            headers = ["orderId", "symbol", "market", "side", "orderType", "quantity", "price", "status", "filledQty", "filledPrice", "strategyId", "createdAt", "updatedAt"];
            break;
          case "equity":
            rows = (await quantStorage.getEquitySnapshots({ start: startDate, end: endDate, limit })).map((r) => ({ ...r }));
            headers = ["date", "equity", "cash", "marketValue", "dailyPnl", "positionCount", "benchmark"];
            break;
          case "klines": {
            if (!symbol) {
              json(res, { status: "error", message: "action=klines 需要指定 symbol 参数" }, 400);
              return;
            }
            const interval = (body.interval as string) ?? "1d";
            const start = startDate ?? "2000-01-01";
            const end = endDate ?? new Date().toISOString().slice(0, 10);
            rows = (await quantStorage.getKlines(symbol, interval, start, end)).slice(0, limit).map((r) => ({ ...r }));
            headers = ["symbol", "date", "interval", "open", "high", "low", "close", "volume", "amount"];
            dataset = `klines_${symbol}`;
            break;
          }
          case "backtest": {
            if (!backtestStore) {
              json(res, { status: "error", message: "回测存储不可用" }, 500);
              return;
            }
            const backtestId = body.backtestId as string | undefined;
            headers = ["id", "createdAt", "strategyId", "symbols", "startDate", "endDate", "engine", "initialCash", "finalValue", "totalReturn", "annualizedReturn", "maxDrawdown", "sharpeRatio", "winRate", "totalTrades", "tags", "notes"];
            if (backtestId) {
              const record = backtestStore.getFullResult(backtestId);
              if (!record) {
                json(res, { status: "error", message: `未找到回测记录: ${backtestId}` }, 404);
                return;
              }
              const r = record.result;
              rows = [{
                id: record.id, createdAt: record.createdAt, strategyId: r.strategyId,
                symbols: r.symbols.join(","), startDate: r.startDate, endDate: r.endDate,
                engine: record.engine, initialCash: r.initialCash, finalValue: r.finalValue,
                totalReturn: r.totalReturn, annualizedReturn: r.annualizedReturn,
                maxDrawdown: r.maxDrawdown, sharpeRatio: r.sharpeRatio,
                winRate: r.winRate, totalTrades: r.totalTrades,
                tags: record.tags.join(";"), notes: record.notes ?? "",
              }];
            } else {
              rows = backtestStore.query().map((e: Record<string, unknown>) => ({
                ...e,
                symbols: Array.isArray(e.symbols) ? (e.symbols as string[]).join(",") : e.symbols,
                tags: Array.isArray(e.tags) ? (e.tags as string[]).join(";") : e.tags,
              }));
            }
            break;
          }
          case "journal": {
            headers = ["id", "date", "symbol", "direction", "entryPrice", "exitPrice", "pnl", "rating", "tags", "content", "lesson", "strategyId", "createdAt"];
            const allNotes = (tradeJournal as unknown as { notes: Array<Record<string, unknown>> }).notes ?? [];
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
            rows = filtered.slice(0, limit).map((n) => ({
              id: n.id, date: n.date, symbol: n.symbol,
              direction: n.direction ?? "", entryPrice: n.entryPrice ?? "",
              exitPrice: n.exitPrice ?? "", pnl: n.pnl ?? "",
              rating: n.rating ?? "",
              tags: Array.isArray(n.tags) ? (n.tags as string[]).join(";") : "",
              content: n.content ?? "", lesson: n.lesson ?? "",
              strategyId: n.strategyId ?? "", createdAt: n.createdAt ?? "",
            }));
            dataset = "journal";
            break;
          }
          case "screen": {
            if (!scanHistoryStore) {
              json(res, { status: "error", message: "选股历史存储不可用" }, 500);
              return;
            }
            headers = ["rank", "code", "name", "score", "screenerId", "universe", "market", "date", "signals"];
            const screenId = body.screenId as string | undefined;
            if (screenId) {
              const record = scanHistoryStore.getFullResult(screenId);
              if (!record) {
                json(res, { status: "error", message: `未找到选股记录: ${screenId}` }, 404);
                return;
              }
              rows = record.results.map((entry: Record<string, unknown>, i: number) => ({
                rank: entry.rank ?? i + 1, code: entry.code, name: entry.name ?? "",
                score: entry.score ?? "", screenerId: record.screenerId,
                universe: record.universe, market: record.market,
                date: record.date,
                signals: Array.isArray(entry.signals) ? (entry.signals as string[]).join(";") : "",
              }));
            } else {
              const index = scanHistoryStore.query({ limit: 1, sortBy: "createdAt", sortDir: "desc" });
              if (index.length === 0) {
                rows = [];
              } else {
                const latest = scanHistoryStore.getFullResult(index[0].id);
                if (latest) {
                  rows = latest.results.map((entry: Record<string, unknown>, i: number) => ({
                    rank: entry.rank ?? i + 1, code: entry.code, name: entry.name ?? "",
                    score: entry.score ?? "", screenerId: latest.screenerId,
                    universe: latest.universe, market: latest.market,
                    date: latest.date,
                    signals: Array.isArray(entry.signals) ? (entry.signals as string[]).join(";") : "",
                  }));
                }
              }
            }
            dataset = "screen";
            break;
          }
          case "watchlist": {
            headers = ["code", "name", "category", "priority", "priceTarget", "convictionScore", "tags", "notes", "addedAt", "updatedAt"];
            if (favoritesStore) {
              const category = body.category as string | undefined;
              const stocks = favoritesStore.listFavorites(category ? { category } : undefined);
              rows = stocks.map((s) => ({
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
            }
            dataset = "watchlist";
            break;
          }
        }

        // Write file
        const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
        const filename = `${dataset}_${ts}.${format}`;
        const filePath = pathMod.join(dir, filename);

        if (format === "json") {
          fsMod.writeFileSync(filePath, JSON.stringify(rows, null, 2), "utf-8");
        } else {
          const csvHeader = headers.map(escapeCsvField).join(",");
          const csvRows = rows.map((row) => headers.map((h) => escapeCsvField(row[h])).join(","));
          fsMod.writeFileSync(filePath, "\uFEFF" + csvHeader + "\n" + csvRows.join("\n") + "\n", "utf-8");
        }

        const stat = fsMod.statSync(filePath);
        json(res, {
          status: "ok",
          dataset: action,
          format,
          rows: rows.length,
          file: filePath,
          filename,
          size: stat.size,
          message: `已导出 ${rows.length} 条记录到 ${filename}`,
        });
      } catch (e) {
        json(res, { status: "error", message: (e as Error).message }, 500);
      }
    }),
  });
}
