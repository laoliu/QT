import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { runPythonScript } from "../../bridge/python-bridge.js";
import { listStrategies } from "../../engine/strategy-registry.js";
import { runBatchBacktest, type BatchBacktestItem } from "../../services/batch-backtest.js";
import { alignCurves } from "../../services/live-backtest-compare.js";
import type { TickAggregationInterval, TradeRecord } from "../../types.js";
import { toNumberOrDefault } from "../../utils/number-utils.js";
import {
  appendPbtHistory,
  getPbtHistory,
  getPbtHistoryById,
  deletePbtHistory,
  getPbtStrategyFrequency,
} from "../pbt-history.js";
/**
 * Strategy Routing API routes (profile, recommend, routing, tick strategies)
 *
 * Extracted from admin-routes.ts for maintainability.
 */
import type { AdminRouteDeps } from "../admin-routes.js";
import { parseJsonBody } from "../parse-body.js";
import {
  json,
  clampInteger,
  toFiniteNumber,
  parseObjectBody,
  parseSymbolsParam,
  normalizeTickStrategyId,
  parseTickStrategyConfig,
  SUPPORTED_TICK_STRATEGY_IDS,
  type TickStrategyConfig,
} from "./_helpers.js";

export function registerStrategyRoutingRoutes(deps: AdminRouteDeps): void {
  const { api, config, secure, securePublic } = deps;

  // ── Strategy Routing: Stock Profile ──────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-profile",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbols = parseSymbolsParam(body.symbols);
        if (!symbols.length) {
          json(res, { error: "请提供 symbols" }, 400);
          return;
        }
        const endDate = (body.endDate as string) ?? new Date().toISOString().slice(0, 10);
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/stock_profiler.py",
          { action: "profile", symbols, endDate },
          config,
        );
        json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
      } catch (err) {
        json(
          res,
          { error: `画像分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Strategy Routing: Recommend ──────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-recommend",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbols = parseSymbolsParam(body.symbols);
        if (!symbols.length) {
          json(res, { error: "请提供 symbols" }, 400);
          return;
        }
        const endDate = (body.endDate as string) ?? new Date().toISOString().slice(0, 10);
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/strategy_router.py",
          { action: "recommend", symbols, endDate },
          config,
        );
        json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
      } catch (err) {
        json(
          res,
          { error: `策略推荐失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Portfolio Backtest Page ──────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/portfolio-backtest",
    handler: securePublic(async (_req, res) => {
      const { getPortfolioBacktestPageHtml } = await import("../pages/portfolio-backtest-page.js");
      const html = getPortfolioBacktestPageHtml("", config.apiToken);
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Portfolio Backtest API ─────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/portfolio-backtest",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbols = parseSymbolsParam(body.symbols);
        if (!symbols.length) {
          json(res, { error: "请提供 symbols (逗号分隔的股票代码)" }, 400);
          return;
        }
        const strategyIds = body.strategyIds
          ? String(body.strategyIds)
              .split(",")
              .map((s: string) => s.trim())
              .filter(Boolean)
          : ["dual-ma-crossover", "momentum-breakout", "rsi-filter", "mean-reversion"];
        const startDate =
          (body.startDate as string) ??
          (() => {
            const d = new Date();
            d.setFullYear(d.getFullYear() - 1);
            return d.toISOString().slice(0, 10);
          })();
        const endDate = (body.endDate as string) ?? new Date().toISOString().slice(0, 10);
        const initialCash = toNumberOrDefault(body.initialCash, 100000);

        const batchItems = strategyIds.map((id) => {
          const allStrats = listStrategies();
          return {
            label: allStrats.find((s) => s.id === id)?.name ?? id,
            request: {
              strategyId: id,
              symbols,
              startDate,
              endDate,
              initialCash,
            },
          };
        });

        const batchResult = await runBatchBacktest(batchItems, config);

        const comparison = batchResult.ranked.map((r, idx) => ({
          rank: idx + 1,
          strategy: r.label,
          strategyId: r.strategyId,
          totalReturn: r.totalReturn ?? null,
          annualizedReturn: r.annualizedReturn ?? null,
          finalValue: r.finalValue ?? null,
          profit: r.finalValue != null ? r.finalValue - initialCash : null,
          maxDrawdown: r.maxDrawdown ?? null,
          sharpeRatio: r.sharpeRatio ?? null,
          winRate: r.winRate ?? null,
          totalTrades: r.totalTrades ?? null,
          compositeScore: (r as unknown as Record<string, unknown>).compositeScore as number ?? null,
        }));

        const responseData = {
          ok: true,
          symbols,
          period: `${startDate} ~ ${endDate}`,
          initialCash,
          comparison,
          bestStrategy: batchResult.bestStrategy,
          totalRuns: batchResult.totalRuns,
          successCount: batchResult.successCount,
          failCount: batchResult.failCount,
          failed: batchResult.results
            .filter((r) => !r.ok)
            .map((r) => ({ strategy: r.label, error: r.error })),
        };

        // Auto-save to history (fire-and-forget)
        appendPbtHistory(config, {
          symbols,
          period: `${startDate} ~ ${endDate}`,
          initialCash,
          comparison,
          bestStrategy: batchResult.bestStrategy,
          successCount: batchResult.successCount,
          failCount: batchResult.failCount,
          totalRuns: batchResult.totalRuns,
        }).catch(() => {});

        json(res, responseData);
      } catch (err) {
        json(
          res,
          {
            error: `持仓回测失败: ${err instanceof Error ? err.message : String(err)}`,
          },
          500,
        );
      }
    }),
  });

  // ── Portfolio Backtest History ──────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/pbt-history",
    handler: secure(async (req, res) => {
      const url = new URL(req.url ?? "", "http://localhost");
      const historyId = url.searchParams.get("id");

      if (req.method === "DELETE" && historyId) {
        const ok = await deletePbtHistory(config, historyId);
        json(res, { ok });
        return;
      }

      if (historyId) {
        const entry = await getPbtHistoryById(config, historyId);
        json(res, entry ?? { error: "not found" });
        return;
      }

      const entries = await getPbtHistory(config);
      json(res, { entries, total: entries.length });
    }),
  });

  // ── Portfolio Backtest Strategy Frequency ───────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/pbt-strategy-frequency",
    handler: secure(async (_req, res) => {
      const freq = await getPbtStrategyFrequency(config);
      json(res, { strategies: freq, total: freq.length });
    }),
  });

  // ── Strategy Routing: Route Portfolio ────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/route-portfolio",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbols = parseSymbolsParam(body.symbols);
        if (!symbols.length) {
          json(res, { error: "请提供 symbols" }, 400);
          return;
        }
        const endDate = (body.endDate as string) ?? new Date().toISOString().slice(0, 10);
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/strategy_router.py",
          { action: "route_portfolio", symbols, endDate },
          config,
        );
        json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
      } catch (err) {
        json(
          res,
          { error: `组合路由失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Strategy Routing: Routing Table ──────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/routing-table",
    handler: secure(async (req, res) => {
      if (req.method === "GET") {
        try {
          const result = await runPythonScript<Record<string, unknown>>(
            "engines/strategy_router.py",
            { action: "get_routing_table" },
            config,
          );
          json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
        } catch (err) {
          json(
            res,
            { error: `获取路由表失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }
      if (req.method === "POST") {
        // Build routing table
        try {
          const body = (await parseObjectBody(req)) as Record<string, unknown>;
          const endDate = (body.endDate as string) ?? new Date().toISOString().slice(0, 10);
          const startDate =
            (body.startDate as string) ??
            (() => {
              const d = new Date();
              d.setFullYear(d.getFullYear() - 1);
              return d.toISOString().slice(0, 10);
            })();
          const maxStocks = toFiniteNumber(body.maxStocks, 10);
          const minBars = toFiniteNumber(body.minBars, 120);
          const result = await runPythonScript<Record<string, unknown>>(
            "engines/build_routing_table.py",
            { action: "build", endDate, startDate, maxStocks, minBars },
            config,
            { timeoutMs: 300_000 },
          );
          json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
        } catch (err) {
          json(
            res,
            { error: `构建路由表失败: ${err instanceof Error ? err.message : String(err)}` },
            500,
          );
        }
        return;
      }
      json(res, { error: "仅支持 GET/POST" }, 405);
    }),
  });

  // ── Profile → Route → Backtest Pipeline ──────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/profile-route-backtest",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbols = parseSymbolsParam(body.symbols);
        if (!symbols.length) {
          json(res, { error: "请提供 symbols 参数" }, 400);
          return;
        }
        const endDate = (body.endDate as string) ?? new Date().toISOString().slice(0, 10);
        const startDate =
          (body.startDate as string) ??
          (() => {
            const d = new Date();
            d.setFullYear(d.getFullYear() - 1);
            return d.toISOString().slice(0, 10);
          })();
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/strategy_router.py",
          { action: "profile_route_backtest", symbols, startDate, endDate },
          config,
          { timeoutMs: 300_000 },
        );
        json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
      } catch (err) {
        json(
          res,
          { error: `画像路由回测失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Enrich Candidates with Routing Info ──────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/enrich-routing",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const candidates = body.candidates as Array<Record<string, unknown>> | undefined;
        if (!candidates?.length) {
          json(res, { error: "请提供 candidates 数组" }, 400);
          return;
        }
        const endDate = (body.endDate as string) ?? new Date().toISOString().slice(0, 10);
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/strategy_router.py",
          { action: "enrich_with_routing", candidates, endDate },
          config,
        );
        json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
      } catch (err) {
        json(
          res,
          { error: `路由标注失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Market Regime ────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/market-regime",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") {
        json(res, { error: "仅支持 GET" }, 405);
        return;
      }
      try {
        const result = await runPythonScript<Record<string, unknown>>(
          "engines/strategy_router.py",
          { action: "get_regime" },
          config,
        );
        json(res, result.ok ? { ok: true, ...result.data } : { error: result.error });
      } catch (err) {
        json(
          res,
          { error: `获取宏观周期失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Tick Strategy Config Management ─────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/tick/strategies",
    handler: secure(async (req, res) => {
      if (req.method === "GET") {
        const configured = deps.tickStrategyService?.getConfiguredStrategies() ?? [];
        const runtime = deps.tickStrategyService?.getStatus() ?? null;
        json(res, {
          supportedIds: SUPPORTED_TICK_STRATEGY_IDS,
          configured,
          runtime,
        });
        return;
      }

      if (req.method !== "POST") {
        json(res, { error: "仅支持 GET/POST" }, 405);
        return;
      }

      const svc = deps.tickStrategyService;
      if (!svc) {
        json(res, { error: "Tick Strategy 服务未配置" }, 400);
        return;
      }

      const body = await parseObjectBody(req);
      const action = typeof body.action === "string" ? body.action : "upsert";
      const existing = svc.getConfiguredStrategies();
      let next = [...existing];

      if (action === "replace") {
        const list = Array.isArray(body.strategies) ? body.strategies : [];
        const parsed = list
          .map((item) =>
            item && typeof item === "object"
              ? parseTickStrategyConfig(item as Record<string, unknown>)
              : null,
          )
          .filter((item): item is TickStrategyConfig => item !== null);
        next = parsed;
      } else if (action === "remove") {
        const id = normalizeTickStrategyId(body.id);
        if (!id) {
          json(res, { error: "缺少有效的 strategy id" }, 400);
          return;
        }
        const symbol = typeof body.symbol === "string" ? body.symbol.trim() : "";
        if (!symbol) {
          next = existing.filter((s) => s.id !== id);
        } else {
          next = existing
            .map((s) => {
              if (s.id !== id) return s;
              const symbols = (s.symbols ?? []).filter((sym) => sym !== symbol);
              return { ...s, symbols: symbols.length > 0 ? symbols : undefined };
            })
            .filter((s) => !(s.id === id && !s.symbols));
        }
      } else {
        const strategy = parseTickStrategyConfig(body);
        if (!strategy) {
          json(res, { error: "策略配置无效，必须包含有效 id" }, 400);
          return;
        }
        const index = existing.findIndex((s) => s.id === strategy.id);
        if (index >= 0) {
          const merged = { ...existing[index], ...strategy };
          next[index] = merged;
        } else {
          next.push(strategy);
        }
      }

      svc.setConfiguredStrategies(next);
      json(res, {
        ok: true,
        supportedIds: SUPPORTED_TICK_STRATEGY_IDS,
        configured: svc.getConfiguredStrategies(),
        runtime: svc.getStatus(),
      });
    }),
  });

  // ── Tick Subscription Sync ───────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/tick/subscriptions",
    handler: secure(async (req, res) => {
      const svc = deps.tickIngressService;
      if (!svc) {
        json(res, { error: "Tick Ingress 服务未配置" }, 400);
        return;
      }

      if (req.method === "GET") {
        json(res, svc.getStatus());
        return;
      }

      if (req.method !== "POST") {
        json(res, { error: "仅支持 GET/POST" }, 405);
        return;
      }

      const body = await parseObjectBody(req);
      const symbols = Array.isArray(body.symbols)
        ? body.symbols.map((s) => String(s).trim()).filter(Boolean)
        : [];
      await svc.syncSubscriptions(symbols);
      json(res, { ok: true, status: svc.getStatus() });
    }),
  });

  // ── Tick Pipeline Status ─────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/tick/status",
    handler: secure((_req, res) => {
      const ingress = deps.tickIngressService?.getStatus() ?? null;
      const strategies = deps.tickStrategyService?.getStatus() ?? null;
      const features = deps.tickFeatureStore?.getAllFeatures() ?? [];
      json(res, {
        enabled: ingress !== null,
        ingress,
        strategies,
        featureSnapshot: features,
        featureSymbolCount: features.length,
      });
    }),
  });

  // ── Tick History Query ───────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/tick/history",
    handler: secure((req, res) => {
      if (req.method !== "GET") {
        json(res, { error: "仅支持 GET" }, 405);
        return;
      }

      const url = new URL(req.url ?? "/", "http://localhost");
      const kind = (url.searchParams.get("kind") ?? "features").toLowerCase();
      const symbol = (url.searchParams.get("symbol") ?? "").trim();
      const interval = (url.searchParams.get("interval") ?? "1m") as TickAggregationInterval;
      const limit = Math.max(1, Math.min(2000, Number(url.searchParams.get("limit") ?? "200")));
      const fromRaw = Number(url.searchParams.get("from") ?? "");
      const toRaw = Number(url.searchParams.get("to") ?? "");
      const range = {
        from: Number.isFinite(fromRaw) ? fromRaw : undefined,
        to: Number.isFinite(toRaw) ? toRaw : undefined,
      };
      const store = deps.tickHistoryStore;

      if (kind === "features") {
        if (!symbol) {
          const snapshot =
            store?.getLatestFeatures(limit, range) ?? deps.tickFeatureStore?.getAllFeatures() ?? [];
          json(res, {
            kind,
            symbol: null,
            limit,
            rows: snapshot.slice(-limit),
            total: snapshot.length,
          });
          return;
        }
        const rows =
          store?.getRecentFeatures(symbol, limit, range) ??
          deps.tickFeatureStore?.getRecentFeatures(symbol, limit) ??
          [];
        json(res, { kind, symbol, limit, rows, total: rows.length });
        return;
      }

      if (kind === "ticks") {
        if (!symbol) {
          json(res, { error: "ticks 查询必须提供 symbol" }, 400);
          return;
        }
        const rows =
          store?.getRecentTicks(symbol, limit, range) ??
          deps.tickIngressService?.getRecentTicks(symbol, limit) ??
          [];
        json(res, { kind, symbol, limit, rows, total: rows.length });
        return;
      }

      if (kind === "bars") {
        if (!symbol) {
          json(res, { error: "bars 查询必须提供 symbol" }, 400);
          return;
        }
        const validIntervals: TickAggregationInterval[] = ["3s", "15s", "1m", "5m"];
        if (!validIntervals.includes(interval)) {
          json(res, { error: "bars interval 仅支持 3s|15s|1m|5m" }, 400);
          return;
        }
        const rows =
          store?.getRecentBars(symbol, interval, limit, range) ??
          deps.tickIngressService?.getRecentBars(symbol, interval, limit) ??
          [];
        json(res, { kind, symbol, interval, limit, rows, total: rows.length });
        return;
      }

      json(res, { error: "kind 仅支持 ticks|bars|features" }, 400);
    }),
  });

  // ── Live vs Backtest Comparison Page ───────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/live-backtest",
    handler: securePublic(async (_req, res) => {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end("<html><body><h1>Live vs Backtest Comparison</h1><p>Coming soon</p></body></html>");
    }),
  });

  // ── Live vs Backtest Comparison API ───────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/live-backtest-compare",
    handler: secure(async (req, res) => {
      if (req.method !== "GET") {
        json(res, { error: "仅支持 GET" }, 405);
        return;
      }
      try {
        const url = new URL(req.url ?? "/", "http://localhost");
        const backtestId = url.searchParams.get("backtestId");
        if (!backtestId) {
          json(res, { error: "请提供 backtestId 参数" }, 400);
          return;
        }

        const mode = (url.searchParams.get("mode") ?? "live") as "paper" | "live";
        const startDate = url.searchParams.get("start") ?? undefined;
        const endDate = url.searchParams.get("end") ?? undefined;

        // Load backtest result
        const backtestStore = (deps as unknown as Record<string, unknown>).backtestStore as { getFullResult(id: string): unknown } | undefined;
        if (!backtestStore) {
          json(res, { error: "回测存储服务未配置" }, 400);
          return;
        }
        const btRecord = backtestStore.getFullResult(backtestId) as Record<string, unknown> | null;
        if (!btRecord) {
          json(res, { error: `未找到回测记录: ${backtestId}` }, 404);
          return;
        }

        // Load live equity snapshots
        type EqHist = { getRange(s: string, e: string): Array<{ date: string; equity: number }>; getAll(): Array<{ date: string; equity: number }> };
        const depsAny = deps as unknown as Record<string, unknown>;
        const eqHistory =
          mode === "paper" ? depsAny.equityHistoryPaper as EqHist | undefined : depsAny.equityHistoryLive as EqHist | undefined;
        const fallback = depsAny.equityHistory as EqHist | undefined;
        const equityHist = eqHistory ?? fallback;
        if (!equityHist) {
          json(res, { error: "权益历史服务未配置" }, 400);
          return;
        }

        let snapshots = startDate && endDate
          ? equityHist.getRange(startDate, endDate)
          : equityHist.getAll();
        if (startDate && !endDate) {
          snapshots = snapshots.filter((s) => s.date >= startDate);
        }
        if (endDate && !startDate) {
          snapshots = snapshots.filter((s) => s.date <= endDate);
        }

        // Load trade records
        const tradesDir =
          config.dataDir ?? path.join(os.homedir(), ".openclaw", "quant-trading");
        let trades: TradeRecord[] = [];
        try {
          trades = JSON.parse(
            await fs.readFile(path.join(tradesDir, "trades.json"), "utf-8"),
          ) as TradeRecord[];
        } catch {
          // No trades file — proceed with empty
        }

        if (snapshots.length === 0) {
          json(res, {
            error: `${mode === "paper" ? "模拟盘" : "实盘"}暂无权益数据。请确保交易引擎已运行并产生记录。`,
          }, 400);
          return;
        }

        const liveCurve = snapshots.map((s: { date: string; equity: number }) => ({ date: s.date, equity: s.equity }));
        const btCurve = Array.isArray((btRecord as Record<string, unknown>).equityCurve)
          ? ((btRecord as Record<string, unknown>).equityCurve as Array<{ date: string; equity: number }>)
          : [];
        const aligned = alignCurves(liveCurve, btCurve);
        json(res, { aligned, live: liveCurve, backtest: btCurve });
      } catch (err) {
        json(
          res,
          { error: `对比失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Strategy Routing UI Page ────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/strategy-routing",
    handler: securePublic(async (_req, res) => {
      const { getStrategyRoutingPageHtml } = await import("../pages/strategy-routing-page.js");
      const html = getStrategyRoutingPageHtml("", config.apiToken);
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });

  // ── Breakout Monitor Setups ────────────────────────────────────
  if (deps.breakoutMonitor) {
    const breakoutMonitor = deps.breakoutMonitor;
    api.registerHttpRoute({
      auth: "plugin",
      path: "/quant/admin/api/breakout-orders",
      handler: secure(async (req, res) => {
        if (req.method === "DELETE") {
          const body = await parseJsonBody<{ id?: string }>(req);
          const id = body?.id;
          if (!id) {
            json(res, { ok: false, error: "missing id" }, 400);
            return;
          }
          const ok = breakoutMonitor.cancelSetup(id);
          json(res, { ok, id });
          return;
        }
        const setups = breakoutMonitor.listSetups();
        const eodRules = breakoutMonitor.listEodRules();
        const active = setups.filter(
          (s) =>
            s.status === "watching" || s.status === "confirming" || s.status === "pullback_wait",
        );
        json(res, {
          setups,
          eodRules,
          activeCount: active.length,
          total: setups.length,
        });
      }),
    });
  }
}
