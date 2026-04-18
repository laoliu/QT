/**
 * Strategy-Stock Fitness API routes
 *
 * Provides endpoints for:
 *  - Running strategy fitness analysis (all strategies × all stocks)
 *  - Querying per-stock top strategies
 *  - Querying global strategy ranking
 *  - Checking analysis progress
 */
import { runPythonScript } from "../../bridge/python-bridge.js";
import type { AdminRouteDeps } from "../admin-routes.js";
import { parseJsonBody } from "../parse-body.js";
import { json, parseObjectBody } from "./_helpers.js";

export function registerStrategyFitnessRoutes(deps: AdminRouteDeps): void {
  const { api, config, secure } = deps;

  // ── Run fitness analysis ───────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/run",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          {
            action: "run",
            universe: (body.universe as string) ?? "csi1000",
            custom_symbols: body.custom_symbols ?? [],
            backtest_years: Number(body.backtest_years) || 5,
            top_n_per_stock: Number(body.top_n_per_stock) || 5,
            top_n_global: Number(body.top_n_global) || 10,
            workers: Number(body.workers) || 8,
            initial_capital: Number(body.initial_capital) || 1_000_000,
          },
          config,
          { timeoutMs: 4 * 3600_000 }, // 4 hours max
        );
        json(res, result.ok ? { ok: true, ...result.data } : { ok: false, error: result.error });
      } catch (err) {
        json(
          res,
          { ok: false, error: `适配分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
      }
    }),
  });

  // ── Query progress ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/status",
    handler: secure(async (_req, res) => {
      try {
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          { action: "status" },
          config,
          { timeoutMs: 10_000 },
        );
        json(res, result.ok ? result.data : { status: "unknown", error: result.error });
      } catch (err) {
        json(res, { status: "error", error: String(err) }, 500);
      }
    }),
  });

  // ── Query stock top strategies ─────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/stock",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const symbol = url.searchParams.get("symbol") ?? "";
        const batchId = url.searchParams.get("batch_id") ?? "";
        if (!symbol) {
          json(res, { error: "需要 symbol 参数" }, 400);
          return;
        }
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          { action: "query_stock", symbol, batch_id: batchId },
          config,
          { timeoutMs: 30_000 },
        );
        json(res, result.ok ? result.data : { error: result.error });
      } catch (err) {
        json(res, { error: String(err) }, 500);
      }
    }),
  });

  // ── Query global strategy ranking ──────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/global",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const batchId = url.searchParams.get("batch_id") ?? "";
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          { action: "query_global", batch_id: batchId },
          config,
          { timeoutMs: 30_000 },
        );
        json(res, result.ok ? result.data : { error: result.error });
      } catch (err) {
        json(res, { error: String(err) }, 500);
      }
    }),
  });

  // ── All stocks top strategy list ────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/all-stocks",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const batchId = url.searchParams.get("batch_id") || undefined;
        const limit = Math.min(Math.max(parseInt(url.searchParams.get("limit") ?? "100", 10) || 100, 1), 2000);
        const offset = Math.max(parseInt(url.searchParams.get("offset") ?? "0", 10) || 0, 0);
        const storage = deps.dataWarehouse?.storage;
        if (storage && "getAllStocksTopStrategy" in storage) {
          const result = await (storage as { getAllStocksTopStrategy(b?: string, l?: number, o?: number): Promise<{ rows: unknown[]; total: number }> })
            .getAllStocksTopStrategy(batchId, limit, offset);
          if (result.total > 0) {
            const bid = batchId ?? (storage as { getLatestFitnessBatchId(): Promise<string | null> }).getLatestFitnessBatchId
              ? await (storage as { getLatestFitnessBatchId(): Promise<string | null> }).getLatestFitnessBatchId()
              : null;
            json(res, { ok: true, stocks: result.rows, total: result.total, batch_id: bid, limit, offset });
            return;
          }
        }
        // Fallback: query via Python
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          { action: "query_all_stocks", batch_id: batchId ?? "", limit, offset },
          config,
          { timeoutMs: 30_000 },
        );
        json(res, result.ok ? { ok: true, ...(result.data as object) } : { error: result.error });
      } catch (err) {
        json(res, { error: String(err) }, 500);
      }
    }),
  });

  // ── List fitness batch history ─────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/history",
    handler: secure(async (_req, res) => {
      try {
        // Try TS storage first (works when better-sqlite3 is available)
        const storage = deps.dataWarehouse?.storage;
        if (storage && "listFitnessBatches" in storage) {
          const batches = await (storage as { listFitnessBatches(n: number): Promise<unknown[]> }).listFitnessBatches(20);
          if (Array.isArray(batches) && batches.length > 0) {
            json(res, { ok: true, batches });
            return;
          }
        }
        // Fallback: query via Python (sqlite3 is built-in)
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          { action: "list_batches", limit: 20 },
          config,
          { timeoutMs: 15_000 },
        );
        json(res, result.ok ? { ok: true, batches: (result.data as Record<string, unknown>)?.batches ?? [] } : { ok: true, batches: [] });
      } catch (err) {
        json(res, { error: String(err) }, 500);
      }
    }),
  });

  // ── Batch detail: list stocks & strategies ─────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/batch-detail",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const batchId = url.searchParams.get("batch_id") ?? "";
        if (!batchId) {
          json(res, { error: "需要 batch_id 参数" }, 400);
          return;
        }
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          { action: "batch_detail", batch_id: batchId },
          config,
          { timeoutMs: 30_000 },
        );
        json(res, result.ok ? result.data : { error: result.error });
      } catch (err) {
        json(res, { error: String(err) }, 500);
      }
    }),
  });
}
