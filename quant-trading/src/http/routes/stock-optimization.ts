/**
 * Stock Combo Optimization API routes — 个股多策略组合优化
 *
 * Provides endpoints for:
 *  - Running per-stock combo optimization (finding best strategy combos)
 *  - Querying optimization results and trading plans
 *  - Checking optimization progress
 *  - Managing optimization history
 */
import { runPythonScript } from "../../bridge/python-bridge.js";
import type { AdminRouteDeps } from "../admin-routes.js";
import { json, parseObjectBody } from "./_helpers.js";
import type { QuantTradingConfig } from "../../types.js";
import * as fs from "node:fs/promises";
import * as path from "node:path";

import { getPythonDir } from "../../bridge/python-dir.js";

// ── Progress file helpers ────────────────────────────────────────

function getProgressPath(): string {
  const pythonDir = getPythonDir();
  const logsDir = path.join(path.dirname(pythonDir), "logs");
  return path.join(logsDir, "stock-optimization-progress.json");
}

async function readProgress(): Promise<Record<string, unknown>> {
  try {
    const p = getProgressPath();
    const raw = await fs.readFile(p, "utf-8");
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return { status: "idle" };
  }
}

async function writeProgress(data: Record<string, unknown>): Promise<void> {
  try {
    const p = getProgressPath();
    await fs.mkdir(path.dirname(p), { recursive: true });
    await fs.writeFile(p, JSON.stringify(data, null, 2), "utf-8");
  } catch { /* best-effort */ }
}

// ── Abort controller for cancellation ────────────────────────────

let optimizationAbort: AbortController | null = null;

// ── Route registration ───────────────────────────────────────────

export function registerStockOptimizationRoutes(deps: AdminRouteDeps): void {
  const { api, config, secure } = deps;

  // ── Run optimization ───────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/run",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;

        const symbols = Array.isArray(body.symbols)
          ? (body.symbols as string[]).filter((s) => typeof s === "string" && s.length > 0)
          : undefined;
        const universe = (body.universe as string) ?? "csi300";
        const backtestYears = Number(body.backtest_years) || 5;
        const topKStrategies = Number(body.top_k_strategies) || 5;
        const maxComboSize = Math.min(Number(body.max_combo_size) || 3, 4);
        const initialCapital = Number(body.initial_capital) || 1_000_000;
        const comboMethods = Array.isArray(body.combo_methods)
          ? (body.combo_methods as string[])
          : ["voting", "weighted", "gated"];
        const fitnessBatchId = (body.fitness_batch_id as string) ?? "";

        // Reset progress
        await writeProgress({
          status: "starting",
          phase: "正在初始化...",
          completed: 0,
          total: 0,
          elapsed_seconds: 0,
        });

        optimizationAbort = new AbortController();

        // Call Python optimizer
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "aipha/optimizer/stock_combo_optimizer.py",
          {
            action: "run",
            symbols: symbols ?? null,
            universe,
            backtest_years: backtestYears,
            top_k_strategies: topKStrategies,
            max_combo_size: maxComboSize,
            initial_capital: initialCapital,
            combo_methods: comboMethods,
            fitness_batch_id: fitnessBatchId,
          },
          config,
          { timeoutMs: 4 * 3600_000 },
        );

        optimizationAbort = null;

        if (!pyResult.ok) {
          json(res, { ok: false, error: pyResult.error ?? "优化失败" });
          return;
        }

        json(res, { ok: true, ...pyResult.data });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── Check progress ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/status",
    handler: secure(async (_req, res) => {
      try {
        const progress = await readProgress();
        json(res, progress);
      } catch {
        json(res, { status: "idle" });
      }
    }),
  });

  // ── Query stock result ─────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/result",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const symbol = url.searchParams.get("symbol") ?? "";
        const runId = url.searchParams.get("run_id") ?? "";

        if (!symbol) {
          json(res, { ok: false, error: "需要 symbol 参数" }, 400);
          return;
        }

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "aipha/optimizer/stock_combo_optimizer.py",
          { action: "query_stock", symbol, run_id: runId },
          config,
          { timeoutMs: 30_000 },
        );

        json(res, pyResult.ok ? pyResult.data : { ok: false, error: pyResult.error });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── List all optimized stocks ──────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/list",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const runId = url.searchParams.get("run_id") ?? "";

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "aipha/optimizer/stock_combo_optimizer.py",
          { action: "list_stocks", run_id: runId, limit: 200 },
          config,
          { timeoutMs: 30_000 },
        );

        json(res, pyResult.ok ? pyResult.data : { ok: false, error: pyResult.error });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── List optimization history ──────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/history",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const limit = Number(url.searchParams.get("limit")) || 20;

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "aipha/optimizer/stock_combo_optimizer.py",
          { action: "list_runs", limit },
          config,
          { timeoutMs: 30_000 },
        );

        json(res, pyResult.ok ? pyResult.data : { ok: false, error: pyResult.error });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── Delete a run ───────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/delete",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const runId = (body.run_id as string) ?? "";
        if (!runId) {
          json(res, { ok: false, error: "需要 run_id 参数" }, 400);
          return;
        }

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "aipha/optimizer/stock_combo_optimizer.py",
          { action: "delete_run", run_id: runId },
          config,
          { timeoutMs: 30_000 },
        );

        json(res, pyResult.ok ? pyResult.data : { ok: false, error: pyResult.error });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── Stop running optimization ──────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/stop",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        if (optimizationAbort) {
          optimizationAbort.abort();
          optimizationAbort = null;
        }
        await writeProgress({ status: "cancelled", phase: "已取消" });
        json(res, { ok: true, message: "已取消" });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── Apply trading plan to conditional orders ────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/apply",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbol = (body.symbol as string) ?? "";
        const entryPrice = Number(body.entry_price) || 0;
        const quantity = Number(body.quantity) || 0;
        const runId = (body.run_id as string) ?? "";

        if (!symbol || entryPrice <= 0 || quantity <= 0) {
          json(res, { ok: false, error: "需要 symbol, entry_price (>0), quantity (>0)" }, 400);
          return;
        }

        // Fetch the trading plan from DB via Python
        const pyResult = await runPythonScript<Record<string, unknown>>(
          "aipha/optimizer/stock_combo_optimizer.py",
          { action: "query_stock", symbol, run_id: runId },
          config,
          { timeoutMs: 30_000 },
        );

        if (!pyResult.ok || !pyResult.data) {
          json(res, { ok: false, error: "未找到优化数据" });
          return;
        }

        const data = pyResult.data as { results?: Array<Record<string, unknown>> };
        const results = data.results ?? [];
        if (!results.length) {
          json(res, { ok: false, error: `未找到 ${symbol} 的优化结果` });
          return;
        }

        // Use best combo (highest score)
        const best = results[0] as Record<string, unknown>;
        const plan = (best.trading_plan ?? {}) as Record<string, unknown>;

        // Apply via ConditionalOrderService
        const { conditionalOrderService } = deps;
        const ids = conditionalOrderService.applyTradingPlan(
          {
            symbol,
            stopLoss: plan.stopLoss as any,
            takeProfitTiers: plan.takeProfitTiers as any,
            buyZones: plan.buyZones as any,
            sellZones: plan.sellZones as any,
            comboMethod: (best.combo_method as string) ?? "optimization",
          },
          entryPrice,
          quantity,
        );

        json(res, {
          ok: true,
          symbol,
          entry_price: entryPrice,
          quantity,
          order_ids: ids,
          message: `已为 ${symbol} 创建 ${ids.length} 个条件单`,
        });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── Track performance snapshot ─────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/track",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const symbols = Array.isArray(body.symbols)
          ? (body.symbols as string[])
          : undefined;

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "aipha/optimizer/stock_combo_optimizer.py",
          { action: "track_performance", symbols: symbols ?? null },
          config,
          { timeoutMs: 120_000 },
        );

        json(res, pyResult.ok ? pyResult.data : { ok: false, error: pyResult.error });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── Performance tracking history ───────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-optimization/perf-history",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const symbol = url.searchParams.get("symbol") ?? "";
        const limit = Number(url.searchParams.get("limit")) || 30;

        const pyResult = await runPythonScript<Record<string, unknown>>(
          "aipha/optimizer/stock_combo_optimizer.py",
          { action: "perf_history", symbol, limit },
          config,
          { timeoutMs: 30_000 },
        );

        json(res, pyResult.ok ? pyResult.data : { ok: false, error: pyResult.error });
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        json(res, { ok: false, error: msg }, 500);
      }
    }),
  });

  // ── Optimization page ──────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/stock-optimization",
    handler: secure(async (_req, res) => {
      const { getStockOptimizationPageHtml } = await import(
        "../pages/stock-optimization-page.js"
      );
      const html = getStockOptimizationPageHtml();
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      res.end(html);
    }),
  });
}
