/**
 * Stock Discovery API routes — 多维度选股引擎
 *
 * Provides endpoints for:
 *  - Listing available discovery strategies
 *  - Running stock discovery (single strategy or combo)
 *  - Checking progress of a running discovery
 *  - Stopping a running discovery
 */
import { runPythonScript } from "../../bridge/python-bridge.js";
import type { AdminRouteDeps } from "../admin-routes.js";
import { json, parseObjectBody } from "./_helpers.js";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import { getPythonDir } from "../../bridge/python-dir.js";
import { nowCSTISO } from "../../utils/cst-time.js";
import { fetchStockNames } from "./daily-pick.js";

// ── Progress file ────────────────────────────────────────────────

function getProgressPath(): string {
  const pythonDir = getPythonDir();
  const logsDir = path.join(path.dirname(pythonDir), "logs");
  return path.join(logsDir, "stock-discovery-progress.json");
}

async function readProgress(): Promise<Record<string, unknown>> {
  try {
    const raw = await fs.readFile(getProgressPath(), "utf-8");
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

// ── Abort controller ─────────────────────────────────────────────

let discoveryAbort: AbortController | null = null;

// ── History file ─────────────────────────────────────────────────

const HISTORY_MAX = 50; // keep last 50 runs

function getHistoryPath(): string {
  const pythonDir = getPythonDir();
  const logsDir = path.join(path.dirname(pythonDir), "logs");
  return path.join(logsDir, "stock-discovery-history.json");
}

async function appendHistory(entry: Record<string, unknown>): Promise<void> {
  try {
    const p = getHistoryPath();
    await fs.mkdir(path.dirname(p), { recursive: true });
    let history: Record<string, unknown>[] = [];
    try {
      const raw = await fs.readFile(p, "utf-8");
      history = JSON.parse(raw) as Record<string, unknown>[];
    } catch { /* no existing history */ }
    history.unshift(entry); // newest first
    if (history.length > HISTORY_MAX) history.length = HISTORY_MAX;
    await fs.writeFile(p, JSON.stringify(history, null, 2), "utf-8");
  } catch { /* best-effort */ }
}

// ── Route registration ───────────────────────────────────────────

export function registerStockDiscoveryRoutes(deps: AdminRouteDeps): void {
  const { api, config, secure } = deps;

  // ── List available strategies ──────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-discovery/strategies",
    handler: secure(async (_req, res) => {
      try {
        const result = await runPythonScript<{
          ok: boolean;
          strategies: Array<{
            id: string;
            name: string;
            description: string;
            requires_klines: boolean;
          }>;
        }>("scripts/stock_discovery.py", { action: "list_strategies" }, config, {
          timeoutMs: 15_000,
        });
        if (result.ok && result.data) {
          json(res, result.data);
        } else {
          json(res, { ok: false, error: result.error ?? "列表获取失败" }, 500);
        }
      } catch (e: unknown) {
        json(res, { ok: false, error: String(e) }, 500);
      }
    }),
  });

  // ── Run discovery ──────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-discovery/run",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") {
        json(res, { error: "仅支持 POST" }, 405);
        return;
      }

      // Check if already running
      const progress = await readProgress();
      if (progress.status === "running") {
        json(res, { ok: false, error: "已有选股任务在运行中" }, 409);
        return;
      }

      let body: Record<string, unknown>;
      try {
        body = (await parseObjectBody(req)) as Record<string, unknown>;
      } catch {
        json(res, { ok: false, error: "请求体解析失败" }, 400);
        return;
      }

      const strategy = (body.strategy as string) ?? "low_position";
      const universe = (body.universe as string) ?? "csi500";
      const limit = Math.min(Number(body.limit) || 20, 100);
      const withKlines = body.with_klines !== false;
      const klineDays = Number(body.kline_days) || 252;
      const symbols = Array.isArray(body.symbols)
        ? (body.symbols as string[]).filter((s) => typeof s === "string" && s.length > 0)
        : [];
      const params = (body.params as Record<string, unknown>) ?? {};

      const batchId = `discovery_${Date.now()}`;

      // Write running progress
      await writeProgress({
        status: "running",
        batch_id: batchId,
        strategy,
        universe,
        started_at: nowCSTISO(),
        phase: "加载行情数据...",
        universe_count: 0,
        screened_count: 0,
      });

      // Start discovery (can be slow — up to 120s with klines)
      discoveryAbort = new AbortController();
      const abortSignal = discoveryAbort.signal;

      const payload: Record<string, unknown> = {
        action: "discover",
        strategy,
        universe: symbols.length > 0 ? "custom" : universe,
        limit,
        with_klines: withKlines,
        kline_days: klineDays,
        params,
      };
      if (symbols.length > 0) {
        payload.symbols = symbols;
      }

      try {
        const result = await runPythonScript<{
          ok: boolean;
          strategy: string;
          strategy_label: string;
          universe: string;
          universe_count: number;
          screened_count: number;
          results: Array<Record<string, unknown>>;
          elapsed_seconds: number;
          generated_at: string;
          error?: string;
        }>("scripts/stock_discovery.py", payload, config, {
          timeoutMs: 360_000,
        });

        if (!abortSignal.aborted) {
          await writeProgress({
            status: result.ok ? "done" : "error",
            batch_id: batchId,
            strategy,
            universe,
            finished_at: nowCSTISO(),
            universe_count: result.data?.universe_count ?? 0,
            screened_count: result.data?.screened_count ?? 0,
            error: result.ok ? (result.data?.ok ? undefined : (result.data?.error ?? "选股失败")) : (result.error ?? "未知错误"),
          });

          if (result.ok && result.data?.ok) {
            const { ok: _ok, ...restData } = result.data;

            // Back-fill missing stock names
            if (Array.isArray(restData.results)) {
              const missing = (restData.results as Array<{ stock_id: string; name?: string }>)
                .filter((r) => !r.name)
                .map((r) => r.stock_id);
              if (missing.length > 0) {
                try {
                  const nameMap = await fetchStockNames(missing);
                  for (const r of restData.results as Array<{ stock_id: string; name?: string }>) {
                    if (!r.name && nameMap.has(r.stock_id)) {
                      r.name = nameMap.get(r.stock_id) ?? "";
                    }
                  }
                } catch { /* best-effort */ }
              }
            }

            // Append to history
            const topResults = Array.isArray(restData.results)
              ? (restData.results as Array<Record<string, unknown>>).slice(0, 10).map((r) => ({
                  stock_id: r["stock_id"],
                  name: r["name"],
                  score: r["score"],
                  rank: r["rank"],
                  signals: (r["signals"] as string[] | undefined)?.slice(0, 2),
                }))
              : [];
            await appendHistory({
              batch_id: batchId,
              strategy,
              strategy_label: restData.strategy_label,
              universe,
              universe_count: restData.universe_count,
              screened_count: restData.screened_count,
              elapsed_seconds: restData.elapsed_seconds,
              finished_at: nowCSTISO(),
              is_offline: restData.is_offline ?? false,
              is_sina_fallback: restData.is_sina_fallback ?? false,
              top_results: topResults,
            });

            json(res, { ok: true, batch_id: batchId, ...restData });
          } else {
            const errMsg = result.ok
              ? (result.data?.error ?? "选股失败")
              : (result.error ?? "选股失败");
            json(res, { ok: false, error: errMsg }, 422);
          }
        }
      } catch (e: unknown) {
        if (!abortSignal.aborted) {
          await writeProgress({ status: "error", batch_id: batchId, error: String(e) });
          json(res, { ok: false, error: String(e) }, 500);
        }
      }
    }),
  });

  // ── Status ─────────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-discovery/status",
    handler: secure(async (_req, res) => {
      const progress = await readProgress();
      json(res, progress);
    }),
  });

  // ── Stop running discovery ──────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-discovery/stop",
    handler: secure(async (_req, res) => {
      if (discoveryAbort) {
        discoveryAbort.abort();
        discoveryAbort = null;
        await writeProgress({ status: "cancelled", cancelled_at: nowCSTISO() });
        json(res, { ok: true, message: "已取消" });
      } else {
        json(res, { ok: true, message: "无运行中任务" });
      }
    }),
  });

  // ── Run history ────────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/stock-discovery/history",
    handler: secure(async (_req, res) => {
      try {
        const raw = await fs.readFile(getHistoryPath(), "utf-8");
        const history = JSON.parse(raw) as Record<string, unknown>[];
        json(res, { ok: true, history });
      } catch {
        json(res, { ok: true, history: [] });
      }
    }),
  });
}
