/**
 * Admin API routes for the quant-trading management panel.
 *
 * All routes prefixed with `/quant/admin/`.
 * The admin panel also calls existing `/quant/api/*` routes directly from
 * the frontend — those routes are NOT duplicated here.
 */
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { runPythonScript } from "../bridge/python-bridge.js";
import type { OrderRequest } from "../broker/adapter.js";
import {
  createDataProvider,
  detectMarketFromSymbol,
  getLotSize,
} from "../data/market-data-provider.js";
import type { LiveEngine } from "../engine/live-engine.js";
import { listStrategies } from "../engine/strategy-registry.js";
import type { TickFeatureStore } from "../engine/tick-feature-store.js";
import { buildAccumulationTop3 } from "../services/accumulation-top3.js";
import type { AlertStore } from "../services/alert-store.js";
import type { BreakoutMonitor } from "../services/breakout-monitor.js";
import type { ComposedStrategyStore } from "../services/composed-strategy-store.js";
import type { ConditionalOrderService } from "../services/conditional-order.js";
import type { HistoricalDataWarehouse } from "../services/data-warehouse.js";
import type { EventBus } from "../services/event-bus.js";
import type { ExplorationHistoryStore } from "../services/exploration-history-store.js";
import type { FavoritesStore } from "../services/favorites-store.js";
import type { LogStore } from "../services/log-store.js";
import type { OptimizationStore } from "../services/optimization-store.js";
import type { PriceAlertService } from "../services/price-alert.js";
import type { ReceiverService } from "../services/receiver-service.js";
import type { UserStrategyStore } from "../services/strategy-template.js";
import type { TaskScheduler } from "../services/task-scheduler.js";
import { analyzeTechMoat, collectTechMoatInputs } from "../services/tech-moat-analysis.js";
import type { TickHistoryStore } from "../services/tick-history-store.js";
import type { TickIngressService } from "../services/tick-ingress-service.js";
import type { TickStrategyService } from "../services/tick-strategy-service.js";
import type { KlineInterval, TickAggregationInterval } from "../types.js";
import type { QuantTradingConfig } from "../types.js";
import { toNumberOrDefault, toOptionalNumber } from "../utils/number-utils.js";
import type { HttpHandler } from "./auth-middleware.js";
import { appendScreenerHistory } from "./portfolio-api.js";
import {
  json,
  toFiniteNumber,
  clampInteger,
  round2,
  formatDateYmd,
  parseObjectBody,
} from "./routes/_helpers.js";
import { registerDailyPickRoutes } from "./routes/daily-pick.js";
import { registerStrategyLabRoutes } from "./routes/strategy-lab.js";
import { registerStrategyRoutingRoutes } from "./routes/strategy-routing.js";

// ── Types ────────────────────────────────────────────────────────

export interface AdminRouteDeps {
  api: OpenClawPluginApi;
  config: QuantTradingConfig;
  dataWarehouse: HistoricalDataWarehouse;
  receiverService: ReceiverService | null;
  userStrategyStore: UserStrategyStore;
  alertStore: AlertStore;
  eventBus: EventBus;
  logStore: LogStore;
  priceAlertService: PriceAlertService;
  conditionalOrderService: ConditionalOrderService;
  breakoutMonitor?: BreakoutMonitor;
  composedStrategyStore?: ComposedStrategyStore;
  explorationHistoryStore?: ExplorationHistoryStore;
  favoritesStore: FavoritesStore;
  optimizationStore?: OptimizationStore;
  taskScheduler: TaskScheduler;
  getEngine: () => LiveEngine;
  tickIngressService?: TickIngressService | null;
  tickFeatureStore?: TickFeatureStore | null;
  tickStrategyService?: TickStrategyService | null;
  tickHistoryStore?: TickHistoryStore | null;
  secure: (handler: HttpHandler) => HttpHandler;
  securePublic: (handler: HttpHandler) => HttpHandler;
}

// ── Available data provider metadata ─────────────────────────────

const DATA_PROVIDERS = [
  { id: "akshare", description: "AKShare — A 股 / 期货 / 基金免费数据源" },
  { id: "jqdata", description: "JQData — 聚宽本地数据 SDK (需账号密码)" },
  { id: "tushare", description: "Tushare — A 股专业数据接口 (需 Token)" },
  { id: "yfinance", description: "Yahoo Finance — 美股 / 全球市场" },
  { id: "smart", description: "Smart 智能路由 — 根据市场自动选择数据源" },
  { id: "demo", description: "Demo 模拟数据 — 用于测试和演示" },
];

// ── Route Registration ───────────────────────────────────────────

export function registerAdminRoutes(deps: AdminRouteDeps): void {
  const {
    api,
    config,
    dataWarehouse,
    receiverService,
    userStrategyStore,
    alertStore,
    eventBus,
    logStore,
    priceAlertService,
    conditionalOrderService,
    composedStrategyStore,
    explorationHistoryStore,
    favoritesStore,
    optimizationStore,
    taskScheduler,
    getEngine,
    secure,
    securePublic,
  } = deps;
  const marketDataProvider = createDataProvider(config);

  // ── Portal (Home) Page ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/",
    handler: securePublic(async (_req, res) => {
      const { getPortalPageHtml } = await import("./pages/portal-page.js");
      const html = getPortalPageHtml("");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Admin UI Page ──────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/ui",
    handler: securePublic(async (_req, res) => {
      const { getAdminPageHtml } = await import("./pages/admin/index.js");
      // Try to resolve gateway auth token so the chat tab can connect via WebSocket
      const rawGwToken = api.config.gateway?.auth?.token;
      let gatewayToken: string | undefined;
      if (typeof rawGwToken === "string") {
        // Resolve ${ENV_VAR} patterns
        const envMatch = /^\$\{(\w+)\}$/.exec(rawGwToken);
        gatewayToken = envMatch ? process.env[envMatch[1]] : rawGwToken;
      }
      const html = getAdminPageHtml("", config.apiToken, gatewayToken);
      res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
      });
      res.end(html);
    }),
  });
  // ── Stock Detail Page ──────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/stock",
    handler: securePublic(async (_req, res) => {
      const { getStockDetailPageHtml } = await import("./pages/stock-detail-page.js");
      const html = getStockDetailPageHtml("", config.apiToken);
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Stock Detail API ───────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-detail",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const symbol = url.searchParams.get("symbol");
      if (!symbol) {
        json(res, { error: "缺少 symbol 参数" }, 400);
        return;
      }
      const market = url.searchParams.get("market") || "cn_stock";
      const klineDays = Number(url.searchParams.get("kline_days") || 120);
      const klineInterval = url.searchParams.get("kline_interval") || "1d";
      try {
        const result = await runPythonScript(
          "scripts/stock_detail.py",
          {
            action: "detail",
            symbol,
            market,
            kline_days: klineDays,
            kline_interval: klineInterval,
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "Python 脚本执行失败" }, 500);
          return;
        }
        const payload =
          result.data && typeof result.data === "object"
            ? { ...(result.data as Record<string, unknown>) }
            : {};

        const { companyName, newsTitles } = collectTechMoatInputs(payload);
        if (newsTitles.length > 0) {
          try {
            const moatResult = await analyzeTechMoat(symbol, companyName, newsTitles, config);
            if (moatResult) payload.techMoat = moatResult;
          } catch {
            // Keep the stock detail page functional when moat analysis is unavailable.
          }
        }

        json(res, payload);
      } catch (err) {
        json(
          res,
          { error: `获取个股详情失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/tech-moat",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }

      try {
        const body = await parseObjectBody(req);
        const symbol = String(body.symbol ?? "").trim();
        if (!symbol) {
          json(res, { error: "缺少 symbol 参数" }, 400);
          return;
        }

        const rawNewsTitles = Array.isArray(body.news_titles)
          ? body.news_titles.map((item) => String(item ?? "").trim()).filter(Boolean)
          : [];
        let companyName = String(body.company_name ?? "").trim();
        let newsTitles = rawNewsTitles;

        if (newsTitles.length === 0) {
          const detailResult = await runPythonScript(
            "scripts/stock_detail.py",
            {
              action: "detail",
              symbol,
              market: body.market ?? "cn_stock",
              kline_days: 120,
              kline_interval: "1d",
            },
            config,
          );
          if (!detailResult.ok || !detailResult.data || typeof detailResult.data !== "object") {
            json(res, { error: detailResult.error ?? "技术壁垒数据准备失败" }, 500);
            return;
          }
          const inputs = collectTechMoatInputs(detailResult.data as Record<string, unknown>);
          companyName = companyName || inputs.companyName;
          newsTitles = inputs.newsTitles;
        }

        const moat = await analyzeTechMoat(symbol, companyName, newsTitles, config);
        if (!moat) {
          json(res, { error: "未收集到足够的新闻/研报证据" }, 400);
          return;
        }
        json(res, moat);
      } catch (err) {
        json(
          res,
          { error: `技术壁垒分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });
  // ── Investment Planner Page ────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/planner",
    handler: securePublic(async (_req, res) => {
      const { getInvestmentPlannerPageHtml } = await import("./pages/investment-planner/index.js");
      const html = getInvestmentPlannerPageHtml("");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Favorites Page ────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/favorites",
    handler: securePublic(async (_req, res) => {
      const { getFavoritesPageHtml } = await import("./pages/favorites-page.js");
      const html = getFavoritesPageHtml("");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Favorites API ─────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/favorites",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");

      if (req.method === "GET") {
        const category = url.searchParams.get("category") || undefined;
        const query = url.searchParams.get("query") || undefined;
        const tag = url.searchParams.get("tag") || undefined;
        const minPriorityRaw = url.searchParams.get("minPriority");
        const minPriority = minPriorityRaw ? Number(minPriorityRaw) : undefined;

        let favorites = query
          ? favoritesStore.search(query)
          : favoritesStore.listFavorites({
              category,
              tags: tag ? [tag] : undefined,
              priority: Number.isFinite(minPriority) ? minPriority : undefined,
            });

        if (category) {
          favorites = favorites.filter((item) => item.category === category);
        }
        if (tag) {
          favorites = favorites.filter((item) => item.tags?.includes(tag));
        }
        if (Number.isFinite(minPriority)) {
          favorites = favorites.filter((item) => (item.priority ?? 0) >= (minPriority as number));
        }

        const favoritesWithQuote = await Promise.all(
          favorites.map(async (item) => {
            try {
              const market = detectMarketFromSymbol(item.code);
              const quote = await marketDataProvider.getQuote(item.code, market);
              if (!quote) {
                return {
                  ...item,
                  currentPrice: null,
                  change: null,
                  changePct: null,
                  quoteTimestamp: null,
                };
              }
              return {
                ...item,
                currentPrice: quote.price,
                change: quote.change,
                changePct: quote.changePct,
                quoteTimestamp: quote.timestamp,
              };
            } catch {
              return {
                ...item,
                currentPrice: null,
                change: null,
                changePct: null,
                quoteTimestamp: null,
              };
            }
          }),
        );

        json(res, {
          count: favorites.length,
          favorites: favoritesWithQuote,
          categories: favoritesStore.getAllCategories(),
          tags: favoritesStore.getAllTags(),
          categoryCounts: favoritesStore.countByCategory(),
        });
        return;
      }

      if (req.method === "POST") {
        const body = await parseObjectBody(req);
        const code = String(body.code ?? "").trim();
        if (!code) {
          json(res, { error: "缺少 code" }, 400);
          return;
        }
        const favorite = await favoritesStore.addFavorite(code, {
          name: typeof body.name === "string" ? body.name.trim() || undefined : undefined,
          notes: typeof body.notes === "string" ? body.notes.trim() || undefined : undefined,
          category:
            typeof body.category === "string" ? body.category.trim() || undefined : undefined,
          tags: Array.isArray(body.tags)
            ? body.tags.map((item) => String(item).trim()).filter(Boolean)
            : undefined,
          priority: Number.isFinite(Number(body.priority)) ? Number(body.priority) : undefined,
          priceTarget: Number.isFinite(Number(body.priceTarget))
            ? Number(body.priceTarget)
            : undefined,
          convictionScore: Number.isFinite(Number(body.convictionScore))
            ? Number(body.convictionScore)
            : undefined,
        });
        json(res, { ok: true, favorite });
        return;
      }

      if (req.method === "PATCH") {
        const body = await parseObjectBody(req);
        const code = String(body.code ?? "").trim();
        if (!code) {
          json(res, { error: "缺少 code" }, 400);
          return;
        }
        const existing = favoritesStore.getFavorite(code);
        if (!existing) {
          json(res, { error: `未找到收藏股票: ${code}` }, 404);
          return;
        }
        const favorite = await favoritesStore.addFavorite(code, {
          name: typeof body.name === "string" ? body.name.trim() || undefined : existing.name,
          notes: typeof body.notes === "string" ? body.notes.trim() || undefined : existing.notes,
          category:
            typeof body.category === "string"
              ? body.category.trim() || undefined
              : existing.category,
          tags: Array.isArray(body.tags)
            ? body.tags.map((item) => String(item).trim()).filter(Boolean)
            : existing.tags,
          priority:
            body.priority === undefined
              ? existing.priority
              : Number.isFinite(Number(body.priority))
                ? Number(body.priority)
                : undefined,
          priceTarget:
            body.priceTarget === undefined
              ? existing.priceTarget
              : Number.isFinite(Number(body.priceTarget))
                ? Number(body.priceTarget)
                : undefined,
          convictionScore:
            body.convictionScore === undefined
              ? existing.convictionScore
              : Number.isFinite(Number(body.convictionScore))
                ? Number(body.convictionScore)
                : undefined,
        });
        json(res, { ok: true, favorite });
        return;
      }

      if (req.method === "DELETE") {
        const body = await parseObjectBody(req);
        const code = String(body.code ?? "").trim();
        if (!code) {
          json(res, { error: "缺少 code" }, 400);
          return;
        }
        const removed = await favoritesStore.removeFavorite(code);
        json(
          res,
          removed ? { ok: true } : { error: `未找到收藏股票: ${code}` },
          removed ? 200 : 404,
        );
        return;
      }

      json(res, { error: "仅支持 GET/POST/PATCH/DELETE" }, 405);
    }),
  });

  // ── Lianban (连板分析) Page ────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/lianban",
    handler: securePublic(async (_req, res) => {
      const { getLianbanPageHtml } = await import("./pages/lianban-page.js");
      const html = getLianbanPageHtml("");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Event Catalyst (事件催化) Page ─────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/event-catalyst",
    handler: securePublic(async (_req, res) => {
      const { getEventCatalystPageHtml } = await import("./pages/event-catalyst/index.js");
      const html = getEventCatalystPageHtml("");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Financial Data Hub (金融数据中心) Page ─────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/datahub",
    handler: securePublic(async (_req, res) => {
      const { getDatahubPageHtml } = await import("./pages/datahub-page.js");
      const html = getDatahubPageHtml("");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Financial Data Hub API ─────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/datahub",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const action = String(body.action ?? "");
        if (!action) {
          json(res, { error: "缺少 action 参数" }, 400);
          return;
        }
        let script = "";
        const params: Record<string, unknown> = {};
        switch (action) {
          case "macro": {
            script = "engines/macro_economic.py";
            params.action = body.indicator ? "history" : "regime";
            if (body.indicator) params.indicator = body.indicator;
            params.market = "cn";
            params.limit = 12;
            break;
          }
          case "earnings": {
            script = "engines/earnings_calendar.py";
            params.action = "upcoming";
            params.market = "cn";
            params.days = Number(body.days) || 30;
            params.limit = 50;
            break;
          }
          case "institutional": {
            script = "engines/institutional_flow.py";
            const subAction = String(body.sub_action ?? "smart_money_signal");
            params.action = subAction;
            if (body.symbol) params.symbol = body.symbol;
            if (subAction === "block_trades") params.limit = 20;
            break;
          }
          case "sentiment": {
            script = "engines/social_sentiment.py";
            params.action = "stock_sentiment";
            if (body.symbol) params.symbol = body.symbol;
            break;
          }
          case "hot_stocks": {
            script = "engines/social_sentiment.py";
            params.action = "hot_stocks";
            params.market = "cn";
            params.limit = Number(body.limit) || 20;
            break;
          }
          default:
            json(res, { error: `未知 action: ${action}` }, 400);
            return;
        }
        const result = await runPythonScript<Record<string, unknown>>(script, params, config);
        if (!result.ok) {
          json(res, { error: result.error ?? "Python 脚本执行失败" }, 500);
          return;
        }
        json(res, { data: result.data });
      } catch (err) {
        json(
          res,
          { error: `数据查询失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Chip Accumulation API (主力吸筹检测) ────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/chip-accumulation",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const action = String(body.action ?? "detect");
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/chip_accumulation.py",
          {
            action,
            symbol: body.symbol,
            symbols: body.symbols,
            lookback: toNumberOrDefault(body.lookback, 30),
            min_confidence: toNumberOrDefault(body.min_confidence, 40),
            as_of_date: body.as_of_date ?? body.asOfDate,
            ambush_volume_ratio: toOptionalNumber(body.ambush_volume_ratio),
            ambush_pre_days: toOptionalNumber(body.ambush_pre_days),
            ambush_pre_hits: toOptionalNumber(body.ambush_pre_hits),
            ambush_change_max: toOptionalNumber(body.ambush_change_max),
            ambush_change_min: toOptionalNumber(body.ambush_change_min),
            ambush_turnover_min: toOptionalNumber(body.ambush_turnover_min),
            ambush_turnover_max: toOptionalNumber(body.ambush_turnover_max),
            ambush_ma_period: toOptionalNumber(body.ambush_ma_period),
            ambush_ma_deviation_max: toOptionalNumber(body.ambush_ma_deviation_max),
            ambush_shrink_ratio: toOptionalNumber(body.ambush_shrink_ratio),
            ambush_stop_volatility: toOptionalNumber(body.ambush_stop_volatility),
            ambush_near_low_max: toOptionalNumber(body.ambush_near_low_max),
            ambush_box_days: toOptionalNumber(body.ambush_box_days),
            ambush_box_amplitude: toOptionalNumber(body.ambush_box_amplitude),
            ambush_ma_convergence: toOptionalNumber(body.ambush_ma_convergence),
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "主力吸筹引擎执行失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `主力吸筹检测失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/chip-accumulation/top3",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const rawSymbols = body.symbols;
        let symbols: string[] | undefined;
        if (typeof rawSymbols === "string" && rawSymbols.trim()) {
          symbols = rawSymbols
            .split(/[\s,，]+/)
            .map((s) => s.trim())
            .filter(Boolean);
        } else if (Array.isArray(rawSymbols)) {
          symbols = rawSymbols.map((s) => String(s).trim()).filter(Boolean);
        }
        const result = await buildAccumulationTop3({
          config,
          symbols,
          lookback: toNumberOrDefault(body.lookback, 30),
          min_confidence: toNumberOrDefault(body.min_confidence, 40),
          evaluate_count: toNumberOrDefault(body.evaluate_count, 15),
          as_of_date:
            typeof body.as_of_date === "string"
              ? body.as_of_date
              : typeof body.asOfDate === "string"
                ? body.asOfDate
                : undefined,
          thresholds: {
            pre_accum_days: toNumberOrDefault(body.pre_accum_days, 10),
            pre_accum_count_min: toNumberOrDefault(body.pre_accum_count_min, 2),
            pre_accum_volume_ratio: toNumberOrDefault(body.pre_accum_volume_ratio, 1.5),
            rush_change_pct: toNumberOrDefault(body.rush_change_pct, 5),
            rush_volume_ratio: toNumberOrDefault(body.rush_volume_ratio, 2),
            rush_turnover_pct: toNumberOrDefault(body.rush_turnover_pct, 3),
            pre_surge_chip_min: toNumberOrDefault(body.pre_surge_chip_min, 8),
            pre_surge_volume_price_min: toNumberOrDefault(body.pre_surge_volume_price_min, 6),
            pre_surge_turnover_min: toNumberOrDefault(body.pre_surge_turnover_min, 6),
          },
        });
        json(res, result);
      } catch (err) {
        json(
          res,
          {
            error: `主力吸筹条件 Top3 生成失败: ${err instanceof Error ? err.message : String(err)}`,
          },
          500,
        );
      }
    }),
  });

  // ── Lianban API ────────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/lianban",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const action = url.searchParams.get("action") || "scan";
      const params: Record<string, string> = { action };
      const symbol = url.searchParams.get("symbol");
      if (symbol) params.symbol = symbol;
      const date = url.searchParams.get("date");
      if (date) params.date = date;
      const minBoards = url.searchParams.get("min_boards");
      if (minBoards) params.min_boards = minBoards;
      const limit = url.searchParams.get("limit");
      if (limit) params.limit = limit;
      try {
        const result = await runPythonScript("scripts/lianban_dashboard.py", params, config);
        if (!result.ok) {
          json(res, { error: result.error ?? "连板数据获取失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `连板分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Dragon Master (真龙战法) Page ──────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/dragon-master",
    handler: securePublic(async (_req, res) => {
      const { getDragonMasterPageHtml } = await import("./pages/dragon-master-page.js");
      const html = getDragonMasterPageHtml("");
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Dragon Master API ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/dragon-master",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const action = url.searchParams.get("action") || "full";
      const params: Record<string, string> = { action };
      const sector = url.searchParams.get("sector");
      if (sector) params.sector = sector;
      const code = url.searchParams.get("code");
      if (code) params.code = code;
      const minScore = url.searchParams.get("min_score");
      if (minScore) params.min_score = minScore;
      const top = url.searchParams.get("top");
      if (top) params.top = top;
      try {
        const result = await runPythonScript("engines/dragon_master.py", params, config);
        if (!result.ok) {
          json(res, { error: result.error ?? "真龙战法分析失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `真龙战法分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Investment Plan Generation API ─────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/investment-plan",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const result = await runPythonScript(
          "scripts/investment_planner.py",
          {
            action: "plan",
            initial_capital: body.initial_capital ?? 500000,
            target_capital: body.target_capital ?? 1000000,
            timeframe_months: body.timeframe_months ?? 12,
            risk_tolerance: body.risk_tolerance ?? "medium",
            strategy_preference: body.strategy_preference ?? "auto",
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "投资计划生成失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `投资计划生成失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Investment Plan History API ─────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/plan-history",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const planId = url.searchParams.get("id");

      if (req.method === "POST") {
        // Save a plan to history
        try {
          const body = await parseObjectBody(req);
          const { appendPlanHistory } = await import("./portfolio-api.js");
          const plan = (body.plan as Record<string, unknown>) ?? {};
          const params = (body.params as Record<string, unknown>) ?? {};
          await appendPlanHistory(config, plan, params);
          json(res, { status: "ok" });
        } catch (err) {
          json(
            res,
            { error: `保存计划失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      if (req.method === "DELETE" && planId) {
        // Delete a specific plan from history
        try {
          const { handleDeletePlanApi } = await import("./portfolio-api.js");
          json(res, await handleDeletePlanApi(config, planId));
        } catch (err) {
          json(
            res,
            { error: `删除失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      if (req.method === "PATCH" && planId) {
        // Update plan label/notes
        try {
          const body = await parseObjectBody(req);
          const { handleUpdatePlanLabelApi } = await import("./portfolio-api.js");
          json(res, await handleUpdatePlanLabelApi(config, planId, String(body.label ?? "")));
        } catch (err) {
          json(
            res,
            { error: `更新失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      // GET — with id: get single plan with full data; without id: list all
      try {
        if (planId) {
          const { handleGetPlanByIdApi } = await import("./portfolio-api.js");
          json(res, await handleGetPlanByIdApi(config, planId));
        } else {
          const { handleGetPlanHistoryApi } = await import("./portfolio-api.js");
          json(res, await handleGetPlanHistoryApi(config));
        }
      } catch (err) {
        json(
          res,
          { error: `加载历史失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Stock Screening API ────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/investment-screen",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const strategy = (body as Record<string, unknown>).strategy ?? "auto";
        const topN = (body as Record<string, unknown>).top_n ?? 20;

        // Route screener-registry strategies to screener_engine.py
        if (strategy === "overnight") {
          const result = await runPythonScript<{
            results?: Array<Record<string, unknown>>;
            screenerId?: string;
            screenerName?: string;
            count?: number;
          }>(
            "engines/screener_engine.py",
            {
              action: "run_screener",
              screenerId: "overnight",
              market: "cn_stock",
              limit: topN,
              includeKlines: true,
              klineDays: 30,
              params: {},
            },
            config,
          );
          if (!result.ok) {
            json(res, { error: result.error ?? "一夜持股法筛选失败" }, 500);
            return;
          }
          // Transform screener results to planner UI format
          const candidates = (result.data?.results ?? []).map(
            (r: Record<string, unknown>, i: number) => {
              const factors = (r.factors ?? {}) as Record<string, unknown>;
              return {
                rank: i + 1,
                symbol: r.stock_id,
                name: r.name ?? r.stock_id,
                price: factors.price ?? 0,
                changePct: factors.change_pct ?? 0,
                pe: 0,
                pb: 0,
                totalMarketCap: ((factors.circulating_mv_yi as number) ?? 0) * 1e8,
                turnoverRate: factors.turnover_rate ?? 0,
                volumeRatio: factors.volume_ratio ?? 0,
                score: r.score ?? 0,
                reasons: r.signals ?? [],
              };
            },
          );
          const screenResult = {
            candidates,
            strategy: result.data?.screenerName ?? "一夜持股法",
            riskProfile: "短线价差",
            timestamp: new Date().toISOString(),
          };
          // Save to screener history for comparison
          const codes = candidates.map((c: { symbol: unknown }) => String(c.symbol));
          appendScreenerHistory(config, codes, "一夜持股法").catch(() => {});
          json(res, screenResult);
          return;
        }

        if (strategy === "sector_hotspot") {
          const result = await runPythonScript<{
            results?: Array<Record<string, unknown>>;
            screenerId?: string;
            screenerName?: string;
            count?: number;
          }>(
            "engines/screener_engine.py",
            {
              action: "run_screener",
              screenerId: "sector_hotspot",
              market: "cn_stock",
              limit: topN,
              includeKlines: false,
              params: {},
            },
            config,
          );
          if (!result.ok) {
            json(res, { error: result.error ?? "板块热点竞价候选筛选失败" }, 500);
            return;
          }
          const candidates = (result.data?.results ?? []).map(
            (r: Record<string, unknown>, i: number) => {
              const factors = (r.factors ?? {}) as Record<string, unknown>;
              return {
                rank: i + 1,
                symbol: r.stock_id,
                name: r.name ?? r.stock_id,
                price: factors.price ?? 0,
                changePct: factors.changePct ?? 0,
                pe: 0,
                pb: 0,
                totalMarketCap: ((factors.marketCapYi as number) ?? 0) * 1e8,
                turnoverRate: 0,
                volumeRatio: 0,
                score: r.score ?? 0,
                reasons: r.signals ?? [],
              };
            },
          );
          const screenResult = {
            candidates,
            strategy: result.data?.screenerName ?? "板块热点竞价候选",
            riskProfile: "短线热点跟风",
            timestamp: new Date().toISOString(),
          };
          const codes = candidates.map((c: { symbol: unknown }) => String(c.symbol));
          appendScreenerHistory(config, codes, "板块热点竞价候选").catch(() => {});
          json(res, screenResult);
          return;
        }

        // ── 真龙战法 (Dragon Master) ──
        if (strategy === "dragon_master") {
          const result = await runPythonScript<{
            results?: Array<Record<string, unknown>>;
            screenerId?: string;
            screenerName?: string;
            count?: number;
          }>(
            "engines/screener_engine.py",
            {
              action: "run_screener",
              screenerId: "dragon_master",
              market: "cn_stock",
              limit: topN,
              includeKlines: false,
              params: (body as Record<string, unknown>).params ?? {},
            },
            config,
          );
          if (!result.ok) {
            json(res, { error: result.error ?? "真龙战法筛选失败" }, 500);
            return;
          }
          const candidates = (result.data?.results ?? []).map(
            (r: Record<string, unknown>, i: number) => {
              const factors = (r.factors ?? {}) as Record<string, unknown>;
              return {
                rank: i + 1,
                symbol: r.stock_id,
                name: r.name ?? r.stock_id,
                price: factors.price ?? 0,
                changePct: factors.changePct ?? 0,
                pe: 0,
                pb: 0,
                totalMarketCap: ((factors.flow_value_yi as number) ?? 0) * 1e8,
                turnoverRate: factors.turnover_rate ?? 0,
                volumeRatio: 0,
                score: r.score ?? 0,
                reasons: r.signals ?? [],
                dragonRank: factors.dragon_rank ?? 0,
                sector: factors.sector ?? "",
                boardNumber: factors.board_number ?? 0,
                isBestTiming: factors.is_best_timing ?? false,
                sealAmountYi: factors.seal_amount_yi ?? 0,
                riskLevel: factors.risk_level ?? "high",
                buyAction: factors.buy_action ?? "wait",
              };
            },
          );
          const screenResult = {
            candidates,
            strategy: result.data?.screenerName ?? "真龙战法",
            riskProfile: "短线打板",
            timestamp: new Date().toISOString(),
          };
          const codes = candidates.map((c: { symbol: unknown }) => String(c.symbol));
          appendScreenerHistory(config, codes, "真龙战法").catch(() => {});
          json(res, screenResult);
          return;
        }

        const result = await runPythonScript(
          "scripts/investment_planner.py",
          {
            action: "screen",
            strategy,
            risk_tolerance: (body as Record<string, unknown>).risk_tolerance ?? "medium",
            top_n: topN,
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "选股筛选失败" }, 500);
          return;
        }
        // Save to screener history for comparison
        const screenData = result.data as Record<string, unknown> | undefined;
        const screenCandidates = (screenData?.candidates ?? []) as Array<{ symbol?: string }>;
        if (screenCandidates.length > 0) {
          const codes = screenCandidates.map((c) => String(c.symbol ?? "")).filter(Boolean);
          const strategyLabel = screenData?.strategy ?? String(strategy);
          appendScreenerHistory(config, codes, String(strategyLabel)).catch(() => {});
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `选股筛选失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Portfolio Monitor API ──────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/investment-monitor",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const result = await runPythonScript(
          "scripts/investment_planner.py",
          {
            action: "monitor",
            plan: body.plan ?? {},
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "持仓监控失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `持仓监控失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Rebalance Advisor API ──────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/rebalance",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const result = await runPythonScript(
          "scripts/investment_planner.py",
          {
            action: "rebalance",
            plan: body.plan ?? {},
            newCandidates: body.newCandidates ?? [],
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "调仓分析失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `调仓分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Stock Recommendation API ─────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/recommend",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const rawSymbols = body.symbols ?? body.customSymbols;
        let symbolList: string[] | undefined;
        if (typeof rawSymbols === "string" && rawSymbols.trim()) {
          symbolList = rawSymbols
            .split(/[,\s]+/)
            .map((s: string) => s.trim())
            .filter(Boolean);
        } else if (Array.isArray(rawSymbols)) {
          symbolList = rawSymbols;
        }
        const result = await runPythonScript(
          "scripts/stock_recommend.py",
          {
            capital: body.capital ?? body.initialCapital ?? body.initial_capital ?? 500000,
            target_capital: body.target_capital ?? body.targetCapital ?? 0,
            strategy: body.strategy ?? "mixed",
            risk_tolerance: body.risk_tolerance ?? body.riskTolerance ?? "high",
            rebalance_days: body.rebalance_days ?? body.rebalanceDays ?? 5,
            symbols: symbolList,
            start_date: body.start_date ?? body.startDate ?? "2025-06-01",
            end_date: body.end_date ?? body.endDate,
            exclude_star: body.exclude_star ?? body.excludeStar ?? true,
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "推荐失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(res, { error: `推荐失败: ${err instanceof Error ? err.message : String(err)}` }, 500);
      }
    }),
  });

  // ── Execute Recommendation API ──────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/recommend-execute",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }

      try {
        const body = await parseObjectBody(req);
        const rawAllocs = Array.isArray(body.allocations) ? body.allocations : [];
        if (rawAllocs.length === 0) {
          json(res, { error: "缺少 allocations，无法执行建仓" }, 400);
          return;
        }

        const dryRun = body.dry_run === true || body.dryRun === true;
        const forcePremarket = body.force_premarket === true || body.forcePremarket === true;
        const executePct = Math.min(100, Math.max(10, toFiniteNumber(body.execute_pct, 60)));
        const createConditionalOrders =
          body.create_conditional_orders !== false && body.createConditionalOrders !== false;
        const trailingStopPct =
          Math.min(20, Math.max(2, toFiniteNumber(body.trailing_stop_pct, 8))) / 100;

        type ExecPlanItem = {
          code: string;
          name: string;
          market: ReturnType<typeof detectMarketFromSymbol>;
          orderType: "limit";
          limitPrice: number;
          quantity: number;
          notional: number;
          stopLossPrice?: number;
          takeProfitPrice?: number;
          sourceShares: number;
        };

        const planItems: ExecPlanItem[] = [];
        const skipped: Array<{ code: string; reason: string }> = [];
        const seenCodes = new Set<string>();

        for (const raw of rawAllocs as Array<Record<string, unknown>>) {
          const code = String(raw.code ?? "").trim();
          if (!code) continue;
          if (seenCodes.has(code)) {
            skipped.push({ code, reason: "同批次重复，已跳过" });
            continue;
          }
          seenCodes.add(code);

          const name = String(raw.name ?? code);
          const sourceShares = clampInteger(raw.shares, 0, 10_000_000, 0);
          if (sourceShares <= 0) {
            skipped.push({ code, reason: "建议股数为 0" });
            continue;
          }

          const market = detectMarketFromSymbol(code);
          const lotSize = getLotSize(code, market);

          const entryPrices =
            raw.entryPrices && typeof raw.entryPrices === "object"
              ? (raw.entryPrices as Record<string, unknown>)
              : undefined;
          const batch1 =
            entryPrices?.batch1 && typeof entryPrices.batch1 === "object"
              ? (entryPrices.batch1 as Record<string, unknown>)
              : undefined;
          const rawBatchPct = toFiniteNumber(batch1?.pct, executePct);
          const batchPct = Math.min(100, Math.max(10, rawBatchPct));

          const targetShares = Math.max(1, Math.floor((sourceShares * batchPct) / 100));
          let quantity = lotSize > 1 ? Math.floor(targetShares / lotSize) * lotSize : targetShares;
          if (quantity <= 0 && sourceShares >= lotSize) {
            quantity = lotSize;
          }
          if (quantity <= 0) {
            skipped.push({ code, reason: "按手数取整后为 0" });
            continue;
          }

          const limitPrice = toFiniteNumber(batch1?.price, toFiniteNumber(raw.price, 0));
          if (limitPrice <= 0) {
            skipped.push({ code, reason: "无有效入场价格" });
            continue;
          }

          const exits =
            raw.exitTargets && typeof raw.exitTargets === "object"
              ? (raw.exitTargets as Record<string, unknown>)
              : undefined;
          const stopLoss =
            exits?.stopLoss && typeof exits.stopLoss === "object"
              ? (exits.stopLoss as Record<string, unknown>)
              : undefined;
          const takeProfit1 =
            exits?.takeProfit1 && typeof exits.takeProfit1 === "object"
              ? (exits.takeProfit1 as Record<string, unknown>)
              : undefined;

          const stopLossPrice = toFiniteNumber(stopLoss?.price, 0);
          const takeProfitPrice = toFiniteNumber(takeProfit1?.price, 0);

          planItems.push({
            code,
            name,
            market,
            orderType: "limit",
            limitPrice: round2(limitPrice),
            quantity,
            notional: round2(quantity * limitPrice),
            stopLossPrice: stopLossPrice > 0 ? round2(stopLossPrice) : undefined,
            takeProfitPrice: takeProfitPrice > 0 ? round2(takeProfitPrice) : undefined,
            sourceShares,
          });
        }

        const estimatedAmount = round2(planItems.reduce((sum, i) => sum + i.notional, 0));
        const plan = {
          executePct,
          totalOrders: planItems.length,
          estimatedAmount,
          skipped,
          items: planItems,
        };

        if (dryRun) {
          json(res, {
            status: "ok",
            dryRun: true,
            plan,
            message: "试运行完成（未实际下单）",
          });
          return;
        }

        const engine = getEngine();
        const orderMgr = engine.orderManager;
        if (!orderMgr) {
          json(
            res,
            {
              error: "实盘引擎未启动，请先启动实盘引擎后再执行建仓",
              hint: "可通过 /quant/api/engine/start 或 live_trading.start_engine 启动",
              plan,
            },
            400,
          );
          return;
        }

        // Pre-check market hours: fail fast if market is closed and not in
        // premarket mode, instead of submitting each order individually only
        // to have them all rejected by RiskGate's MARKET_HOURS rule.
        if (!dryRun && !forcePremarket) {
          const { isMarketOpen } = await import("../broker/paper-broker.js");
          // Check the primary market for the first item; all items in a
          // recommendation batch are typically from the same market.
          const primaryMarket = planItems[0]?.market;
          if (!isMarketOpen(primaryMarket)) {
            json(
              res,
              {
                error: "当前非交易时段，无法提交实盘委托",
                hint: "请使用「📋 预埋委托（盘前下单）」按钮提交预埋单，委托将在开盘后按限价撮合。",
                plan,
              },
              400,
            );
            return;
          }
        }

        // Pre-fetch account state for position-limit pre-check.
        // Prefer a fresh broker query so the cash balance is up-to-date
        // (cached lastAccount may be stale or null if sync hasn't run yet).
        let account = engine.getAccount();
        const broker = engine.broker;
        if (broker) {
          try {
            account = await broker.queryAccount();
          } catch {
            // fall back to cached account if fresh query fails
          }
        }
        const positions = engine.getPositions();
        const rl = config.tradingAccount?.riskLimits as Record<string, unknown> | undefined;
        const maxPosPct = rl?.maxPositionPct;
        const posLimitPct = typeof maxPosPct === "number" && maxPosPct > 0 ? maxPosPct : 0.3;
        const maxOpenOrders =
          typeof rl?.maxOpenOrders === "number" && rl.maxOpenOrders > 0 ? rl.maxOpenOrders : 10;

        // Build a map of current position market values by symbol
        const posValueMap = new Map<string, number>();
        for (const p of positions) {
          posValueMap.set(p.symbol, Math.abs(p.marketValue ?? 0));
        }
        const totalAssets = account?.totalAssets ?? 0;

        // Track cumulative added value per symbol across the batch so
        // sequential orders for the same symbol are correctly limited.
        const batchAddedValue = new Map<string, number>();

        // Track open order count so we can skip orders that would exceed the limit.
        // Only count orders from the current broker type so paper orders don't
        // block real QMT orders and vice versa.
        const currentBrokerType = broker?.brokerType;
        const existingOpenCount = orderMgr
          ? (
              await orderMgr.getOrders({
                status: ["pending", "submitted", "partial"] as any,
                brokerType: currentBrokerType,
              })
            ).length
          : 0;
        let batchSubmitted = 0;

        // Track remaining available cash across the batch so we can pre-check
        // before submitting and auto-reduce quantity when funds are insufficient.
        let remainingCash = account?.cash ?? 0;

        const executed: Array<{
          code: string;
          name: string;
          quantity: number;
          price: number;
          orderId: string;
          status: string;
          conditionals: string[];
        }> = [];
        const rejected: Array<{ code: string; reason: string }> = [];

        // Sum of all batch-submitted order values so far (across all symbols).
        // Used to adjust effective totalAssets so the pre-check stays in sync
        // with the risk-gate's live view (pending orders freeze cash).
        let batchTotalValue = 0;

        for (const item of planItems) {
          // Pre-check pending order limit
          if (existingOpenCount + batchSubmitted >= maxOpenOrders) {
            rejected.push({
              code: item.code,
              reason: `当前挂单数 ${existingOpenCount + batchSubmitted} 已达上限 ${maxOpenOrders}，请等待现有订单成交或取消`,
            });
            continue;
          }

          // Pre-check available cash and auto-reduce quantity if needed.
          // When remainingCash is 0 but totalAssets > 0, the cached balance may
          // be stale — skip the pre-check and let OrderManager's RiskGate
          // (which does a fresh broker query) make the final call.
          let qty = item.quantity;
          const cashPreCheckReliable = remainingCash > 0;
          if (cashPreCheckReliable && item.limitPrice > 0) {
            const lotSize = getLotSize(item.code, item.market);
            const estimatedCommission = qty * item.limitPrice * 0.0003;
            const cashNeeded = qty * item.limitPrice + estimatedCommission;
            if (cashNeeded > remainingCash) {
              // Try to reduce quantity to fit remaining cash
              const maxAffordShares = Math.floor(remainingCash / (item.limitPrice * 1.0003));
              const maxAffordQty =
                lotSize > 1 ? Math.floor(maxAffordShares / lotSize) * lotSize : maxAffordShares;
              if (maxAffordQty <= 0) {
                rejected.push({
                  code: item.code,
                  reason: `资金不足。需要 ¥${cashNeeded.toFixed(2)}，可用现金 ¥${remainingCash.toFixed(2)}`,
                });
                continue;
              }
              qty = maxAffordQty;
            }
          }

          // Pre-check position limit and auto-reduce quantity if needed
          if (totalAssets > 0 && item.limitPrice > 0) {
            const currentValue =
              (posValueMap.get(item.code) ?? 0) + (batchAddedValue.get(item.code) ?? 0);
            // Use a 3% safety margin so borderline orders don't pass pre-check
            // but fail the risk-gate (which sees live account state with price drift).
            const effectiveLimit = posLimitPct - 0.03;
            const maxAllowedValue = totalAssets * effectiveLimit - currentValue;
            if (maxAllowedValue <= 0) {
              rejected.push({
                code: item.code,
                reason: `已有持仓占比 ${((currentValue / totalAssets) * 100).toFixed(1)}% 已达上限 ${(posLimitPct * 100).toFixed(0)}%，跳过`,
              });
              continue;
            }
            const maxShares = Math.floor(maxAllowedValue / item.limitPrice);
            const lotSize = getLotSize(item.code, item.market);
            const maxQty = lotSize > 1 ? Math.floor(maxShares / lotSize) * lotSize : maxShares;
            if (maxQty <= 0) {
              rejected.push({
                code: item.code,
                reason: `买入后持仓占比将超过上限 ${(posLimitPct * 100).toFixed(0)}%，最低 ${lotSize} 股亦超限，跳过`,
              });
              continue;
            }
            if (qty > maxQty) {
              qty = maxQty;
            }
          }

          const orderReq: OrderRequest = {
            symbol: item.code,
            market: item.market,
            side: "buy",
            orderType: item.orderType,
            quantity: qty,
            price: item.limitPrice,
            strategyId: "recommend-exec",
            signalId: `recommend_${Date.now()}_${item.code}`,
          };

          const submitResult = await orderMgr.submit(
            orderReq,
            forcePremarket ? { skipMarketHours: true } : undefined,
          );
          if (!submitResult.ok) {
            rejected.push({ code: item.code, reason: submitResult.reason });
            continue;
          }

          // Track submitted value and count for subsequent orders in this batch
          const addedVal = qty * item.limitPrice;
          batchAddedValue.set(item.code, (batchAddedValue.get(item.code) ?? 0) + addedVal);
          batchTotalValue += addedVal;
          remainingCash -= addedVal + addedVal * 0.0003;
          batchSubmitted++;

          const conditionalIds: string[] = [];
          if (createConditionalOrders) {
            try {
              if (item.stopLossPrice && item.stopLossPrice < item.limitPrice) {
                conditionalIds.push(
                  conditionalOrderService.addStopLoss({
                    symbol: item.code,
                    entryPrice: item.limitPrice,
                    stopPrice: item.stopLossPrice,
                    quantity: qty,
                    strategyId: "recommend-exec",
                    description: `自动止损(${item.code})`,
                  }),
                );
              }

              if (item.takeProfitPrice && item.takeProfitPrice > item.limitPrice) {
                conditionalIds.push(
                  conditionalOrderService.addTakeProfit({
                    symbol: item.code,
                    entryPrice: item.limitPrice,
                    targetPrice: item.takeProfitPrice,
                    quantity: Math.max(1, Math.floor(qty / 2)),
                    strategyId: "recommend-exec",
                    description: `自动止盈(${item.code})`,
                  }),
                );
              }

              conditionalIds.push(
                conditionalOrderService.addTrailingStop({
                  symbol: item.code,
                  entryPrice: item.limitPrice,
                  trailPct: trailingStopPct,
                  quantity: qty,
                  strategyId: "recommend-exec",
                  description: `自动跟踪止损(${item.code})`,
                }),
              );
            } catch (err) {
              rejected.push({
                code: item.code,
                reason: `条件单创建失败: ${(err as Error).message}`,
              });
            }
          }

          executed.push({
            code: item.code,
            name: item.name,
            quantity: qty,
            price: item.limitPrice,
            orderId: submitResult.order.orderId,
            status: submitResult.order.status,
            conditionals: conditionalIds,
          });
        }

        json(res, {
          status: "ok",
          dryRun: false,
          plan,
          execution: {
            submitted: executed.length,
            rejected: rejected.length,
            conditionalOrders: executed.reduce((sum, x) => sum + x.conditionals.length, 0),
            orders: executed,
            failures: rejected,
          },
        });
      } catch (err) {
        json(
          res,
          { error: `执行建仓失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Schedule strategy for MiniQMT (pre-market orders) ──────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/recommend-schedule-strategy",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const rawAllocs = Array.isArray(body.allocations) ? body.allocations : [];
        if (rawAllocs.length === 0) {
          json(res, { error: "缺少 allocations" }, 400);
          return;
        }

        const executePct = Math.min(100, Math.max(10, toFiniteNumber(body.execute_pct, 60)));
        const strategyName =
          typeof body.strategy_name === "string" ? body.strategy_name : "openclaw_scheduled";

        const engine = getEngine();
        const broker = engine.broker;
        if (!broker || broker.brokerType !== "qmt") {
          json(res, { error: "预埋策略仅支持 QMT 券商，请确保实盘引擎已启动并连接 QMT" }, 400);
          return;
        }

        // Build order list from allocations (same logic as recommend-execute)
        const orders: Array<{
          stockCode: string;
          name: string;
          volume: number;
          price: number;
          remark: string;
        }> = [];
        const skipped: Array<{ code: string; reason: string }> = [];

        const seenCodes = new Set<string>();
        for (const raw of rawAllocs as Array<Record<string, unknown>>) {
          const code = String(raw.code ?? "").trim();
          if (!code) continue;
          if (seenCodes.has(code)) {
            skipped.push({ code, reason: "同批次重复" });
            continue;
          }
          seenCodes.add(code);

          const name = String(raw.name ?? code);
          const sourceShares = clampInteger(raw.shares, 0, 10_000_000, 0);
          if (sourceShares <= 0) {
            skipped.push({ code, reason: "建议股数为 0" });
            continue;
          }

          const market = detectMarketFromSymbol(code);
          const lotSize = getLotSize(code, market);

          const entryPrices =
            raw.entryPrices && typeof raw.entryPrices === "object"
              ? (raw.entryPrices as Record<string, unknown>)
              : undefined;
          const batch1 =
            entryPrices?.batch1 && typeof entryPrices.batch1 === "object"
              ? (entryPrices.batch1 as Record<string, unknown>)
              : undefined;
          const rawBatchPct = toFiniteNumber(batch1?.pct, executePct);
          const batchPct = Math.min(100, Math.max(10, rawBatchPct));

          const targetShares = Math.max(1, Math.floor((sourceShares * batchPct) / 100));
          let quantity = lotSize > 1 ? Math.floor(targetShares / lotSize) * lotSize : targetShares;
          if (quantity <= 0 && sourceShares >= lotSize) {
            quantity = lotSize;
          }
          if (quantity <= 0) {
            skipped.push({ code, reason: "按手数取整后为 0" });
            continue;
          }

          const limitPrice = toFiniteNumber(batch1?.price, toFiniteNumber(raw.price, 0));
          if (limitPrice <= 0) {
            skipped.push({ code, reason: "无有效入场价格" });
            continue;
          }

          orders.push({
            stockCode: code,
            name,
            volume: quantity,
            price: round2(limitPrice),
            remark: `recommend_${code}`,
          });
        }

        if (orders.length === 0) {
          json(res, { error: "没有有效的委托可生成策略", skipped }, 400);
          return;
        }

        // Call QMT broker to generate and save the strategy file
        if (!("saveScheduledStrategy" in broker)) {
          json(res, { error: "当前 broker 不支持生成预埋策略" }, 400);
          return;
        }

        const result = await (
          broker as unknown as {
            saveScheduledStrategy: (
              orders: Array<{
                stockCode: string;
                name: string;
                volume: number;
                price: number;
                remark: string;
              }>,
              name?: string,
            ) => Promise<{
              filepath: string;
              filename: string;
              orderCount: number;
              strategyName: string;
            }>;
          }
        ).saveScheduledStrategy(orders, strategyName);

        json(res, {
          status: "ok",
          strategy: result,
          orders: orders.map((o) => ({
            code: o.stockCode,
            name: o.name,
            quantity: o.volume,
            price: o.price,
          })),
          skipped,
          message: `预埋策略已生成，共 ${orders.length} 笔委托。请在 MiniQMT 中加载策略文件运行。`,
        });
      } catch (err) {
        json(
          res,
          { error: `生成预埋策略失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Conditional Orders Management API ───────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/conditional-orders",
    handler: secure(async (req, res) => {
      if (req.method === "GET") {
        const activeOnly =
          new URL(req.url ?? "/", "http://localhost").searchParams.get("active") !== "false";
        const orders = conditionalOrderService.listOrders(activeOnly);
        json(res, { orders, activeCount: conditionalOrderService.activeCount });
        return;
      }

      if (req.method === "DELETE") {
        const body = await parseObjectBody(req);
        const id = String(body.id ?? "").trim();
        if (!id) {
          json(res, { error: "缺少条件单 id" }, 400);
          return;
        }
        const ok = conditionalOrderService.removeOrder(id);
        json(res, { ok, id });
        return;
      }

      if (req.method === "POST") {
        const body = await parseObjectBody(req);
        const action = String(body.action ?? "").trim();

        if (action === "check_now") {
          const triggered = await conditionalOrderService.checkAll();
          json(res, { triggered, count: triggered.length });
          return;
        }

        if (action === "add") {
          const type = String(body.type ?? "").trim();
          const symbol = String(body.symbol ?? "").trim();
          if (!symbol) {
            json(res, { error: "缺少 symbol" }, 400);
            return;
          }
          const entryPrice = toFiniteNumber(body.entryPrice, 0);
          const quantity = clampInteger(body.quantity, 1, 10_000_000, 100);

          let orderId: string;
          if (type === "stop_loss") {
            orderId = conditionalOrderService.addStopLoss({
              symbol,
              entryPrice,
              stopPrice: toFiniteNumber(body.stopPrice, 0),
              quantity,
              strategyId: String(body.strategyId ?? "manual"),
              description: String(body.description ?? `手动止损(${symbol})`),
            });
          } else if (type === "take_profit") {
            orderId = conditionalOrderService.addTakeProfit({
              symbol,
              entryPrice,
              targetPrice: toFiniteNumber(body.targetPrice, 0),
              quantity,
              strategyId: String(body.strategyId ?? "manual"),
              description: String(body.description ?? `手动止盈(${symbol})`),
            });
          } else if (type === "trailing_stop") {
            orderId = conditionalOrderService.addTrailingStop({
              symbol,
              entryPrice,
              trailPct: Math.min(0.3, Math.max(0.01, toFiniteNumber(body.trailPct, 0.08))),
              quantity,
              strategyId: String(body.strategyId ?? "manual"),
              description: String(body.description ?? `手动跟踪止损(${symbol})`),
            });
          } else {
            json(res, { error: "type 必须为 stop_loss / take_profit / trailing_stop" }, 400);
            return;
          }
          json(res, { ok: true, id: orderId });
          return;
        }

        json(res, { error: "action 必须为 check_now / add" }, 400);
        return;
      }

      json(res, { error: "仅支持 GET / POST / DELETE" }, 405);
    }),
  });

  // ── Engine Status API ───────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/engine-status",
    handler: secure(async (_req, res) => {
      try {
        const engine = getEngine();
        const status = engine.getStatus();
        const isRunning = status === "running";
        const orderMgr = engine.orderManager;
        // Prefer a fresh broker query for positions so currentPrice/PnL are up-to-date;
        // fall back to cached positions if the fresh query fails.
        let positions = engine.getPositions();
        const broker = engine.broker;
        if (broker) {
          try {
            positions = await broker.queryPositions();
          } catch {
            // keep cached
          }
        }
        const pendingOrders = orderMgr
          ? await orderMgr.getOrders({ status: ["pending", "submitted", "partial"] })
          : [];
        const currentBrokerType = engine.broker?.brokerType;
        const connHealth = engine.getConnectionHealth();

        json(res, {
          mode: status,
          isRunning,
          broker: engine.broker ? "connected" : "disconnected",
          brokerType: currentBrokerType ?? null,
          lastError: connHealth.lastError ?? null,
          positionCount: positions.length,
          positions: positions.slice(0, 50),
          pendingOrderCount: pendingOrders.length,
          pendingOrderCountByBroker: currentBrokerType
            ? pendingOrders.filter((o) => o.brokerType === currentBrokerType).length
            : pendingOrders.length,
          pendingOrders: pendingOrders.slice(0, 50),
          conditionalOrderCount: conditionalOrderService.activeCount,
        });
      } catch (err) {
        json(res, {
          mode: "unknown",
          isRunning: false,
          error: err instanceof Error ? err.message : String(err),
        });
      }
    }),
  });

  // ── Order Fill Status API (poll after placing orders) ────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/order-fill-status",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const orderIds = Array.isArray(body.orderIds) ? body.orderIds.map(String) : [];
        if (orderIds.length === 0) {
          json(res, { error: "缺少 orderIds" }, 400);
          return;
        }

        const engine = getEngine();
        const orderMgr = engine.orderManager;
        if (!orderMgr) {
          json(res, { error: "实盘引擎未启动" }, 400);
          return;
        }

        const allOrders = await orderMgr.getOrders({});
        const positions = engine.getPositions();
        const posMap = new Map(positions.map((p) => [p.symbol, p]));

        const results = orderIds.map((id: string) => {
          const order = allOrders.find((o) => o.orderId === id);
          if (!order) return { orderId: id, status: "unknown" as const, hasPosition: false };
          const pos = posMap.get(order.symbol);
          return {
            orderId: id,
            symbol: order.symbol,
            status: order.status,
            quantity: order.quantity,
            filledQty: order.filledQty ?? 0,
            avgFillPrice: order.avgFillPrice ?? null,
            hasPosition: pos != null && (pos.quantity ?? 0) > 0,
            positionQty: pos?.quantity ?? 0,
          };
        });

        const allFilled = results.every(
          (r) => r.status === "filled" || r.status === "partial" || r.hasPosition,
        );
        const anyFilled = results.some(
          (r) => r.status === "filled" || r.status === "partial" || r.hasPosition,
        );

        json(res, { orders: results, allFilled, anyFilled });
      } catch (err) {
        json(
          res,
          { error: `查询订单状态失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // Daily Composite Picks (extracted to routes/daily-pick.ts)
  registerDailyPickRoutes(deps);

  // ── Event Catalyst API ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/event-catalyst",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const action = String(body.action ?? "scan");
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/event_catalyst_engine.py",
          {
            action,
            us_symbols: body.us_symbols,
            themes: body.themes,
            min_change_pct: toFiniteNumber(body.min_change_pct, 2.0),
            require_fund_flow: body.require_fund_flow ?? true,
            limit: clampInteger(body.limit, 1, 50, 20),
            top_n: clampInteger(body.top_n, 1, 30, 10),
            us_symbol: body.us_symbol,
          },
          config,
          { timeoutMs: 120_000 },
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "事件催化引擎执行失败" }, 500);
          return;
        }
        json(res, { status: "ok", ...result.data });
      } catch (err) {
        json(
          res,
          { error: `事件催化分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Scan History API (全局扫描历史) ─────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/scan-history",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const historyId = url.searchParams.get("id");

      if (req.method === "POST") {
        try {
          const body = await parseObjectBody(req);
          const { appendScanHistory } = await import("./scan-history.js");
          const entry = await appendScanHistory(
            config,
            (body.data as Record<string, unknown>) ?? {},
          );
          json(res, { status: "ok", entry });
        } catch (err) {
          json(
            res,
            { error: `全局扫描快照保存失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      if (req.method === "DELETE" && historyId) {
        try {
          const { handleDeleteScanHistory } = await import("./scan-history.js");
          json(res, await handleDeleteScanHistory(config, historyId));
        } catch (err) {
          json(
            res,
            { error: `全局扫描快照删除失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      try {
        const { handleGetScanHistory, handleGetScanHistoryById } =
          await import("./scan-history.js");
        if (historyId) {
          json(res, await handleGetScanHistoryById(config, historyId));
        } else {
          json(res, await handleGetScanHistory(config));
        }
      } catch (err) {
        json(
          res,
          { error: `全局扫描快照读取失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── US Mover History API ───────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/us-mover-history",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const historyId = url.searchParams.get("id");

      if (req.method === "POST") {
        try {
          const body = await parseObjectBody(req);
          const { appendUsMoverHistory } = await import("./us-mover-history.js");
          const entry = await appendUsMoverHistory(
            config,
            (body.data as Record<string, unknown>) ?? {},
          );
          json(res, { status: "ok", entry });
        } catch (err) {
          json(
            res,
            { error: `美股异动快照保存失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      if (req.method === "DELETE" && historyId) {
        try {
          const { handleDeleteUsMoverHistory } = await import("./us-mover-history.js");
          json(res, await handleDeleteUsMoverHistory(config, historyId));
        } catch (err) {
          json(
            res,
            { error: `美股异动快照删除失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      try {
        const { handleGetUsMoverHistory, handleGetUsMoverHistoryById } =
          await import("./us-mover-history.js");
        if (historyId) {
          json(res, await handleGetUsMoverHistoryById(config, historyId));
        } else {
          json(res, await handleGetUsMoverHistory(config));
        }
      } catch (err) {
        json(
          res,
          { error: `美股异动快照读取失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Sentiment Signal API ───────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/sentiment-signal",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const action = String(body.action ?? "analyze");
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/sentiment_signal_engine.py",
          {
            action,
            symbol: body.symbol,
            symbols: body.symbols,
            news: body.news,
            limit: clampInteger(body.limit, 1, 50, 20),
          },
          config,
          { timeoutMs: 120_000 },
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "情绪信号引擎执行失败" }, 500);
          return;
        }
        json(res, { status: "ok", ...result.data });
      } catch (err) {
        json(
          res,
          { error: `情绪信号分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Institution Activity APIs ─────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/institution-activity/scan",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const rawSymbols = body.symbols;
        let symbols: string[] | undefined;
        if (typeof rawSymbols === "string" && rawSymbols.trim()) {
          symbols = rawSymbols
            .split(/[\s,，]+/)
            .map((s) => s.trim())
            .filter(Boolean);
        } else if (Array.isArray(rawSymbols)) {
          symbols = rawSymbols.map((s) => String(s).trim()).filter(Boolean);
        }
        const result = await runPythonScript(
          "scripts/institution_activity.py",
          {
            action: "scan",
            symbols,
            as_of_date: body.as_of_date ?? body.asOfDate,
            lookback_days: body.lookback_days ?? body.lookbackDays ?? 10,
            min_confidence: body.min_confidence ?? body.minConfidence ?? 0,
            pre_accum_days: body.pre_accum_days ?? body.preAccumDays ?? 10,
            pre_accum_count_min: body.pre_accum_count_min ?? body.preAccumCountMin ?? 2,
            pre_accum_volume_ratio: body.pre_accum_volume_ratio ?? body.preAccumVolumeRatio ?? 1.5,
            rush_change_pct: body.rush_change_pct ?? body.rushChangePct ?? 5,
            rush_volume_ratio: body.rush_volume_ratio ?? body.rushVolumeRatio ?? 2,
            rush_turnover_pct: body.rush_turnover_pct ?? body.rushTurnoverPct ?? 3,
            market_scope: body.market_scope ?? body.marketScope ?? "auto",
            include_low_freq_confirmers:
              body.include_low_freq_confirmers ?? body.includeLowFreqConfirmers ?? false,
          },
          config,
          { timeoutMs: 300_000 },
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "机构行为扫描失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `机构行为扫描失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/institution-activity/detail",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const symbol = String(body.symbol ?? "").trim();
        if (!symbol) {
          json(res, { error: "缺少 symbol 参数" }, 400);
          return;
        }
        const result = await runPythonScript(
          "scripts/institution_activity.py",
          {
            action: "detail",
            symbol,
            as_of_date: body.as_of_date ?? body.asOfDate,
            lookback_days: body.lookback_days ?? body.lookbackDays ?? 10,
            pre_accum_days: body.pre_accum_days ?? body.preAccumDays ?? 10,
            pre_accum_count_min: body.pre_accum_count_min ?? body.preAccumCountMin ?? 2,
            pre_accum_volume_ratio: body.pre_accum_volume_ratio ?? body.preAccumVolumeRatio ?? 1.5,
            rush_change_pct: body.rush_change_pct ?? body.rushChangePct ?? 5,
            rush_volume_ratio: body.rush_volume_ratio ?? body.rushVolumeRatio ?? 2,
            rush_turnover_pct: body.rush_turnover_pct ?? body.rushTurnoverPct ?? 3,
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "机构行为详情加载失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(
          res,
          { error: `机构行为详情加载失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/institution-activity/history",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const historyId = url.searchParams.get("id");

      if (req.method === "POST") {
        try {
          const body = await parseObjectBody(req);
          const { appendInstitutionActivityHistory } = await import("./institution-history.js");
          await appendInstitutionActivityHistory(
            config,
            (body.scan as Record<string, unknown>) ?? {},
            (body.params as Record<string, unknown>) ?? {},
          );
          json(res, { status: "ok" });
        } catch (err) {
          json(
            res,
            { error: `机构行为快照保存失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      if (req.method === "DELETE" && historyId) {
        try {
          const { handleDeleteInstitutionActivityHistoryApi } =
            await import("./institution-history.js");
          json(res, await handleDeleteInstitutionActivityHistoryApi(config, historyId));
        } catch (err) {
          json(
            res,
            { error: `机构行为快照删除失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }

      if (req.method === "PATCH" && historyId) {
        try {
          const body = await parseObjectBody(req);
          const { handlePatchInstitutionActivityHistoryApi } =
            await import("./institution-history.js");
          json(
            res,
            await handlePatchInstitutionActivityHistoryApi(
              config,
              historyId,
              (body as Record<string, unknown>) ?? {},
            ),
          );
        } catch (err) {
          json(
            res,
            {
              error: `机构行为快照更新失败: ${err instanceof Error ? err.message : String(err)}`,
            },
            500,
          );
        }
        return;
      }

      try {
        const {
          handleGetInstitutionActivityHistoryApi,
          handleGetInstitutionActivityHistoryByIdApi,
        } = await import("./institution-history.js");
        if (historyId) {
          json(res, await handleGetInstitutionActivityHistoryByIdApi(config, historyId));
        } else {
          json(res, await handleGetInstitutionActivityHistoryApi(config));
        }
      } catch (err) {
        json(
          res,
          { error: `机构行为快照读取失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Pool Optimizer API ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/optimize-pool",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const capital = toFiniteNumber(body.capital ?? body.initial_capital, 500000);
        const startDate = String(body.start_date ?? body.startDate ?? "2025-06-01");
        const endDate = body.end_date ?? body.endDate;
        const maxRounds = toFiniteNumber(body.max_rounds ?? body.maxRounds, 12);
        const autoScreen = !!(body.auto_screen ?? body.autoScreen ?? false);

        const result = await runPythonScript(
          "scripts/pool_optimizer.py",
          {
            capital,
            start_date: startDate,
            end_date: endDate,
            max_rounds: maxRounds,
            auto_screen: autoScreen,
          },
          config,
          { timeoutMs: 600_000 },
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "优化失败" }, 500);
          return;
        }
        const data = result.data as Record<string, unknown>;

        // Auto-save to optimization history
        if (optimizationStore && data.best) {
          try {
            const savedId = await optimizationStore.save({
              params: {
                capital,
                startDate,
                endDate: endDate ? String(endDate) : undefined,
                maxRounds,
                autoScreen,
              },
              totalRounds: toFiniteNumber(data.totalRounds, 0),
              best: data.best as never,
              recommendText: data.recommendText as string | undefined,
              screeningSummary: data.screeningSummary as never,
              rounds: (data.rounds ?? []) as never[],
            });
            (data as Record<string, unknown>).savedOptId = savedId;
          } catch {
            // Best-effort save — don't fail the request
          }
        }

        json(res, data);
      } catch (err) {
        json(res, { error: `优化失败: ${err instanceof Error ? err.message : String(err)}` }, 500);
      }
    }),
  });

  // ── Optimization History API ───────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/optimize-history",
    handler: secure(async (req, res) => {
      if (!optimizationStore) {
        json(res, { error: "优化历史未启用" }, 503);
        return;
      }
      const url = new URL(req.url ?? "/", "http://localhost");
      const id = url.searchParams.get("id");

      if (req.method === "DELETE" && id) {
        const ok = await optimizationStore.delete(id);
        json(res, ok ? { status: "ok" } : { error: "未找到记录" }, ok ? 200 : 404);
        return;
      }

      if (req.method === "PATCH" && id) {
        const body = await parseObjectBody(req);
        if (body.action === "star") {
          const ok = await optimizationStore.toggleStar(id);
          json(res, ok ? { status: "ok" } : { error: "未找到记录" }, ok ? 200 : 404);
        } else if (body.notes !== undefined) {
          const ok = await optimizationStore.setNotes(id, String(body.notes));
          json(res, ok ? { status: "ok" } : { error: "未找到记录" }, ok ? 200 : 404);
        } else {
          json(res, { error: "不支持的操作" }, 400);
        }
        return;
      }

      // GET — with id: full detail; without: list
      if (id) {
        const record = optimizationStore.getFullResult(id);
        if (!record) {
          json(res, { error: "未找到记录" }, 404);
          return;
        }
        json(res, record);
      } else {
        const limit = clampInteger(url.searchParams.get("limit"), 1, 200, 50);
        const offset = clampInteger(url.searchParams.get("offset"), 0, 10000, 0);
        const entries = optimizationStore.query({ limit, offset });
        json(res, { entries, total: optimizationStore.size });
      }
    }),
  });

  // ── Backtest API ───────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/backtest",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        // Support both camelCase and snake_case field names
        const rawSymbols = body.symbols ?? body.custom_symbols ?? body.customSymbols;
        let symbolList: string[] | undefined;
        if (typeof rawSymbols === "string" && rawSymbols.trim()) {
          symbolList = rawSymbols
            .split(/[,\s]+/)
            .map((s: string) => s.trim())
            .filter(Boolean);
        } else if (Array.isArray(rawSymbols)) {
          symbolList = rawSymbols;
        }
        // Resolve composed strategy if requested (single backtest)
        let strategy = body.strategy ?? "momentum";
        let subStrategies: string[] | undefined;
        let compositionMethod: string | undefined;
        const composedId = body.composed_strategy_id ?? body.composedStrategyId;
        if (composedId && composedStrategyStore) {
          const comp = composedStrategyStore.list().find((c) => c.id === composedId);
          if (comp) {
            subStrategies = comp.strategies.map((s) => s.strategyId);
            compositionMethod = comp.method;
            strategy = "composed";
          }
        }

        // For compare actions, include all composed strategies automatically
        let composedStrategiesPayload:
          | Array<{
              id: string;
              name: string;
              sub_strategies: string[];
              composition_method: string;
            }>
          | undefined;
        const action = body.action ?? "run";
        if (action === "compare" && composedStrategyStore) {
          const allComps = composedStrategyStore.list();
          if (allComps.length > 0) {
            composedStrategiesPayload = allComps.map((c) => ({
              id: c.id,
              name: c.name,
              sub_strategies: c.strategies.map((s) => s.strategyId),
              composition_method: c.method,
            }));
          }
        }

        const result = await runPythonScript(
          "scripts/backtest_engine.py",
          {
            action,
            initial_capital: body.initial_capital ?? body.initialCapital ?? 500000,
            start_date: body.start_date ?? body.startDate ?? "2025-01-01",
            end_date: body.end_date ?? body.endDate ?? "2025-12-31",
            strategy,
            strategies: body.strategies,
            sub_strategies: subStrategies,
            composition_method: compositionMethod,
            composed_strategies: composedStrategiesPayload,
            risk_tolerance: body.risk_tolerance ?? body.riskTolerance ?? "medium",
            symbols: symbolList,
            rebalance_days: body.rebalance_days ?? body.rebalanceDays ?? 14,
            commission_rate: body.commission_rate ?? body.commissionRate ?? 0.0003,
            stamp_tax: body.stamp_tax ?? body.stampTax ?? 0.001,
          },
          config,
        );
        if (!result.ok) {
          json(res, { error: result.error ?? "回测失败" }, 500);
          return;
        }
        json(res, result.data);
      } catch (err) {
        json(res, { error: `回测失败: ${err instanceof Error ? err.message : String(err)}` }, 500);
      }
    }),
  });

  // ── Services Status ────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/services",
    handler: secure(async (_req, res) => {
      const engine = getEngine();

      // Gateway info
      const gateway = {
        running: true,
        pid: process.pid,
        port: Number(process.env["OPENCLAW_GATEWAY_PORT"] || 18789),
        version: process.env["OPENCLAW_SERVICE_VERSION"] || "unknown",
        uptime: formatUptime(process.uptime()),
      };

      // Receiver status
      const receiver = receiverService?.getStatus() ?? {
        running: false,
        pid: null,
        port: config.receiver?.port ?? 58088,
        host: config.receiver?.host ?? "0.0.0.0",
        restartCount: 0,
        startedAt: null,
      };

      // Bridge health (fetch from RECEIVER's /health endpoint)
      let bridgeHealth = null;
      if (receiver.running) {
        try {
          const resp = await fetch(`http://127.0.0.1:${receiver.port}/health`, {
            signal: AbortSignal.timeout(2_000),
          });
          if (resp.ok) bridgeHealth = await resp.json();
        } catch {
          // RECEIVER not responding
        }
      }

      // Engine status
      const engineStatus = {
        status: engine.getStatus(),
        activeStrategies: engine.strategyRegistry?.list?.(true)?.length ?? 0,
        positions: engine.getPositions()?.length ?? 0,
      };

      const tickIngress = deps.tickIngressService?.getStatus() ?? { running: false };
      const tickStrategies = deps.tickStrategyService?.getStatus() ?? { running: false };

      // Other registered service IDs
      const otherServices = [
        "data-sync",
        "signal-monitor",
        "pnl-monitor",
        "options-monitor",
        "task-scheduler",
        "price-alert",
        "equity-history",
        "quant-storage",
        "alert-store",
        ...(deps.tickIngressService ? ["qmt-tick-ingress"] : []),
        ...(deps.tickStrategyService ? ["qmt-tick-strategies"] : []),
      ].map((id) => ({ id }));

      json(res, {
        gateway,
        receiver,
        bridgeHealth,
        engine: engineStatus,
        tickIngress,
        tickStrategies,
        services: otherServices,
      });
    }),
  });

  // ── Service Control ────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/receiver/start",
    handler: secure(async (_req, res) => {
      if (!receiverService) {
        json(res, { status: "error", message: "RECEIVER 服务未配置" }, 400);
        return;
      }
      await receiverService.start();
      json(res, { status: "ok", message: "RECEIVER 已启动" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/receiver/stop",
    handler: secure(async (_req, res) => {
      if (!receiverService) {
        json(res, { status: "error", message: "RECEIVER 服务未配置" }, 400);
        return;
      }
      await receiverService.stop();
      json(res, { status: "ok", message: "RECEIVER 已停止" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/receiver/restart",
    handler: secure(async (_req, res) => {
      if (!receiverService) {
        json(res, { status: "error", message: "RECEIVER 服务未配置" }, 400);
        return;
      }
      await receiverService.restart();
      json(res, { status: "ok", message: "RECEIVER 已重启" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/engine/start",
    handler: secure(async (_req, res) => {
      const engine = getEngine();
      await engine.start();
      json(res, { status: "ok", message: "实盘引擎已启动" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/engine/stop",
    handler: secure(async (_req, res) => {
      const engine = getEngine();
      await engine.stop();
      json(res, { status: "ok", message: "实盘引擎已停止" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/engine/pause",
    handler: secure(async (_req, res) => {
      const engine = getEngine();
      engine.pause();
      json(res, { status: "ok", message: "实盘引擎已暂停" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/engine/resume",
    handler: secure(async (_req, res) => {
      const engine = getEngine();
      engine.resume();
      json(res, { status: "ok", message: "实盘引擎已恢复" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/tick-ingress/start",
    handler: secure(async (_req, res) => {
      const svc = deps.tickIngressService;
      if (!svc) {
        json(res, { status: "error", message: "Tick Ingress 服务未配置" }, 400);
        return;
      }
      await svc.start();
      json(res, { status: "ok", message: "Tick Ingress 已启动" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/tick-ingress/stop",
    handler: secure(async (_req, res) => {
      const svc = deps.tickIngressService;
      if (!svc) {
        json(res, { status: "error", message: "Tick Ingress 服务未配置" }, 400);
        return;
      }
      await svc.stop();
      json(res, { status: "ok", message: "Tick Ingress 已停止" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/tick-ingress/restart",
    handler: secure(async (_req, res) => {
      const svc = deps.tickIngressService;
      if (!svc) {
        json(res, { status: "error", message: "Tick Ingress 服务未配置" }, 400);
        return;
      }
      await svc.stop();
      await svc.start();
      json(res, { status: "ok", message: "Tick Ingress 已重启" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/tick-strategy/start",
    handler: secure(async (_req, res) => {
      const svc = deps.tickStrategyService;
      if (!svc) {
        json(res, { status: "error", message: "Tick Strategy 服务未配置" }, 400);
        return;
      }
      await svc.start();
      json(res, { status: "ok", message: "Tick Strategy 已启动" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/tick-strategy/stop",
    handler: secure(async (_req, res) => {
      const svc = deps.tickStrategyService;
      if (!svc) {
        json(res, { status: "error", message: "Tick Strategy 服务未配置" }, 400);
        return;
      }
      await svc.stop();
      json(res, { status: "ok", message: "Tick Strategy 已停止" });
    }),
  });

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/service/tick-strategy/restart",
    handler: secure(async (_req, res) => {
      const svc = deps.tickStrategyService;
      if (!svc) {
        json(res, { status: "error", message: "Tick Strategy 服务未配置" }, 400);
        return;
      }
      await svc.stop();
      await svc.start();
      json(res, { status: "ok", message: "Tick Strategy 已重启" });
    }),
  });

  // ── Data Management ────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/data",
    handler: secure(async (_req, res) => {
      const warehouseStats = await dataWarehouse.getStats();

      // Query Python-side SQLite cache stats (the real data store)
      let pythonCache: Record<string, unknown> = {};
      let pythonSymbols: Array<{
        symbol: string;
        interval: string;
        bars: number;
        dateRange: { min: string; max: string };
      }> = [];
      try {
        const [statsResult, detailResult] = await Promise.all([
          runPythonScript<Record<string, unknown>>(
            "cache/stock_cache.py",
            { action: "stats" },
            config,
          ),
          runPythonScript<
            Array<{
              symbol: string;
              interval: string;
              bars: number;
              dateRange: { min: string; max: string };
            }>
          >("cache/stock_cache.py", { action: "symbols_detail" }, config),
        ]);
        if (statsResult.ok && statsResult.data) pythonCache = statsResult.data;
        if (detailResult.ok && detailResult.data) pythonSymbols = detailResult.data;
      } catch {
        // Python cache unavailable; fall through with empty data
      }

      json(res, {
        provider: config.dataProvider ?? "smart",
        pythonPath: config.pythonPath ?? "python3",
        availableProviders: DATA_PROVIDERS,
        receiverPort: config.receiver?.port ?? 58088,
        receiverHost: config.receiver?.host ?? "0.0.0.0",
        receiverEnabled: config.receiver?.enabled !== false,
        warehouse: warehouseStats,
        pythonCache,
        pythonSymbols,
      });
    }),
  });

  // ── Strategy Management ────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategies",
    handler: secure(async (_req, res) => {
      // Built-in strategies from registry
      const builtin = listStrategies().map((s) => ({
        id: s.id,
        name: s.name,
        description: s.description,
        engine: s.engine,
        parameters: s.parameters?.map((p) => ({
          name: p.name,
          type: p.type,
          default: p.default,
          description: p.description,
          min: p.min,
          max: p.max,
        })),
      }));

      // User-defined strategies
      const userStrategies = userStrategyStore
        .list()
        .map((s: import("../services/strategy-template.js").UserStrategy) => ({
          id: s.id,
          name: s.name,
          description: s.description,
          strategyId: s.strategyId,
          parameters: s.parameters,
          symbols: s.symbols,
          tags: s.tags,
          starred: s.starred,
          createdAt: s.createdAt,
          updatedAt: s.updatedAt,
        }));

      // Strategy templates
      let templates: unknown[] = [];
      try {
        const mod = await import("../services/strategy-template.js");
        templates = (mod.STRATEGY_TEMPLATES ?? []).map(
          (t: (typeof mod.STRATEGY_TEMPLATES)[number]) => ({
            id: t.id,
            name: t.name,
            description: t.description,
            baseStrategyId: t.baseStrategyId,
            category: t.category,
            suggestedParams: t.suggestedParams,
            suggestedSymbols: t.suggestedSymbols,
            notes: t.notes,
          }),
        );
      } catch {
        // templates unavailable
      }

      json(res, {
        builtin,
        userStrategies,
        templates,
        totalBuiltin: builtin.length,
        totalUser: userStrategies.length,
        totalTemplates: templates.length,
      });
    }),
  });

  // ── Cache-first K-line Query ────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/query-klines",
    handler: secure(async (req, res) => {
      const params = new URL(req.url ?? "", "http://localhost").searchParams;
      const symbol = params.get("symbol");
      const interval = params.get("interval") ?? "1d";
      const start = params.get("start");
      const end = params.get("end") ?? new Date().toISOString().slice(0, 10);
      if (!symbol) {
        json(res, { status: "error", message: "symbol required" }, 400);
        return;
      }
      const startDate =
        start ||
        (() => {
          const d = new Date();
          d.setMonth(d.getMonth() - 6);
          return d.toISOString().slice(0, 10);
        })();

      // Map UI intervals to Python cache period names
      const periodMap: Record<string, string> = {
        "1d": "daily",
        "1h": "60min",
        "30m": "30min",
        "15m": "15min",
        "5m": "5min",
      };
      const period = periodMap[interval] ?? interval;

      // 1) Try Python cache first
      try {
        const cacheResult = await runPythonScript<{
          bars: Array<{
            timestamp: number;
            open: number;
            high: number;
            low: number;
            close: number;
            volume: number;
            amount: number;
          }>;
          count: number;
          cached_start: string;
          cached_end: string;
        }>(
          "cache/stock_cache.py",
          {
            action: "query_klines",
            symbol,
            period,
            start_date: startDate,
            end_date: end,
          },
          config,
        );

        if (cacheResult.ok && cacheResult.data && cacheResult.data.count > 0) {
          json(res, {
            status: "ok",
            source: "cache",
            data: cacheResult.data.bars,
            count: cacheResult.data.count,
            dateRange: { start: cacheResult.data.cached_start, end: cacheResult.data.cached_end },
          });
          return;
        }
      } catch {
        // Cache query failed, fall through to online
      }

      // 2) Fallback: fetch online via provider
      try {
        const bars = await dataWarehouse.getKlines(symbol, interval as "1d", startDate, end);
        json(res, { status: "ok", source: "online", data: bars, count: bars.length });
      } catch (err) {
        json(res, { status: "error", message: (err as Error).message }, 500);
      }
    }),
  });

  // ── Cache Invalidation ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/data-warehouse/invalidate",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        res.writeHead(405);
        res.end();
        return;
      }
      const body = await parseObjectBody(req);
      const symbol = body.symbol as string | undefined;
      const interval = body.interval as KlineInterval | undefined;
      const result = await dataWarehouse.invalidate(symbol, interval);
      json(res, { status: "ok", ...result });
    }),
  });

  // ── Data Warmup (admin convenience) ────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/data-warehouse/warmup",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        res.writeHead(405);
        res.end();
        return;
      }
      const body = await parseObjectBody(req);
      const symbols = (body.symbols as string[]) ?? [];
      const interval = (body.interval as string) ?? "1d";
      const start = (body.start as string) ?? "";
      const end = (body.end as string) ?? "";
      if (!symbols.length || !start || !end) {
        json(res, { status: "error", message: "symbols, start, end 必填" }, 400);
        return;
      }
      const results = await dataWarehouse.warmup(symbols, interval as never, start, end);
      json(res, { status: "ok", results });
    }),
  });

  // ── Batch Sync (bridge / akshare) ─────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/data-warehouse/sync",
    handler: secure(async (req, res) => {
      if (req.method === "GET") {
        // GET → check what needs updating (dry-run)
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/batch_sync.py",
          { action: "check" },
          config,
          { maxRetries: 0, timeoutMs: 5 * 60_000 },
        );
        json(res, result.ok ? result.data : { status: "error", message: result.error });
        return;
      }
      if (req.method !== "POST") {
        res.writeHead(405);
        res.end();
        return;
      }
      const body = await parseObjectBody(req);
      const source = (body.source as string) ?? "auto";
      const symbols = (body.symbols as string[]) ?? [];
      const maxStocks = (body.max_stocks as number) ?? 0;
      const days = (body.days as number) ?? 120;

      const result = await runPythonScript<Record<string, unknown>>(
        "scripts/batch_sync.py",
        { action: "sync", source, symbols, max_stocks: maxStocks, days },
        config,
        { maxRetries: 0, timeoutMs: 10 * 60_000 },
      );
      json(res, result.ok ? result.data : { status: "error", message: result.error });
    }),
  });

  // ── Bridge Status ─────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/data-warehouse/bridge-status",
    handler: secure(async (_req, res) => {
      const result = await runPythonScript<Record<string, unknown>>(
        "scripts/batch_sync.py",
        { action: "status" },
        config,
        { maxRetries: 0, timeoutMs: 3 * 60_000 },
      );
      json(res, result.ok ? result.data : { status: "error", message: result.error });
    }),
  });

  // ── Config View (read-only) ────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/config",
    handler: secure((_req, res) => {
      // Expose a sanitized config snapshot (hide sensitive fields)
      const safeConfig = { ...config };
      if (safeConfig.apiToken) safeConfig.apiToken = "***";
      if ((safeConfig as Record<string, unknown>).tushareToken)
        (safeConfig as Record<string, unknown>).tushareToken = "***";
      json(res, safeConfig);
    }),
  });

  // ── Alerts Summary (admin-specific enriched version) ───────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/alerts",
    handler: secure((req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const severity = url.searchParams.get("severity") as
        | "info"
        | "warning"
        | "critical"
        | undefined;
      const limit = Number(url.searchParams.get("limit") ?? "100");
      const alerts = alertStore.query({ severity: severity ?? undefined, limit });
      const summary = alertStore.getSummary();
      json(res, { alerts, summary });
    }),
  });

  // ── Log Viewer ──────────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/logs",
    handler: secure((req, res) => {
      if (req.method === "DELETE") {
        logStore.clear();
        json(res, { ok: true });
        return;
      }
      const url = new URL(req.url ?? "/", "http://localhost");
      const level = url.searchParams.get("level") ?? undefined;
      const source = url.searchParams.get("source") ?? undefined;
      const search = url.searchParams.get("search") ?? undefined;
      const since = url.searchParams.get("since") ?? undefined;
      const limit = Number(url.searchParams.get("limit") ?? "200");
      const entries = logStore.query({
        level: level as "info" | "warn" | "error" | "debug" | undefined,
        source,
        search,
        since,
        limit,
      });
      const summary = logStore.getSummary();
      json(res, { entries, summary, total: logStore.size });
    }),
  });

  // ── Recent Events ─────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/events",
    handler: secure((_req, res) => {
      const recent = eventBus.getRecentEvents(undefined, 100);
      json(res, { events: recent, sseClients: eventBus.clientCount });
    }),
  });

  // ── Price Alerts List ──────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/price-alerts",
    handler: secure((_req, res) => {
      const alerts = priceAlertService.listAlerts();
      json(res, {
        alerts,
        activeCount: priceAlertService.activeCount,
        total: alerts.length,
      });
    }),
  });

  // ── Scheduled Tasks List ───────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/scheduled-tasks",
    handler: secure((_req, res) => {
      const pending = taskScheduler.list("pending");
      const completed = taskScheduler.list("completed");
      const failed = taskScheduler.list("failed");
      json(res, {
        pending: pending.map(simplifyTask),
        completed: completed.map(simplifyTask),
        failed: failed.map(simplifyTask),
        pendingCount: taskScheduler.pendingCount,
      });
    }),
  });

  // ── Overall System Summary ─────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/overview",
    handler: secure(async (_req, res) => {
      const engine = getEngine();
      const alertSummary = alertStore.getSummary();
      const receiver = receiverService?.getStatus() ?? { running: false };

      // Query Python-side cache for real data stats
      let symbolCount = 0;
      let totalBars = 0;
      let hitRate = "N/A";
      try {
        const statsResult = await runPythonScript<{
          klines?: { total_bars?: number; unique_symbols?: number };
        }>("cache/stock_cache.py", { action: "stats" }, config);
        if (statsResult.ok && statsResult.data) {
          const kl = statsResult.data.klines ?? {};
          symbolCount = kl.unique_symbols ?? 0;
          totalBars = kl.total_bars ?? 0;
        }
      } catch {
        // Python cache unavailable
      }

      // Also get TS-side warehouse hit rate (runtime stats)
      try {
        const warehouseStats = await dataWarehouse.getStats();
        if (warehouseStats.hitRate !== "N/A") hitRate = warehouseStats.hitRate;
      } catch {
        /* ignore */
      }

      json(res, {
        gateway: {
          pid: process.pid,
          port: Number(process.env["OPENCLAW_GATEWAY_PORT"] || 18789),
          version: process.env["OPENCLAW_SERVICE_VERSION"] || "unknown",
          uptime: formatUptime(process.uptime()),
          memoryMB: Math.round(process.memoryUsage().heapUsed / 1048576),
        },
        receiver: { running: receiver.running },
        engine: { status: engine.getStatus() },
        data: {
          provider: config.dataProvider ?? "smart",
          symbolCount,
          totalBars,
          hitRate,
        },
        alerts: alertSummary,
        priceAlerts: { active: priceAlertService.activeCount },
        breakoutMonitor: deps.breakoutMonitor
          ? {
              active: deps.breakoutMonitor.listSetups(true).length,
              total: deps.breakoutMonitor.listSetups().length,
            }
          : undefined,
        scheduledTasks: { pending: taskScheduler.pendingCount },
        strategies: {
          builtin: listStrategies().length,
          user: userStrategyStore.list().length,
        },
        sseClients: eventBus.clientCount,
      });
    }),
  });

  // ── Sector Index API (CSV-based sector data management) ────────

  // GET stats / sector list
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/sector-index",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "/", "http://localhost");
      const action = url.searchParams.get("action") || "stats";
      const sector = url.searchParams.get("sector") || "";
      const code = url.searchParams.get("code") || "";

      const params: Record<string, string> = { action };
      if (sector) params.sector = sector;
      if (code) params.code = code;

      const result = await runPythonScript<Record<string, unknown>>(
        "data_providers/sector_index.py",
        params,
        config,
        { maxRetries: 0, timeoutMs: 30_000 },
      );
      json(res, result.ok ? result.data : { error: result.error ?? "板块索引查询失败" });
    }),
  });

  // POST upload CSV / reload
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/sector-index/upload",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = await parseObjectBody(req);
        const action = (body as Record<string, unknown>).action ?? "reload";
        const content = (body as Record<string, unknown>).content ?? "";

        const params: Record<string, string> = { action: String(action) };
        if (content) params.content = String(content);

        const result = await runPythonScript<Record<string, unknown>>(
          "data_providers/sector_index.py",
          params,
          config,
          { maxRetries: 0, timeoutMs: 30_000 },
        );
        json(res, result.ok ? result.data : { error: result.error ?? "操作失败" });
      } catch (err) {
        json(res, { error: `操作失败: ${err instanceof Error ? err.message : String(err)}` }, 500);
      }
    }),
  });

  // Strategy Lab (extracted to routes/strategy-lab.ts)
  registerStrategyLabRoutes(deps);

  // ── Strategy Lab UI Page ────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/strategy-lab",
    handler: securePublic(async (_req, res) => {
      const { getStrategyLabPageHtml } = await import("./pages/strategy-lab-page.js");
      const initialStrategies = listStrategies().map((s) => ({ id: s.id, name: s.name }));
      const html = getStrategyLabPageHtml("", config.apiToken, initialStrategies);
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // Strategy Routing (extracted to routes/strategy-routing.ts)
  registerStrategyRoutingRoutes(deps);

  // ── Helpers ──────────────────────────────────────────────────────

  /** Simplify a ScheduledTask for JSON output (strip non-serializable `run` fn). */
  function simplifyTask(t: {
    id: string;
    description: string;
    status: string;
    scheduledAt: string;
    createdAt: string;
    lastError?: string;
    meta?: unknown;
  }) {
    return {
      id: t.id,
      description: t.description,
      status: t.status,
      scheduledAt: t.scheduledAt,
      createdAt: t.createdAt,
      lastError: t.lastError,
      meta: t.meta,
    };
  }

  function formatUptime(seconds: number): string {
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    const parts: string[] = [];
    if (d > 0) parts.push(`${d}天`);
    if (h > 0) parts.push(`${h}时`);
    if (m > 0) parts.push(`${m}分`);
    parts.push(`${s}秒`);
    return parts.join("");
  }
}
