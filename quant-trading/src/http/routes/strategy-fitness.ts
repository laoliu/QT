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
import { runBuiltinBacktest } from "../../engine/builtin-engine.js";
import { hasStrategyFactory, resolveBuiltinStrategy } from "../../engine/strategy-registry.js";
import { createDataProvider } from "../../data/market-data-provider.js";
import type { AdminRouteDeps } from "../admin-routes.js";
import { json, parseObjectBody, setAiphaStrategies } from "./_helpers.js";
import type { BacktestResult, QuantTradingConfig } from "../../types.js";
import type { QuantStorage, StockStrategyFitnessRow, GlobalStrategyRankingRow } from "../../services/sqlite-storage.js";
import * as fs from "node:fs/promises";
import * as fsSync from "node:fs";
import * as path from "node:path";
import * as os from "node:os";
import { Worker } from "node:worker_threads";
import { fileURLToPath, pathToFileURL } from "node:url";

import { getPythonDir } from "../../bridge/python-dir.js";
import { applyComboToPaper } from "../../services/portfolio-combo-backtest.js";

// ── Composite score (same formula as Python + daily-pick) ────────
function compositeScore(m: {
  totalReturn: number;
  annualizedReturn: number;
  sharpeRatio: number;
  winRate: number;
  profitFactor: number;
  maxDrawdown: number;
  totalTrades: number;
}): number {
  return (
    m.totalReturn * 1.0 +
    m.annualizedReturn * 0.35 +
    m.sharpeRatio * 12 +
    m.winRate * 0.28 +
    Math.min(m.profitFactor, 4) * 4 -
    m.maxDrawdown * 0.85 +
    Math.min(m.totalTrades, 120) * 0.03
  );
}

// ── Progress file helpers ────────────────────────────────────────
function getProgressPath(_config: QuantTradingConfig): string {
  const pythonDir = getPythonDir();
  const logsDir = path.join(path.dirname(pythonDir), "logs");
  return path.join(logsDir, "fitness-progress.json");
}

async function writeProgress(config: QuantTradingConfig, data: Record<string, unknown>): Promise<void> {
  try {
    const p = getProgressPath(config);
    await fs.mkdir(path.dirname(p), { recursive: true });
    await fs.writeFile(p, JSON.stringify(data, null, 2), "utf-8");
  } catch { /* best-effort */ }
}

// ── DB helpers (same schema as Python) ───────────────────────────
function getDbPath(): string {
  const dataDir = process.env.QUANT_DATA_DIR || path.join(os.homedir(), ".openclaw", "quant-data");
  return path.join(dataDir, "quant-trading.db");
}

function ensureFitnessTables(db: { exec(sql: string): void }): void {
  db.exec(`
    CREATE TABLE IF NOT EXISTS stock_strategy_fitness (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      symbol          TEXT NOT NULL,
      stock_name      TEXT,
      strategy_id     TEXT NOT NULL,
      strategy_name   TEXT,
      rank            INTEGER NOT NULL,
      composite_score REAL NOT NULL,
      total_return    REAL,
      annualized_return REAL,
      sharpe_ratio    REAL,
      sortino_ratio   REAL,
      max_drawdown    REAL,
      win_rate        REAL,
      profit_loss_ratio REAL,
      calmar_ratio    REAL,
      total_trades    INTEGER,
      backtest_start  TEXT,
      backtest_end    TEXT,
      batch_id        TEXT NOT NULL,
      created_at      TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE (symbol, strategy_id, batch_id)
    );
    CREATE INDEX IF NOT EXISTS idx_ssf_symbol ON stock_strategy_fitness(symbol);
    CREATE INDEX IF NOT EXISTS idx_ssf_strategy ON stock_strategy_fitness(strategy_id);
    CREATE INDEX IF NOT EXISTS idx_ssf_batch ON stock_strategy_fitness(batch_id);

    CREATE TABLE IF NOT EXISTS global_strategy_ranking (
      id              INTEGER PRIMARY KEY AUTOINCREMENT,
      strategy_id     TEXT NOT NULL,
      strategy_name   TEXT,
      rank            INTEGER NOT NULL,
      total_score     REAL NOT NULL,
      stock_count     INTEGER NOT NULL,
      avg_score       REAL,
      max_score       REAL,
      min_score       REAL,
      batch_id        TEXT NOT NULL,
      created_at      TEXT NOT NULL DEFAULT (datetime('now')),
      UNIQUE (strategy_id, batch_id)
    );
    CREATE INDEX IF NOT EXISTS idx_gsr_strategy ON global_strategy_ranking(strategy_id);
    CREATE INDEX IF NOT EXISTS idx_gsr_batch ON global_strategy_ranking(batch_id);
  `);
}

interface FitnessRow {
  symbol: string;
  stock_name?: string;
  strategy_id: string;
  strategy_name?: string;
  rank: number;
  composite_score: number;
  total_return?: number;
  annualized_return?: number;
  sharpe_ratio?: number;
  sortino_ratio?: number;
  max_drawdown?: number;
  win_rate?: number;
  profit_loss_ratio?: number;
  calmar_ratio?: number;
  total_trades?: number;
  backtest_start?: string;
  backtest_end?: string;
}

interface GlobalRankRow {
  strategy_id: string;
  strategy_name?: string;
  rank: number;
  total_score: number;
  stock_count: number;
  avg_score?: number;
  max_score?: number;
  min_score?: number;
}

// ── Pool-backtest boost: load latest pool results → per-symbol boost map ──
interface PoolBoostEntry {
  poolScore: number;     // pool composite score
  poolLabel: string;
  strategyType: string;
  poolKey: string;
}

/**
 * Load latest pool_strategy_backtest results from SQLite.
 * Returns Map<symbol, PoolBoostEntry> — for each symbol the best pool combo it appears in.
 * Pool composite score = annualized×0.3 + sharpe×20 + (100-drawdown)×0.2 + winRate×0.1 + pf×5
 */
function loadPoolBoostMap(): Map<string, PoolBoostEntry> {
  const boostMap = new Map<string, PoolBoostEntry>();
  try {
    const dbPath = getDbPath();
    const fsSync = require("node:fs") as typeof import("node:fs");
    if (!fsSync.existsSync(dbPath)) return boostMap;
    const sqlite3Mod = require("better-sqlite3") as typeof import("better-sqlite3");
    const Database = (sqlite3Mod as unknown as { default?: unknown }).default ?? sqlite3Mod;
    const db = new (Database as unknown as new (p: string) => Record<string, Function>)(dbPath);
    try {
      db.pragma("busy_timeout = 10000");
      // Check table existence
      const tbl = db.prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='pool_strategy_backtest'").get();
      if (!tbl) return boostMap;
      // Load latest run_id
      const latestRun = db.prepare(
        "SELECT run_id FROM pool_strategy_backtest ORDER BY created_at DESC LIMIT 1",
      ).get() as { run_id: string } | undefined;
      if (!latestRun) return boostMap;
      // Load all combos from latest run
      const rows = db.prepare(
        "SELECT * FROM pool_strategy_backtest WHERE run_id = ?",
      ).all(latestRun.run_id) as Array<Record<string, unknown>>;
      for (const r of rows) {
        // Compute composite score (same formula as Python)
        const ann = (r.annualized_return_pct as number) ?? 0;
        const sharpe = (r.sharpe as number) ?? 0;
        const dd = (r.max_drawdown_pct as number) ?? 0;
        const wr = (r.win_rate_pct as number) ?? 0;
        const pf = (r.profit_factor as number) ?? 0;
        const poolScore = Math.round(
          (ann * 0.3 + sharpe * 20 + (100 - dd) * 0.2 + wr * 0.1 + pf * 5) * 100,
        ) / 100;
        // Parse symbols
        let symbols: string[] = [];
        try {
          const raw = r.symbols as string;
          symbols = raw ? JSON.parse(raw) : [];
        } catch { symbols = []; }
        const entry: PoolBoostEntry = {
          poolScore,
          poolLabel: (r.pool_label as string) ?? "",
          strategyType: (r.strategy_type as string) ?? "",
          poolKey: (r.pool_key as string) ?? "",
        };
        // Assign to each symbol — keep best pool score
        for (const sym of symbols) {
          const existing = boostMap.get(sym);
          if (!existing || existing.poolScore < poolScore) {
            boostMap.set(sym, entry);
          }
        }
      }
    } finally {
      db.close();
    }
  } catch { /* pool boost unavailable, continue without */ }
  return boostMap;
}

/** Apply pool boost to a fitness score. boost_weight controls max boost (0.2 = 20%). */
function applyPoolBoost(fitnessScore: number, poolScore: number, maxPoolScore: number, boostWeight = 0.2): number {
  if (maxPoolScore <= 0) return fitnessScore;
  const normalizedPool = Math.max(0, poolScore) / maxPoolScore; // 0..1
  return fitnessScore * (1 + boostWeight * normalizedPool);
}

// ── Abort controller for fitness run ─────────────────────────────
let fitnessAbort: AbortController | null = null;

// ── Kline-caching provider wrapper ───────────────────────────────
// Wraps a real provider and caches getKlines results in memory so the same
// symbol+interval+range is only fetched once across all strategy runs.
import type { MarketDataProvider } from "../../data/market-data-provider.js";
import type { KlineBar, KlineInterval, MarketType, QuoteSnapshot, SymbolInfo } from "../../types.js";

class CachingProvider implements MarketDataProvider {
  name = "caching";
  private cache = new Map<string, Promise<KlineBar[]>>();
  constructor(private inner: MarketDataProvider) {}
  private cacheKey(symbol: string, interval: KlineInterval, start: string, end: string, market?: MarketType) {
    return `${symbol}|${interval}|${start}|${end}|${market ?? ""}`;
  }
  getQuote(symbol: string, market?: MarketType): Promise<QuoteSnapshot | null> {
    return this.inner.getQuote(symbol, market);
  }
  getKlines(symbol: string, interval: KlineInterval, start: string, end: string, market?: MarketType): Promise<KlineBar[]> {
    const key = this.cacheKey(symbol, interval, start, end, market);
    let cached = this.cache.get(key);
    if (!cached) {
      cached = this.inner.getKlines(symbol, interval, start, end, market);
      this.cache.set(key, cached);
    }
    return cached;
  }
  searchSymbols(keyword: string, market?: MarketType): Promise<SymbolInfo[]> {
    return this.inner.searchSymbols(keyword, market);
  }
}

// ── Worker thread pool for parallel backtesting ─────────────────
const WORKER_SRC = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../engine/fitness-worker.ts",
);
const WORKER_BUNDLE = WORKER_SRC.replace(/\.ts$/, ".mjs");

/** Build the worker bundle with esbuild if missing or any dependency is newer */
async function ensureWorkerBundle(): Promise<string> {
  const fsP = await import("node:fs/promises");
  const outStat = await fsP.stat(WORKER_BUNDLE).catch(() => null);
  const outMtime = outStat?.mtimeMs ?? 0;

  // Check all .ts files under src/ — any change should trigger rebuild
  const srcDir = path.resolve(path.dirname(WORKER_SRC), "..");
  let needsRebuild = !outStat;
  if (!needsRebuild) {
    const walk = async (dir: string): Promise<boolean> => {
      for (const entry of await fsP.readdir(dir, { withFileTypes: true })) {
        if (entry.isDirectory()) {
          if (await walk(path.join(dir, entry.name))) return true;
        } else if (entry.name.endsWith(".ts")) {
          const st = await fsP.stat(path.join(dir, entry.name));
          if (st.mtimeMs > outMtime) return true;
        }
      }
      return false;
    };
    needsRebuild = await walk(srcDir);
  }

  if (needsRebuild) {
    console.log("[Builtin Fitness] 重新构建 worker bundle...");
    const { build } = await import("esbuild");
    await build({
      entryPoints: [WORKER_SRC],
      bundle: true,
      platform: "node",
      format: "esm",
      outfile: WORKER_BUNDLE,
      external: ["node:*"],
    });
  }
  return WORKER_BUNDLE;
}

interface WorkerHandle {
  worker: Worker;
  busy: boolean;
}

function createWorkerPool(size: number, bundlePath: string, config: QuantTradingConfig): WorkerHandle[] {
  const pool: WorkerHandle[] = [];
  for (let i = 0; i < size; i++) {
    const worker = new Worker(pathToFileURL(bundlePath), {
      workerData: { config },
      execArgv: [],
    });
    pool.push({ worker, busy: false });
  }
  return pool;
}

function destroyPool(pool: WorkerHandle[]) {
  for (const h of pool) {
    h.worker.postMessage({ type: "exit" });
    h.worker.terminate();
  }
}

interface FitnessTaskResult {
  strategy_id: string;
  symbol: string;
  composite_score: number;
  [k: string]: unknown;
}

/**
 * Load pre-fetched klines from a NDJSON temp file written by Python.
 * Each line: symbol\tJSON_array_of_bars (parsed line-by-line to avoid string length limits).
 * Returns a Map<symbol, KlineBar[]> ready for direct use, or null on failure.
 */
async function loadPreloadedKlines(filePath: string): Promise<Map<string, KlineBar[]> | null> {
  try {
    const { createInterface } = await import("node:readline");
    const result = new Map<string, KlineBar[]>();
    const input = fsSync.createReadStream(filePath, { encoding: "utf-8" });
    const rl = createInterface({ input, crlfDelay: Infinity });
    for await (const line of rl) {
      const tabIdx = line.indexOf("\t");
      if (tabIdx <= 0) continue;
      const sym = line.slice(0, tabIdx);
      const barsJson = line.slice(tabIdx + 1);
      try {
        const bars = JSON.parse(barsJson) as Array<{ t: number; o: number; h: number; l: number; c: number; v: number }>;
        if (bars.length > 0) {
          result.set(sym, bars.map((b) => ({
            timestamp: b.t,
            open: b.o,
            high: b.h,
            low: b.l,
            close: b.c,
            volume: b.v,
          })));
        }
      } catch { /* skip malformed line */ }
    }
    console.log(`[Builtin Fitness] 从Python临时文件加载了 ${result.size} 只股票K线数据`);
    // Clean up temp file (best-effort)
    fs.unlink(filePath).catch(() => {});
    return result;
  } catch (err) {
    console.log(`[Builtin Fitness] 加载预存K线失败: ${err}, 将回退到在线获取`);
    return null;
  }
}

// ── Builtin fitness runner ───────────────────────────────────────
async function runBuiltinFitness(
  builtinStrategies: string[],
  symbols: string[],
  startDate: string,
  endDate: string,
  initialCash: number,
  config: QuantTradingConfig,
  onProgress: (completed: number, total: number, current: string) => void,
  signal?: AbortSignal,
  preloadedBars?: Map<string, KlineBar[]>,
): Promise<FitnessTaskResult[]> {
  const results: FitnessTaskResult[] = [];
  const total = builtinStrategies.length * symbols.length;
  let completed = 0;

  // If Python already loaded klines, use them directly — no bridge requests needed
  let allBars: Map<string, KlineBar[]>;
  if (preloadedBars && preloadedBars.size > 0) {
    console.log(`[Builtin Fitness] 使用Python预加载的K线数据 (${preloadedBars.size} 只股票)`);
    allBars = preloadedBars;
  } else {
    // Fallback: fetch from data provider with reduced concurrency to avoid overwhelming bridge
    const provider = new CachingProvider(createDataProvider(config));
    const PREFETCH_CONCURRENCY = 4;  // Reduced from 16 to avoid bridge overload
    const PER_SYMBOL_TIMEOUT_MS = 30_000;
    const timedGetKlines = (sym: string) => {
      return new Promise<KlineBar[]>((resolve) => {
        const timer = setTimeout(() => {
          console.log(`[Builtin Fitness] ⚠ ${sym} K线获取超时 (${PER_SYMBOL_TIMEOUT_MS / 1000}s), 跳过`);
          resolve([]);
        }, PER_SYMBOL_TIMEOUT_MS);
        provider.getKlines(sym, "1d", startDate, endDate, "cn_stock")
          .then((bars) => { clearTimeout(timer); resolve(bars); })
          .catch(() => { clearTimeout(timer); resolve([]); });
      });
    };
    console.log(`[Builtin Fitness] 预加载 ${symbols.length} 只股票K线数据 (并发=${PREFETCH_CONCURRENCY})...`);
    let prefetched = 0;
    for (let i = 0; i < symbols.length; i += PREFETCH_CONCURRENCY) {
      if (signal?.aborted) break;
      const batch = symbols.slice(i, i + PREFETCH_CONCURRENCY);
      await Promise.all(batch.map((s) => timedGetKlines(s)));
      prefetched += batch.length;
      if (prefetched % 50 === 0 || prefetched === symbols.length) {
        console.log(`[Builtin Fitness] 已预加载 ${prefetched}/${symbols.length} 只股票K线`);
      }
    }
    console.log(`[Builtin Fitness] K线预加载完成, 开始回测 ${builtinStrategies.length} 个策略`);

    // Materialize cached bars into a map for workers
    allBars = new Map<string, KlineBar[]>();
    for (const sym of symbols) {
      const bars = await provider.getKlines(sym, "1d", startDate, endDate, "cn_stock").catch(() => []);
      if (bars.length > 0) allBars.set(sym, bars);
    }
  }
  const validSymbols = symbols.filter((s) => allBars.has(s));
  console.log(`[Builtin Fitness] ${validSymbols.length}/${symbols.length} 只股票有K线数据`);

  // Determine thread count: use all but 2 cores (reserve for system + Node main)
  const cpuCount = os.cpus().length;
  const threadCount = Math.max(1, Math.min(cpuCount - 2, 12));

  // Phase 2: Try multi-threaded, fallback to single-threaded
  let useWorkers = true;
  let pool: WorkerHandle[] = [];

  try {
    console.log(`[Builtin Fitness] 构建工作线程bundle...`);
    const bundlePath = await ensureWorkerBundle();
    console.log(`[Builtin Fitness] 启动 ${threadCount} 个工作线程 (CPU核心: ${cpuCount})`);
    pool = createWorkerPool(threadCount, bundlePath, config);

    // Wait for all workers to be ready
    await Promise.all(pool.map((h) => new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("Worker启动超时")), 15_000);
      h.worker.once("message", (msg: { type: string }) => {
        clearTimeout(timeout);
        if (msg.type === "ready") resolve();
      });
      h.worker.once("error", (err) => {
        clearTimeout(timeout);
        reject(err);
      });
    })));
    console.log(`[Builtin Fitness] ${threadCount} 个工作线程已就绪`);
  } catch (err) {
    const errMsg = err instanceof Error ? `${err.message}\n${err.stack}` : String(err);
    console.log(`[Builtin Fitness] 工作线程启动失败, 降级为单线程模式: ${errMsg}`);
    destroyPool(pool);
    pool = [];
    useWorkers = false;
  }

  if (useWorkers && pool.length > 0) {
    // ── Multi-threaded path ─────────────────────────────────────
    try {
    // Build task queue: all (strategy, symbol) pairs
    const taskQueue: Array<{ strategyId: string; symbol: string }> = [];
    for (const strategyId of builtinStrategies) {
      if (!hasStrategyFactory(strategyId)) {
        completed += symbols.length;
        continue;
      }
      for (const symbol of validSymbols) {
        taskQueue.push({ strategyId, symbol });
      }
    }

    // Process tasks in chunks distributed across workers
    const chunkSize = 32; // tasks per worker message
    let taskIdx = 0;

    while (taskIdx < taskQueue.length) {
      if (signal?.aborted) {
        console.log(`[Builtin Fitness] 已取消`);
        break;
      }

      // Distribute chunks to all workers
      const workerPromises: Promise<void>[] = [];

      for (const handle of pool) {
        if (taskIdx >= taskQueue.length) break;
        const chunk = taskQueue.slice(taskIdx, taskIdx + chunkSize);
        taskIdx += chunk.length;

        // Build tasks with bars data
        const tasks = chunk.map((t) => ({
          strategyId: t.strategyId,
          symbol: t.symbol,
          bars: allBars.get(t.symbol) ?? [],
          startDate,
          endDate,
          initialCash,
        }));

        handle.busy = true;
        workerPromises.push(
          new Promise<void>((resolve) => {
            handle.worker.once("message", (msg: {
              type: string;
              results?: Array<{
                strategyId: string;
                symbol: string;
                result: BacktestResult | null;
                error?: string;
              }>;
            }) => {
              if (msg.type === "results" && msg.results) {
                for (const wr of msg.results) {
                  if (!wr.result || (wr.result.totalTrades ?? 0) === 0) {
                    completed++;
                    continue;
                  }
                  const r = wr.result;
                  const cs = compositeScore({
                    totalReturn: (r.totalReturn ?? 0) * 100,
                    annualizedReturn: (r.annualizedReturn ?? 0) * 100,
                    sharpeRatio: r.sharpeRatio ?? 0,
                    winRate: (r.winRate ?? 0) * 100,
                    profitFactor: r.profitFactor ?? 0,
                    maxDrawdown: (r.maxDrawdown ?? 0) * 100,
                    totalTrades: r.totalTrades ?? 0,
                  });
                  completed++;
                  if (!Number.isFinite(cs)) continue;
                  results.push({
                    strategy_id: wr.strategyId,
                    symbol: wr.symbol,
                    composite_score: Math.round(cs * 10000) / 10000,
                    total_return: Math.round((r.totalReturn ?? 0) * 10000) / 10000,
                    annualized_return: Math.round((r.annualizedReturn ?? 0) * 10000) / 10000,
                    sharpe_ratio: Math.round((r.sharpeRatio ?? 0) * 10000) / 10000,
                    sortino_ratio: Math.round((r.sortinoRatio ?? 0) * 10000) / 10000,
                    max_drawdown: Math.round((r.maxDrawdown ?? 0) * 1000000) / 1000000,
                    win_rate: Math.round((r.winRate ?? 0) * 10000) / 10000,
                    profit_loss_ratio: Math.round((r.profitFactor ?? 0) * 10000) / 10000,
                    calmar_ratio: Math.round((r.calmarRatio ?? 0) * 10000) / 10000,
                    total_trades: r.totalTrades ?? 0,
                    backtest_start: r.startDate ?? startDate,
                    backtest_end: r.endDate ?? endDate,
                  });
                }
              }
              handle.busy = false;
              resolve();
            });
            handle.worker.postMessage({ type: "batch", tasks });
          }),
        );
      }

      await Promise.all(workerPromises);

      // Report progress after each round
      const currentStrategyIdx = Math.floor(completed / Math.max(validSymbols.length, 1));
      const currentStrategy = builtinStrategies[Math.min(currentStrategyIdx, builtinStrategies.length - 1)] ?? "";
      onProgress(completed, total, currentStrategy);
    }
    } finally {
      destroyPool(pool);
    }
  } else {
    // ── Single-threaded fallback ────────────────────────────────
    console.log(`[Builtin Fitness] 使用单线程模式回测`);
    // Build a provider that serves bars directly from the in-memory map
    const mapProvider: MarketDataProvider = {
      name: "map-backed",
      async getKlines(symbol: string) {
        return allBars.get(symbol) ?? [];
      },
      async getQuote() { return null; },
      async searchSymbols() { return []; },
    };
    for (const strategyId of builtinStrategies) {
      if (signal?.aborted) { console.log(`[Builtin Fitness] 已取消`); break; }
      if (!hasStrategyFactory(strategyId)) {
        completed += validSymbols.length;
        onProgress(completed, total, strategyId);
        continue;
      }
      const batchSize = 16;
      for (let i = 0; i < validSymbols.length; i += batchSize) {
        const batch = validSymbols.slice(i, i + batchSize);
        const promises = batch.map(async (symbol) => {
          try {
            const strategyFn = resolveBuiltinStrategy(strategyId);
            const result: BacktestResult = await runBuiltinBacktest(strategyFn, {
              strategyId, symbols: [symbol], startDate, endDate, initialCash, interval: "1d", market: "cn_stock",
            }, config, mapProvider);
            if (result.totalTrades === 0) return null;
            const cs = compositeScore({
              totalReturn: (result.totalReturn ?? 0) * 100, annualizedReturn: (result.annualizedReturn ?? 0) * 100,
              sharpeRatio: result.sharpeRatio ?? 0, winRate: (result.winRate ?? 0) * 100,
              profitFactor: result.profitFactor ?? 0, maxDrawdown: (result.maxDrawdown ?? 0) * 100,
              totalTrades: result.totalTrades ?? 0,
            });
            if (!Number.isFinite(cs)) return null;
            return {
              strategy_id: strategyId, symbol, composite_score: Math.round(cs * 10000) / 10000,
              total_return: Math.round((result.totalReturn ?? 0) * 10000) / 10000,
              annualized_return: Math.round((result.annualizedReturn ?? 0) * 10000) / 10000,
              sharpe_ratio: Math.round((result.sharpeRatio ?? 0) * 10000) / 10000,
              sortino_ratio: Math.round((result.sortinoRatio ?? 0) * 10000) / 10000,
              max_drawdown: Math.round((result.maxDrawdown ?? 0) * 1000000) / 1000000,
              win_rate: Math.round((result.winRate ?? 0) * 10000) / 10000,
              profit_loss_ratio: Math.round((result.profitFactor ?? 0) * 10000) / 10000,
              calmar_ratio: Math.round((result.calmarRatio ?? 0) * 10000) / 10000,
              total_trades: result.totalTrades ?? 0,
              backtest_start: result.startDate ?? startDate, backtest_end: result.endDate ?? endDate,
            };
          } catch { return null; }
        });
        const batchResults = await Promise.all(promises);
        for (const r of batchResults) { if (r) results.push(r); }
        completed += batch.length;
      }
      onProgress(completed, total, strategyId);
    }
  }

  return results;
}

async function mergeAndSave(
  batchId: string,
  allRawResults: Array<{ strategy_id: string; symbol: string; composite_score: number; [k: string]: unknown }>,
  topNPerStock: number,
  topNGlobal: number,
  storage?: QuantStorage,
): Promise<{ fitnessCount: number; globalRanking: GlobalRankRow[] }> {
  // Group by symbol
  const bySymbol = new Map<string, typeof allRawResults>();
  for (const r of allRawResults) {
    let arr = bySymbol.get(r.symbol);
    if (!arr) { arr = []; bySymbol.set(r.symbol, arr); }
    arr.push(r);
  }

  const stockFitness: FitnessRow[] = [];
  const strategyScores = new Map<string, number[]>();

  for (const [sym, items] of bySymbol) {
    items.sort((a, b) => (b.composite_score ?? 0) - (a.composite_score ?? 0));
    const top = items.slice(0, topNPerStock);
    for (let rank = 0; rank < top.length; rank++) {
      const m = top[rank];
      stockFitness.push({
        symbol: sym,
        stock_name: (m.stock_name as string) ?? "",
        strategy_id: m.strategy_id,
        rank: rank + 1,
        composite_score: m.composite_score,
        total_return: m.total_return as number,
        annualized_return: m.annualized_return as number,
        sharpe_ratio: m.sharpe_ratio as number,
        sortino_ratio: m.sortino_ratio as number,
        max_drawdown: m.max_drawdown as number,
        win_rate: m.win_rate as number,
        profit_loss_ratio: m.profit_loss_ratio as number,
        calmar_ratio: m.calmar_ratio as number,
        total_trades: m.total_trades as number,
        backtest_start: m.backtest_start as string,
        backtest_end: m.backtest_end as string,
      });

      let scores = strategyScores.get(m.strategy_id);
      if (!scores) { scores = []; strategyScores.set(m.strategy_id, scores); }
      scores.push(m.composite_score);
    }
  }

  // Global strategy ranking
  const strategyAgg: GlobalRankRow[] = [];
  for (const [sid, scores] of strategyScores) {
    const total = scores.reduce((a, b) => a + b, 0);
    strategyAgg.push({
      strategy_id: sid,
      rank: 0,
      total_score: Math.round(total * 10000) / 10000,
      stock_count: scores.length,
      avg_score: Math.round((total / scores.length) * 10000) / 10000,
      max_score: Math.round(Math.max(...scores) * 10000) / 10000,
      min_score: Math.round(Math.min(...scores) * 10000) / 10000,
    });
  }
  strategyAgg.sort((a, b) => b.total_score - a.total_score);
  const globalRanking = strategyAgg.slice(0, topNGlobal);
  globalRanking.forEach((item, i) => { item.rank = i + 1; });

  // Save to DB — prefer using the shared storage connection
  if (storage && "clearFitnessBatch" in storage) {
    try {
      await storage.clearFitnessBatch(batchId);
      const fitnessRows: StockStrategyFitnessRow[] = stockFitness.map((r) => ({
        symbol: r.symbol,
        stockName: r.stock_name,
        strategyId: r.strategy_id,
        strategyName: r.strategy_name,
        rank: r.rank,
        compositeScore: r.composite_score,
        totalReturn: r.total_return,
        annualizedReturn: r.annualized_return,
        sharpeRatio: r.sharpe_ratio,
        sortinoRatio: r.sortino_ratio,
        maxDrawdown: r.max_drawdown,
        winRate: r.win_rate,
        profitLossRatio: r.profit_loss_ratio,
        calmarRatio: r.calmar_ratio,
        totalTrades: r.total_trades,
        backtestStart: r.backtest_start,
        backtestEnd: r.backtest_end,
        batchId,
      }));
      await storage.saveStockStrategyFitness(fitnessRows);
      const globalRows: GlobalStrategyRankingRow[] = globalRanking.map((r) => ({
        strategyId: r.strategy_id,
        strategyName: r.strategy_name,
        rank: r.rank,
        totalScore: r.total_score,
        stockCount: r.stock_count,
        avgScore: r.avg_score,
        maxScore: r.max_score,
        minScore: r.min_score,
        batchId,
      }));
      await storage.saveGlobalStrategyRanking(globalRows);
      // Verify save actually persisted (InMemoryStorage has no-op fitness methods)
      const verifyBatch = await storage.getLatestFitnessBatchId();
      if (verifyBatch !== batchId) {
        throw new Error("Storage save did not persist (likely InMemoryStorage), falling back to standalone");
      }
      console.log(`[Fitness DB] saved via storage: ${stockFitness.length} fitness + ${globalRanking.length} ranking`);
    } catch (err) {
      console.error(`[Fitness DB] storage save failed, trying standalone:`, err);
      await saveFitnessStandalone(batchId, stockFitness, globalRanking);
    }
  } else {
    await saveFitnessStandalone(batchId, stockFitness, globalRanking);
  }

  return { fitnessCount: stockFitness.length, globalRanking };
}

/** Fallback: open a standalone DB connection to save fitness results */
async function saveFitnessStandalone(
  batchId: string,
  stockFitness: FitnessRow[],
  globalRanking: GlobalRankRow[],
): Promise<void> {
  const dbPath = getDbPath();
  const dir = path.dirname(dbPath);
  const fsSync = await import("node:fs");
  fsSync.mkdirSync(dir, { recursive: true });
  const sqlite3Mod = await import("better-sqlite3");
  const Database = sqlite3Mod.default ?? sqlite3Mod;
  const db = new (Database as unknown as new(p: string) => Record<string, Function>)(dbPath);
  try {
    // Set busy timeout to handle WAL lock contention
    db.pragma("busy_timeout = 30000");
    ensureFitnessTables(db as unknown as { exec(sql: string): void });

    db.prepare("DELETE FROM stock_strategy_fitness WHERE batch_id = ?").run(batchId);
    db.prepare("DELETE FROM global_strategy_ranking WHERE batch_id = ?").run(batchId);

    const insertFitness = db.prepare(`
      INSERT OR REPLACE INTO stock_strategy_fitness
        (symbol, stock_name, strategy_id, strategy_name, rank, composite_score,
         total_return, annualized_return, sharpe_ratio, sortino_ratio, max_drawdown,
         win_rate, profit_loss_ratio, calmar_ratio, total_trades,
         backtest_start, backtest_end, batch_id)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);

    const insertTx = db.transaction((rows: FitnessRow[]) => {
      for (const r of rows) {
        insertFitness.run(
          r.symbol, r.stock_name ?? null,
          r.strategy_id, r.strategy_name ?? null,
          r.rank, r.composite_score,
          r.total_return ?? null, r.annualized_return ?? null,
          r.sharpe_ratio ?? null, r.sortino_ratio ?? null,
          r.max_drawdown ?? null, r.win_rate ?? null,
          r.profit_loss_ratio ?? null, r.calmar_ratio ?? null,
          r.total_trades ?? null,
          r.backtest_start ?? null, r.backtest_end ?? null,
          batchId,
        );
      }
    });
    insertTx(stockFitness);

    const insertGlobal = db.prepare(`
      INSERT OR REPLACE INTO global_strategy_ranking
        (strategy_id, strategy_name, rank, total_score, stock_count,
         avg_score, max_score, min_score, batch_id)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    `);
    for (const r of globalRanking) {
      insertGlobal.run(
        r.strategy_id, r.strategy_name ?? null,
        r.rank, r.total_score, r.stock_count,
        r.avg_score ?? null, r.max_score ?? null, r.min_score ?? null,
        batchId,
      );
    }

    console.log(`[Fitness DB] saved via standalone: ${stockFitness.length} fitness + ${globalRanking.length} ranking`);
  } finally {
    db.close();
  }
}

export function registerStrategyFitnessRoutes(deps: AdminRouteDeps): void {
  const { api, config, secure, getEngine } = deps;

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
        const builtinStrategies = Array.isArray(body.builtin_strategies)
          ? (body.builtin_strategies as string[]).filter((s) => typeof s === "string" && s.length > 0)
          : [];
        const includeAlpha = body.include_alpha !== false;
        const topNPerStock = Number(body.top_n_per_stock) || 5;
        const topNGlobal = Number(body.top_n_global) || 10;
        const initialCapital = Number(body.initial_capital) || 1_000_000;
        const backtestYears = Number(body.backtest_years) || 5;
        const hasBuiltin = builtinStrategies.length > 0;

        if (!includeAlpha && !hasBuiltin) {
          json(res, { ok: false, error: "请至少选择一类策略 (Alpha或Builtin)" });
          return;
        }

        // Immediately reset progress file so stale results from previous runs don't show
        await writeProgress(config, {
          status: "starting",
          phase: "正在初始化...",
          total_stocks: 0,
          completed_stocks: 0,
          elapsed_seconds: 0,
        });

        let batchId = "";
        let validSymbols: string[] = [];
        let startDate = "";
        let endDate = "";
        let alphaResults: Array<Record<string, unknown>> = [];
        let alphaStrategyCount = 0;
        let totalElapsed = 0;
        let klinesFile = "";  // Temp file with pre-loaded klines from Python

        // Phase 1: Python alpha strategies (if enabled)
        if (includeAlpha || hasBuiltin) {
          // Always call Python to get the stock universe (and alpha results if enabled)
          const pyResult = await runPythonScript<Record<string, unknown>>(
            "scripts/strategy_fitness.py",
            {
              action: "run",
              universe: (body.universe as string) ?? "csi1000",
              custom_symbols: body.custom_symbols ?? [],
              backtest_years: backtestYears,
              top_n_per_stock: topNPerStock,
              top_n_global: topNGlobal,
              workers: Number(body.workers) || 8,
              initial_capital: initialCapital,
              include_alpha: includeAlpha,
              has_builtin: hasBuiltin,
            },
            config,
            { timeoutMs: 4 * 3600_000 },
          );

          if (!pyResult.ok) {
            json(res, { ok: false, error: pyResult.error });
            return;
          }

          const pyData = pyResult.data as Record<string, unknown>;

          // Python may return { error, batch_id } with exit code 0 — detect & propagate
          if (pyData.error) {
            await writeProgress(config, {
              batch_id: (pyData.batch_id as string) ?? "",
              status: "error",
              error: pyData.error,
            });
            json(res, { ok: false, error: pyData.error as string });
            return;
          }

          batchId = (pyData.batch_id as string) ?? "";
          alphaStrategyCount = (pyData.total_strategies as number) ?? 0;
          totalElapsed = (pyData.elapsed_seconds as number) ?? 0;

          if (pyData._phase === "alpha_only" || pyData._phase === "metadata_only") {
            // Python returned intermediate result with universe for TS to continue
            validSymbols = (pyData.valid_symbols as string[]) ?? [];
            startDate = (pyData.start_date as string) ?? "";
            endDate = (pyData.end_date as string) ?? "";
            alphaResults = (pyData.alpha_results as Array<Record<string, unknown>>) ?? [];
            klinesFile = (pyData.klines_file as string) ?? "";
          } else {
            // Python handled everything (no builtin strategies)
            json(res, { ok: true, ...pyData });
            return;
          }
        }

        // Phase 2: TS builtin strategies
        if (hasBuiltin && validSymbols.length > 0) {
          fitnessAbort = new AbortController();
          await writeProgress(config, {
            batch_id: batchId,
            status: "running",
            phase: `Builtin策略回测: 0/${builtinStrategies.length}`,
            total_stocks: validSymbols.length,
            total_strategies: alphaStrategyCount + builtinStrategies.length,
            completed_strategies: alphaStrategyCount,
            elapsed_seconds: totalElapsed,
          });

          // Load pre-fetched klines from Python temp file (avoids re-fetching from bridge)
          let preloadedBars: Map<string, KlineBar[]> | undefined;
          if (klinesFile) {
            const loaded = await loadPreloadedKlines(klinesFile);
            if (loaded && loaded.size > 0) {
              preloadedBars = loaded;
            }
          }

          const t0 = Date.now();
          const builtinResults = await runBuiltinFitness(
            builtinStrategies,
            validSymbols,
            startDate,
            endDate,
            initialCapital,
            config,
            (completed, total, current) => {
              const builtinDone = Math.floor(completed / validSymbols.length);
              writeProgress(config, {
                batch_id: batchId,
                status: "running",
                phase: `Builtin策略回测: ${builtinDone}/${builtinStrategies.length}`,
                total_stocks: validSymbols.length,
                total_strategies: alphaStrategyCount + builtinStrategies.length,
                completed_strategies: alphaStrategyCount + builtinDone,
                current_strategy: current,
                elapsed_seconds: Math.round(totalElapsed + (Date.now() - t0) / 1000),
              });
            },
            fitnessAbort!.signal,
            preloadedBars,
          );
          fitnessAbort = null;

          const builtinElapsed = Math.round((Date.now() - t0) / 1000);
          totalElapsed += builtinElapsed;

          // Phase 3: Merge alpha + builtin results, rank, save
          const allRawResults = [...alphaResults, ...builtinResults] as Array<{
            strategy_id: string; symbol: string; composite_score: number; [k: string]: unknown;
          }>;

          const { fitnessCount, globalRanking } = await mergeAndSave(
            batchId,
            allRawResults,
            topNPerStock,
            topNGlobal,
            deps.dataWarehouse?.storage,
          );

          await writeProgress(config, {
            batch_id: batchId,
            status: "done",
            total_stocks: validSymbols.length,
            completed_stocks: validSymbols.length,
            total_strategies: alphaStrategyCount + builtinStrategies.length,
            elapsed_seconds: totalElapsed,
            results: {
              fitness_records: fitnessCount,
              global_ranking_count: globalRanking.length,
            },
          });

          json(res, {
            ok: true,
            batch_id: batchId,
            universe: (body.universe as string) ?? "csi1000",
            total_stocks: validSymbols.length,
            total_strategies: alphaStrategyCount + builtinStrategies.length,
            alpha_strategies: alphaStrategyCount,
            builtin_strategies: builtinStrategies.length,
            fitness_records: fitnessCount,
            elapsed_seconds: totalElapsed,
            global_top_strategies: globalRanking,
          });
        } else if (hasBuiltin && validSymbols.length === 0) {
          await writeProgress(config, { batch_id: batchId, status: "error", error: "无有效股票，无法运行Builtin策略" });
          json(res, { ok: false, error: "无有效股票，无法运行Builtin策略" });
        }
      } catch (err) {
        const errMsg = `适配分析失败: ${err instanceof Error ? err.message : String(err)}`;
        await writeProgress(config, { status: "error", error: errMsg }).catch(() => {});
        json(res, { ok: false, error: errMsg }, 500);
      }
    }),
  });

  // ── Stop/cancel running fitness ────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/stop",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      if (fitnessAbort) {
        fitnessAbort.abort();
        await writeProgress(config, { status: "cancelled", phase: "用户取消" });
        json(res, { ok: true, message: "已发送取消信号" });
      } else {
        json(res, { ok: false, message: "当前没有正在运行的Builtin适配分析" });
      }
    }),
  });

  // ── List available AIPHA A-stock strategies ────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/aipha-strategies",
    handler: secure(async (_req, res) => {
      try {
        const result = await runPythonScript<{
          strategies: Array<{ id: string; name: string }>;
          count: number;
          error?: string;
        }>(
          "scripts/strategy_fitness.py",
          { action: "list_strategies" },
          config,
          { timeoutMs: 30_000 },
        );
        if (result.ok && result.data) {
          // Cache into global allowed strategies for daily-pick validation
          if (result.data.strategies?.length) {
            setAiphaStrategies(result.data.strategies);
          }
          json(res, result.data);
        } else {
          json(res, { strategies: [], count: 0, error: result.error ?? "加载失败" });
        }
      } catch (err) {
        json(res, { strategies: [], count: 0, error: String(err) }, 500);
      }
    }),
  });

  // ── Query progress ─────────────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/status",
    handler: secure(async (_req, res) => {
      try {
        // Read progress directly from JSON file (fast, no Python spawn)
        const progressPath = getProgressPath(config);
        const raw = await fs.readFile(progressPath, "utf-8").catch(() => "");
        if (!raw) {
          json(res, { status: "idle" });
          return;
        }
        json(res, JSON.parse(raw));
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

  // ── Strategy correlation preview (lightweight, from fitness scores) ──
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/correlation-preview",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const strategyIds = Array.isArray(body.strategy_ids) ? (body.strategy_ids as string[]) : [];
        const batchId = (body.batch_id as string) ?? "";
        if (strategyIds.length < 2) { json(res, { error: "至少选择 2 个策略" }); return; }

        const dbPath = getDbPath();
        const fsSync = await import("node:fs");
        if (!fsSync.existsSync(dbPath)) { json(res, { error: "数据库不存在" }); return; }
        const sqlite3Mod = await import("better-sqlite3");
        const Database = sqlite3Mod.default ?? sqlite3Mod;
        const db = new (Database as unknown as new (p: string) => Record<string, Function>)(dbPath);
        try {
          db.pragma("busy_timeout = 10000");
          // Resolve batch_id if not provided
          let bid = batchId;
          if (!bid) {
            const row = db.prepare("SELECT batch_id FROM global_strategy_ranking ORDER BY created_at DESC LIMIT 1").get() as { batch_id: string } | undefined;
            bid = row?.batch_id ?? "";
          }
          if (!bid) { json(res, { error: "无批次数据" }); return; }

          // Load composite_score vectors: {strategy_id: {symbol: score}}
          const placeholders = strategyIds.map(() => "?").join(",");
          const rows = db.prepare(
            `SELECT strategy_id, symbol, composite_score FROM stock_strategy_fitness WHERE batch_id = ? AND strategy_id IN (${placeholders})`
          ).all(bid, ...strategyIds) as { strategy_id: string; symbol: string; composite_score: number }[];

          // Build score vectors per strategy
          const scoreMap: Record<string, Record<string, number>> = {};
          const allSymbols = new Set<string>();
          for (const r of rows) {
            if (!scoreMap[r.strategy_id]) scoreMap[r.strategy_id] = {};
            scoreMap[r.strategy_id][r.symbol] = r.composite_score;
            allSymbols.add(r.symbol);
          }
          const symbols = Array.from(allSymbols);

          // Compute Pearson correlation between each pair
          function pearson(a: number[], b: number[]): number {
            const n = a.length;
            if (n < 3) return 0;
            const ma = a.reduce((s, v) => s + v, 0) / n;
            const mb = b.reduce((s, v) => s + v, 0) / n;
            let num = 0, da = 0, db2 = 0;
            for (let i = 0; i < n; i++) {
              const da2 = a[i] - ma, db3 = b[i] - mb;
              num += da2 * db3; da += da2 * da2; db2 += db3 * db3;
            }
            const denom = Math.sqrt(da * db2);
            return denom > 1e-12 ? num / denom : 0;
          }

          const matrix: Record<string, Record<string, number>> = {};
          const pairCorrs: { a: string; b: string; corr: number }[] = [];
          for (const si of strategyIds) {
            matrix[si] = {};
            for (const sj of strategyIds) {
              if (si === sj) { matrix[si][sj] = 1; continue; }
              // Find common symbols
              const va: number[] = [], vb: number[] = [];
              for (const sym of symbols) {
                const a = scoreMap[si]?.[sym], b = scoreMap[sj]?.[sym];
                if (a !== undefined && b !== undefined) { va.push(a); vb.push(b); }
              }
              const corr = pearson(va, vb);
              matrix[si][sj] = Math.round(corr * 1000) / 1000;
              if (si < sj) pairCorrs.push({ a: si, b: sj, corr: Math.round(corr * 1000) / 1000 });
            }
          }

          // Statistics
          const absCorrs = pairCorrs.map(p => Math.abs(p.corr));
          const avgCorr = absCorrs.length > 0 ? absCorrs.reduce((a, b) => a + b, 0) / absCorrs.length : 0;
          const maxPair = pairCorrs.reduce((best, p) => Math.abs(p.corr) > Math.abs(best.corr) ? p : best, pairCorrs[0]);
          const highCorr = pairCorrs.filter(p => Math.abs(p.corr) > 0.5);

          // Recommendation: find low-correlation alternatives from ranking
          let alternatives: string[] = [];
          if (highCorr.length > 0) {
            const allStrats = db.prepare(
              "SELECT strategy_id FROM global_strategy_ranking WHERE batch_id = ? ORDER BY rank ASC"
            ).all(bid) as { strategy_id: string }[];
            const notSelected = allStrats.filter(s => !strategyIds.includes(s.strategy_id)).map(s => s.strategy_id);
            // For each unselected strategy, compute avg correlation with selected ones
            const altCorrs: { id: string; avgCorr: number }[] = [];
            for (const alt of notSelected) {
              const altScores = scoreMap[alt] || {};
              // Load if not in scoreMap
              if (!scoreMap[alt]) {
                const altRows = db.prepare(
                  "SELECT symbol, composite_score FROM stock_strategy_fitness WHERE batch_id = ? AND strategy_id = ?"
                ).all(bid, alt) as { symbol: string; composite_score: number }[];
                const m: Record<string, number> = {};
                for (const r of altRows) m[r.symbol] = r.composite_score;
                scoreMap[alt] = m;
              }
              const corrs: number[] = [];
              for (const si of strategyIds) {
                const va: number[] = [], vb: number[] = [];
                for (const sym of symbols) {
                  const a = scoreMap[si]?.[sym], b = scoreMap[alt]?.[sym];
                  if (a !== undefined && b !== undefined) { va.push(a); vb.push(b); }
                }
                if (va.length >= 3) corrs.push(Math.abs(pearson(va, vb)));
              }
              if (corrs.length > 0) {
                altCorrs.push({ id: alt, avgCorr: corrs.reduce((a, b) => a + b, 0) / corrs.length });
              }
            }
            altCorrs.sort((a, b) => a.avgCorr - b.avgCorr);
            alternatives = altCorrs.slice(0, 3).map(a => a.id + ' (平均相关: ' + a.avgCorr.toFixed(3) + ')');
          }

          json(res, {
            matrix,
            avg_correlation: Math.round(avgCorr * 1000) / 1000,
            max_pair: maxPair ? { a: maxPair.a, b: maxPair.b, corr: maxPair.corr } : null,
            high_correlation_pairs: highCorr,
            verdict: avgCorr < 0.3 ? 'LOW' : avgCorr < 0.5 ? 'MODERATE' : 'HIGH',
            alternatives,
            n_common_symbols: symbols.length,
          });
        } finally {
          (db as unknown as { close(): void }).close();
        }
      } catch (err) {
        json(res, { error: String(err) }, 500);
      }
    }),
  });

  // ── Auto-select low-correlation strategies ─────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/auto-select",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const batchId = (body.batch_id as string) ?? "";
        const targetCount = Math.min(Math.max(Number(body.count) || 5, 2), 10);
        const maxAvgCorr = Number(body.max_avg_corr) || 0.4;

        const dbPath = getDbPath();
        const fsSync = await import("node:fs");
        if (!fsSync.existsSync(dbPath)) { json(res, { error: "数据库不存在" }); return; }
        const sqlite3Mod = await import("better-sqlite3");
        const Database = sqlite3Mod.default ?? sqlite3Mod;
        const db = new (Database as unknown as new (p: string) => Record<string, Function>)(dbPath);
        try {
          db.pragma("busy_timeout = 10000");
          let bid = batchId;
          if (!bid) {
            const row = db.prepare("SELECT batch_id FROM global_strategy_ranking ORDER BY created_at DESC LIMIT 1").get() as { batch_id: string } | undefined;
            bid = row?.batch_id ?? "";
          }
          if (!bid) { json(res, { error: "无批次数据" }); return; }

          // Load all strategies sorted by rank
          const allStrats = db.prepare(
            "SELECT strategy_id, rank, total_score FROM global_strategy_ranking WHERE batch_id = ? ORDER BY rank ASC"
          ).all(bid) as { strategy_id: string; rank: number; total_score: number }[];
          if (allStrats.length < 2) { json(res, { error: "策略不足" }); return; }

          // Load all fitness scores
          const rows = db.prepare(
            "SELECT strategy_id, symbol, composite_score FROM stock_strategy_fitness WHERE batch_id = ?"
          ).all(bid) as { strategy_id: string; symbol: string; composite_score: number }[];
          const scoreMap: Record<string, Record<string, number>> = {};
          const allSymbols = new Set<string>();
          for (const r of rows) {
            if (!scoreMap[r.strategy_id]) scoreMap[r.strategy_id] = {};
            scoreMap[r.strategy_id][r.symbol] = r.composite_score;
            allSymbols.add(r.symbol);
          }
          const symbols = Array.from(allSymbols);

          function pearson(a: number[], b: number[]): number {
            const n = a.length;
            if (n < 3) return 0;
            const ma = a.reduce((s, v) => s + v, 0) / n;
            const mb = b.reduce((s, v) => s + v, 0) / n;
            let num = 0, da = 0, db2 = 0;
            for (let i = 0; i < n; i++) {
              const x = a[i] - ma, y = b[i] - mb;
              num += x * y; da += x * x; db2 += y * y;
            }
            const denom = Math.sqrt(da * db2);
            return denom > 1e-12 ? num / denom : 0;
          }

          function corrBetween(s1: string, s2: string): number {
            const va: number[] = [], vb: number[] = [];
            for (const sym of symbols) {
              const a = scoreMap[s1]?.[sym], b = scoreMap[s2]?.[sym];
              if (a !== undefined && b !== undefined) { va.push(a); vb.push(b); }
            }
            return pearson(va, vb);
          }

          function avgAbsCorr(selected: string[]): number {
            if (selected.length < 2) return 0;
            let sum = 0, count = 0;
            for (let i = 0; i < selected.length; i++) {
              for (let j = i + 1; j < selected.length; j++) {
                sum += Math.abs(corrBetween(selected[i], selected[j]));
                count++;
              }
            }
            return count > 0 ? sum / count : 0;
          }

          // Greedy: start with #1 ranked, then add next best that keeps avg corr low
          const selected: string[] = [allStrats[0].strategy_id];
          const remaining = allStrats.slice(1);

          while (selected.length < targetCount && remaining.length > 0) {
            let bestIdx = -1;
            let bestCorr = Infinity;
            for (let i = 0; i < remaining.length; i++) {
              const candidate = [...selected, remaining[i].strategy_id];
              const ac = avgAbsCorr(candidate);
              if (ac < bestCorr) {
                bestCorr = ac;
                bestIdx = i;
              }
            }
            if (bestIdx < 0) break;
            // Accept if under threshold, or if we haven't reached minimum 3 strategies
            if (bestCorr <= maxAvgCorr || selected.length < 3) {
              selected.push(remaining[bestIdx].strategy_id);
              remaining.splice(bestIdx, 1);
            } else {
              // Try remaining ones — maybe a lower-ranked one has lower corr
              remaining.splice(bestIdx, 1);
              if (remaining.length === 0) break;
            }
          }

          // Build correlation matrix for selected
          const matrix: Record<string, Record<string, number>> = {};
          for (const si of selected) {
            matrix[si] = {};
            for (const sj of selected) {
              matrix[si][sj] = si === sj ? 1 : Math.round(corrBetween(si, sj) * 1000) / 1000;
            }
          }

          const finalCorr = avgAbsCorr(selected);
          const stratInfo = selected.map(id => {
            const info = allStrats.find(s => s.strategy_id === id);
            return { strategy_id: id, rank: info?.rank ?? 0, total_score: info?.total_score ?? 0 };
          });

          json(res, {
            selected: selected,
            strategy_info: stratInfo,
            matrix,
            avg_correlation: Math.round(finalCorr * 1000) / 1000,
            verdict: finalCorr < 0.3 ? 'LOW' : finalCorr < 0.5 ? 'MODERATE' : 'HIGH',
            target_count: targetCount,
            max_avg_corr: maxAvgCorr,
          });
        } finally {
          (db as unknown as { close(): void }).close();
        }
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

  // ── Delete a fitness batch ─────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/delete-batch",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const batchId = (body.batch_id as string) ?? "";
        if (!batchId) {
          json(res, { error: "需要 batch_id 参数" }, 400);
          return;
        }
        // Always use Python for delete – MemoryStorage's clearFitnessBatch is a no-op
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          { action: "delete_batch", batch_id: batchId },
          config,
          { timeoutMs: 15_000 },
        );
        if (result.ok) {
          // Also clear from TS storage if available (best-effort)
          const storage = deps.dataWarehouse?.storage;
          if (storage && "clearFitnessBatch" in storage) {
            try { await (storage as { clearFitnessBatch(b: string): Promise<void> }).clearFitnessBatch(batchId); } catch { /* ignore */ }
          }
          json(res, { ok: true, ...(result.data as object) });
        } else {
          json(res, { error: result.error });
        }
      } catch (err) {
        json(res, { error: String(err) }, 500);
      }
    }),
  });

  // ── Aggregate global ranking across all batches ────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/aggregate-global",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const topN = Math.min(Math.max(parseInt(url.searchParams.get("top_n") ?? "10", 10) || 10, 1), 50);
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/strategy_fitness.py",
          { action: "aggregate_global", top_n: topN },
          config,
          { timeoutMs: 15_000 },
        );
        json(res, result.ok ? { ok: true, ...(result.data as object) } : { error: result.error });
      } catch (err) {
        json(res, { error: String(err) }, 500);
      }
    }),
  });

  // ── Top-N Portfolio Combo Backtest ─────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/portfolio-backtest",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const result = await runPythonScript<Record<string, unknown>>(
          "aipha/backtest/multi_strategy_backtest.py",
          {
            action: "run_top10",
            batch_id: (body.batch_id as string) ?? "",
            top_n: Number(body.top_n) || 10,
            allocation_mode: (body.allocation_mode as string) ?? "equal",
            rebalance_freq: (body.rebalance_freq as string) ?? "monthly",
            period_days: Number(body.period_days) || 252,
            initial_capital: Number(body.initial_capital) || 1_000_000,
            custom_weights: Array.isArray(body.custom_weights) ? body.custom_weights : null,
            strategy_ids: Array.isArray(body.strategy_ids) ? body.strategy_ids : null,
          },
          config,
          { timeoutMs: 10 * 60_000 },
        );
        json(res, result.ok ? result.data : { ok: false, error: result.error });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Portfolio backtest progress status ─────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/portfolio-backtest/status",
    handler: secure(async (_req, res) => {
      try {
        const pythonDir = getPythonDir();
        const logsDir = path.join(path.dirname(pythonDir), "logs");
        const progressPath = path.join(logsDir, "combo-backtest-progress.json");
        const raw = await fs.readFile(progressPath, "utf-8").catch(() => "");
        if (!raw) {
          json(res, { status: "idle" });
          return;
        }
        json(res, JSON.parse(raw));
      } catch (err) {
        json(res, { status: "error", error: String(err) }, 500);
      }
    }),
  });

  // ── Apply combo result to paper trading ────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/portfolio-backtest/apply-paper",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const allocations = body.allocations;
        if (!Array.isArray(allocations) || allocations.length === 0) {
          json(res, { ok: false, error: "需要 allocations 数组" }, 400);
          return;
        }
        const result = await applyComboToPaper(
          {
            allocations: allocations as Array<{ strategy_name: string; weight: number; latest_signals?: unknown[] }>,
            dryRun: body.dry_run === true,
            executePct: Number(body.execute_pct) || 100,
            createConditionalOrders: body.create_conditional_orders === true,
            trailingStopPct: Number(body.trailing_stop_pct) || 5,
          },
          config,
        );

        // Auto-configure the auto-trade scheduler with combo strategies
        let autoTradeConfigured = false;
        console.log(`[apply-paper] result.ok=${result.ok}, hasScheduler=${!!deps.autoTradeScheduler}`);
        if (deps.autoTradeScheduler) {
          try {
            const strategyNames = (allocations as Array<{ strategy_name: string }>)
              .map((a) => a.strategy_name)
              .filter(Boolean);
            console.log(`[apply-paper] strategyNames=${JSON.stringify(strategyNames)}, initial_capital=${body.initial_capital}`);
            if (strategyNames.length > 0) {
              // Fitness strategies use "fitness_optimal" as the daily-pick strategy key,
              // with the actual strategy IDs passed via aipha_strategies.
              deps.autoTradeScheduler.updateConfig({
                strategies: ["fitness_optimal"],
                initialCapital: Number(body.initial_capital) || 10_000_000,
                dryRun: true, // always start in dry-run mode for safety
                useFitnessStrategies: false, // use explicit strategies
              });
              // Store aipha strategy names for the screen phase to pick up
              (deps.autoTradeScheduler as unknown as Record<string, unknown>)._aiphaStrategies = strategyNames;
              autoTradeConfigured = true;
              console.log(`[apply-paper] autoTradeScheduler configured with fitness_optimal + ${strategyNames.length} aipha strategies`);
            }
          } catch (configErr) {
            console.error(`[apply-paper] autoTradeScheduler config error:`, configErr);
          }
        }

        json(res, { ...result, autoTradeConfigured });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Apply combo result to LIVE trading ─────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/portfolio-backtest/apply-live",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const allocations = body.allocations;
        if (!Array.isArray(allocations) || allocations.length === 0) {
          json(res, { ok: false, error: "需要 allocations 数组" }, 400);
          return;
        }

        // Verify broker is connected and not paper
        const engine = getEngine?.();
        const broker = engine?.broker;
        const currentBrokerType = broker?.brokerType;
        if (!broker || !currentBrokerType) {
          json(res, { ok: false, error: "交易引擎未启动或 broker 未连接，无法执行实盘操作" }, 400);
          return;
        }
        if (currentBrokerType === "paper") {
          json(res, { ok: false, error: "当前引擎为 Paper 模式，请先连接实盘 broker (QMT/富途等) 再执行实盘操作" }, 400);
          return;
        }

        const result = await applyComboToPaper(
          {
            allocations: allocations as Array<{ strategy_name: string; weight: number; latest_signals?: unknown[] }>,
            dryRun: false,
            executePct: Number(body.execute_pct) || 100,
            createConditionalOrders: body.create_conditional_orders === true,
            trailingStopPct: Number(body.trailing_stop_pct) || 5,
          },
          config,
        );

        // Auto-configure the auto-trade scheduler for live
        let autoTradeConfigured = false;
        if (result.ok && deps.autoTradeScheduler) {
          try {
            const strategyNames = (allocations as Array<{ strategy_name: string }>)
              .map((a) => a.strategy_name)
              .filter(Boolean);
            if (strategyNames.length > 0) {
              deps.autoTradeScheduler.updateConfig({
                strategies: ["fitness_optimal"],
                initialCapital: Number(body.initial_capital) || 10_000_000,
                dryRun: false, // live mode
                useFitnessStrategies: false,
              });
              (deps.autoTradeScheduler as unknown as Record<string, unknown>)._aiphaStrategies = strategyNames;
              autoTradeConfigured = true;
              console.log(`[apply-live] autoTradeScheduler configured for LIVE with ${strategyNames.length} strategies`);
            }
          } catch (configErr) {
            console.error(`[apply-live] autoTradeScheduler config error:`, configErr);
          }
        }

        json(res, { ...result, autoTradeConfigured, brokerType: currentBrokerType, mode: "live" });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Compare allocation modes side-by-side ──────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/portfolio-backtest/compare",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const modes = Array.isArray(body.modes)
          ? (body.modes as string[]).filter((s) => typeof s === "string")
          : ["equal", "risk_parity", "max_sharpe", "min_variance"];
        const result = await runPythonScript<Record<string, unknown>>(
          "aipha/backtest/multi_strategy_backtest.py",
          {
            action: "compare_allocations",
            batch_id: (body.batch_id as string) ?? "",
            top_n: Number(body.top_n) || 10,
            modes,
            rebalance_freq: (body.rebalance_freq as string) ?? "monthly",
            period_days: Number(body.period_days) || 252,
            initial_capital: Number(body.initial_capital) || 1_000_000,
          },
          config,
          { timeoutMs: 30 * 60_000 },
        );
        json(res, result.ok ? result.data : { ok: false, error: result.error });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── List stock pools (for pool backtest UI) ─────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/pools",
    handler: secure(async (_req, res) => {
      try {
        const result = await runPythonScript<Record<string, unknown>>(
          "scripts/pool_optimizer.py",
          { action: "list_pools" },
          config,
          { timeoutMs: 60_000 },
        );
        json(res, result.ok ? result.data : { ok: false, error: result.error });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Pool × Strategy Backtest (run) ─────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/pool-backtest",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const result = await runPythonScript<Record<string, unknown>>(
          "aipha/backtest/multi_strategy_backtest.py",
          {
            action: "run_pool_backtest",
            pool_configs: body.pool_configs ?? [],
            period_years: Number(body.period_years) || 5,
            rebalance_days: Number(body.rebalance_days) || 5,
            initial_capital: Number(body.initial_capital) || 1_000_000,
          },
          config,
          { timeoutMs: 30 * 60_000 },
        );
        json(res, result.ok ? result.data : { ok: false, error: result.error });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Pool backtest progress ─────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/pool-backtest/status",
    handler: secure(async (_req, res) => {
      try {
        const pythonDir = getPythonDir();
        const logsDir = path.join(path.dirname(pythonDir), "logs");
        const progressPath = path.join(logsDir, "pool-backtest-progress.json");
        const raw = await fs.readFile(progressPath, "utf-8").catch(() => "");
        if (!raw) { json(res, { status: "idle" }); return; }
        json(res, JSON.parse(raw));
      } catch (err) {
        json(res, { status: "error", error: String(err) }, 500);
      }
    }),
  });

  // ── Pool backtest history ──────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/pool-backtest/history",
    handler: secure(async (_req, res) => {
      try {
        const result = await runPythonScript<Record<string, unknown>>(
          "aipha/backtest/multi_strategy_backtest.py",
          { action: "pool_backtest_history", limit: 100 },
          config,
          { timeoutMs: 15_000 },
        );
        json(res, result.ok ? result.data : { ok: false, error: result.error });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Pool backtest delete ───────────────────────────────────
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/pool-backtest/delete",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const result = await runPythonScript<Record<string, unknown>>(
          "aipha/backtest/multi_strategy_backtest.py",
          { action: "pool_backtest_delete", run_id: body.run_id ?? "" },
          config,
          { timeoutMs: 15_000 },
        );
        json(res, result.ok ? result.data : { ok: false, error: result.error });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Pool backtest apply → register as live strategy ────────
  const POOL_STRATEGY_MAP: Record<string, string> = {
    momentum: "momentum-breakout",
    mixed: "dual-ma-crossover",
  };

  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/pool-backtest/apply",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const runId = String(body.run_id ?? "");
        const poolKey = String(body.pool_key ?? "");
        const strategyType = String(body.strategy_type ?? "");
        const capitalBudget = body.capital_budget != null ? Number(body.capital_budget) : undefined;

        if (!runId || !poolKey || !strategyType) {
          json(res, { ok: false, error: "run_id, pool_key, strategy_type 必填" }, 400);
          return;
        }

        // 1. Query combo details from DB via Python
        const pyResult = await runPythonScript<{
          combo?: {
            symbols_list?: string[];
            pool_label?: string;
            strategy_type?: string;
            annualized_return_pct?: number;
            sharpe?: number;
            symbol_count?: number;
          };
        }>(
          "aipha/backtest/multi_strategy_backtest.py",
          { action: "pool_backtest_get_combo", run_id: runId, pool_key: poolKey, strategy_type: strategyType },
          config,
          { timeoutMs: 15_000 },
        );

        if (!pyResult.ok || !pyResult.data?.combo) {
          json(res, { ok: false, error: pyResult.error ?? "未找到该回测记录" });
          return;
        }

        const combo = pyResult.data.combo;
        const symbols = combo.symbols_list ?? [];
        if (symbols.length === 0) {
          json(res, { ok: false, error: "该组合无有效股票" });
          return;
        }

        // 2. Map strategy_type to live strategy ID
        const liveStrategyId = POOL_STRATEGY_MAP[strategyType];
        if (!liveStrategyId) {
          json(res, { ok: false, error: `不支持的策略类型: ${strategyType}` });
          return;
        }

        // 3. Duplicate check
        const engine = getEngine();
        const existing = engine.strategyRegistry.list().find(
          (s) => (s.parameters as Record<string, unknown> | undefined)?.source === "pool-backtest"
            && (s.parameters as Record<string, unknown> | undefined)?.run_id === runId
            && (s.parameters as Record<string, unknown> | undefined)?.pool_key === poolKey,
        );
        if (existing) {
          json(res, { ok: false, error: `该组合已应用，策略 ID: ${existing.id}` });
          return;
        }

        // 4. Register as live strategy (auto-persists + hot-reloads)
        const entry = await engine.addStrategy(liveStrategyId, symbols, {
          source: "pool-backtest",
          run_id: runId,
          pool_key: poolKey,
          pool_label: combo.pool_label ?? "",
          strategy_type: strategyType,
          rebalanceDays: 5,
          annualized_return_pct: combo.annualized_return_pct ?? 0,
          sharpe: combo.sharpe ?? 0,
        }, {
          capitalBudget: capitalBudget && capitalBudget > 0 ? capitalBudget : undefined,
        });

        const brokerType = engine.broker?.brokerType ?? "paper";
        json(res, {
          ok: true,
          strategy: entry,
          brokerType,
          message: `已应用「${combo.pool_label ?? poolKey} × ${strategyType}」，共 ${symbols.length} 只股票`,
        });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Fitness auto-assign: per-stock best strategy → live strategies ──
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/auto-assign",
    handler: secure(async (req, res) => {
      if (req.method !== "POST") { json(res, { error: "仅支持 POST" }, 405); return; }
      try {
        const body = (await parseObjectBody(req)) as Record<string, unknown>;
        const batchId = body.batch_id ? String(body.batch_id) : undefined;
        const capitalBudgetPerStrategy = body.capital_budget_per_strategy != null
          ? Number(body.capital_budget_per_strategy) : undefined;
        const replaceExisting = body.replace_existing !== false; // default true
        const topN = Math.max(1, Math.min(100, Number(body.top_n) || 10));

        // 1. Load all rank=1 fitness records (per-stock best strategy)
        const storage = deps.dataWarehouse?.storage;
        if (!storage || !("getAllStocksTopStrategy" in storage)) {
          json(res, { ok: false, error: "存储层不可用，无法读取 fitness 数据" }, 500);
          return;
        }
        const typed = storage as {
          getAllStocksTopStrategy(b?: string, l?: number, o?: number): Promise<{ rows: StockStrategyFitnessRow[]; total: number }>;
          getLatestFitnessBatchId(): Promise<string | null>;
        };
        const usedBatchId = batchId ?? await typed.getLatestFitnessBatchId();
        if (!usedBatchId) {
          json(res, { ok: false, error: "无 fitness 分析数据，请先运行策略适配分析" });
          return;
        }
        // Fetch all rank=1 rows (up to 5000 stocks)
        const { rows, total } = await typed.getAllStocksTopStrategy(usedBatchId, 5000, 0);
        if (!rows.length) {
          json(res, { ok: false, error: "该批次无 fitness 数据" });
          return;
        }

        // 2. Load pool boost and group stocks by their best strategy
        const poolBoost = loadPoolBoostMap();
        const maxPoolScore = poolBoost.size > 0
          ? Math.max(...Array.from(poolBoost.values()).map((e) => e.poolScore))
          : 0;
        const strategyGroups = new Map<string, { symbols: string[]; avgScore: number; scores: number[] }>();
        for (const row of rows) {
          const sid = row.strategyId;
          if (!strategyGroups.has(sid)) {
            strategyGroups.set(sid, { symbols: [], avgScore: 0, scores: [] });
          }
          const g = strategyGroups.get(sid)!;
          g.symbols.push(row.symbol);
          // Apply pool boost to score
          const pb = poolBoost.get(row.symbol);
          const adjusted = (pb && maxPoolScore > 0)
            ? applyPoolBoost(row.compositeScore, pb.poolScore, maxPoolScore)
            : row.compositeScore;
          g.scores.push(adjusted);
        }
        // Compute avg score
        for (const g of strategyGroups.values()) {
          g.avgScore = g.scores.reduce((a, b) => a + b, 0) / g.scores.length;
        }

        // 2b. Sort strategy groups by avg score DESC, take top N only
        const sortedGroups = Array.from(strategyGroups.entries())
          .map(([sid, g]) => ({ sid, ...g }))
          .sort((a, b) => b.avgScore - a.avgScore)
          .slice(0, topN);

        // 3. If replaceExisting, remove old auto-assigned strategies
        const engine = getEngine();
        const removed: string[] = [];
        if (replaceExisting) {
          const existing = engine.strategyRegistry.list().filter(
            (s) => (s.parameters as Record<string, unknown> | undefined)?.source === "fitness-auto-assign",
          );
          for (const old of existing) {
            await engine.removeStrategy(old.id);
            removed.push(old.id);
          }
        }

        // 4. Create one live strategy per top-N strategy group
        const created: Array<{ id: string; strategyId: string; symbols: string[]; avgScore: number }> = [];
        const selectedStockCount = sortedGroups.reduce((acc, g) => acc + g.symbols.length, 0);
        for (const group of sortedGroups) {
          const strategyId = group.sid;
          const entry = await engine.addStrategy(strategyId, group.symbols, {
            source: "fitness-auto-assign",
            batch_id: usedBatchId,
            avg_composite_score: Math.round(group.avgScore * 100) / 100,
            stock_count: group.symbols.length,
          }, {
            capitalBudget: capitalBudgetPerStrategy && capitalBudgetPerStrategy > 0
              ? capitalBudgetPerStrategy : undefined,
          });
          created.push({
            id: entry.id,
            strategyId,
            symbols: group.symbols,
            avgScore: Math.round(group.avgScore * 100) / 100,
          });
        }

        const brokerType = engine.broker?.brokerType ?? "paper";
        json(res, {
          ok: true,
          batch_id: usedBatchId,
          top_n: topN,
          total_stocks: selectedStockCount,
          total_strategies: created.length,
          removed_count: removed.length,
          strategies: created.map((c) => ({
            id: c.id,
            strategyId: c.strategyId,
            stockCount: c.symbols.length,
            avgScore: c.avgScore,
            symbols: c.symbols.slice(0, 10), // preview first 10
          })),
          brokerType,
          message: `已按适配分析自动分配 Top ${topN} 策略（共 ${created.length} 个），覆盖 ${selectedStockCount} 只股票`,
        });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });

  // ── Fitness auto-assign preview (dry-run, no side effects) ──
  api.registerHttpRoute({
    auth: "plugin",
    path: "/quant/admin/api/strategy-fitness/auto-assign/preview",
    handler: secure(async (req, res) => {
      try {
        const url = new URL(req.url ?? "", `http://${req.headers.host}`);
        const batchId = url.searchParams.get("batch_id") || undefined;
        const topN = Math.max(1, Math.min(100, Number(url.searchParams.get("top_n")) || 10));

        // Try TS storage first
        let usedBatchId = batchId;
        let rows: StockStrategyFitnessRow[] = [];

        const storage = deps.dataWarehouse?.storage;
        if (storage && "getAllStocksTopStrategy" in storage) {
          const typed = storage as {
            getAllStocksTopStrategy(b?: string, l?: number, o?: number): Promise<{ rows: StockStrategyFitnessRow[]; total: number }>;
            getLatestFitnessBatchId(): Promise<string | null>;
          };
          if (!usedBatchId) usedBatchId = await typed.getLatestFitnessBatchId() ?? undefined;
          if (usedBatchId) {
            const result = await typed.getAllStocksTopStrategy(usedBatchId, 5000, 0);
            rows = result.rows;
          }
        }

        // Fallback: open SQLite directly (InMemoryStorage returns empty)
        if (!rows.length) {
          try {
            const dbPath = getDbPath();
            const fsSync = await import("node:fs");
            if (fsSync.existsSync(dbPath)) {
              const sqlite3Mod = await import("better-sqlite3");
              const Database = sqlite3Mod.default ?? sqlite3Mod;
              const db = new (Database as unknown as new (p: string) => Record<string, Function>)(dbPath);
              try {
                db.pragma("busy_timeout = 10000");
                if (!usedBatchId) {
                  const batchRow = db.prepare("SELECT batch_id FROM stock_strategy_fitness ORDER BY created_at DESC LIMIT 1").get() as { batch_id: string } | undefined;
                  usedBatchId = batchRow?.batch_id;
                }
                if (usedBatchId) {
                  const dbRows = db.prepare(
                    "SELECT * FROM stock_strategy_fitness WHERE batch_id = ? AND rank = 1 ORDER BY composite_score DESC LIMIT 5000",
                  ).all(usedBatchId) as Array<Record<string, unknown>>;
                  rows = dbRows.map((r) => ({
                    id: r.id as number,
                    symbol: r.symbol as string,
                    stockName: (r.stock_name as string) ?? "",
                    strategyId: r.strategy_id as string,
                    strategyName: (r.strategy_name as string) ?? "",
                    rank: r.rank as number,
                    compositeScore: r.composite_score as number,
                    totalReturn: (r.total_return as number) ?? 0,
                    annualizedReturn: (r.annualized_return as number) ?? 0,
                    sharpeRatio: (r.sharpe_ratio as number) ?? 0,
                    sortinoRatio: (r.sortino_ratio as number) ?? 0,
                    maxDrawdown: (r.max_drawdown as number) ?? 0,
                    winRate: (r.win_rate as number) ?? 0,
                    profitLossRatio: (r.profit_loss_ratio as number) ?? 0,
                    calmarRatio: (r.calmar_ratio as number) ?? 0,
                    totalTrades: (r.total_trades as number) ?? 0,
                    backtestStart: (r.backtest_start as string) ?? "",
                    backtestEnd: (r.backtest_end as string) ?? "",
                    batchId: r.batch_id as string,
                    createdAt: (r.created_at as string) ?? "",
                  }));
                }
              } finally {
                db.close();
              }
            }
          } catch { /* SQLite fallback failed, continue with empty */ }
        }

        if (!usedBatchId) {
          json(res, { ok: false, error: "无 fitness 分析数据" });
          return;
        }
        if (!rows.length) {
          json(res, { ok: false, error: "该批次无 fitness 数据" });
          return;
        }

        // Load pool backtest boost map
        const poolBoost = loadPoolBoostMap();
        const maxPoolScore = poolBoost.size > 0
          ? Math.max(...Array.from(poolBoost.values()).map((e) => e.poolScore))
          : 0;
        let boostedCount = 0;

        // Group by strategy (with pool boost applied)
        const groups = new Map<string, {
          symbols: Array<{ symbol: string; name?: string; score: number; originalScore: number; poolBoost?: { poolLabel: string; poolScore: number } }>;
          totalScore: number;
        }>();
        for (const row of rows) {
          const sid = row.strategyId;
          if (!groups.has(sid)) groups.set(sid, { symbols: [], totalScore: 0 });
          const g = groups.get(sid)!;
          const originalScore = row.compositeScore;
          const pb = poolBoost.get(row.symbol);
          let adjustedScore = originalScore;
          let boostInfo: { poolLabel: string; poolScore: number } | undefined;
          if (pb && maxPoolScore > 0) {
            adjustedScore = applyPoolBoost(originalScore, pb.poolScore, maxPoolScore);
            boostInfo = { poolLabel: pb.poolLabel, poolScore: pb.poolScore };
            boostedCount++;
          }
          g.symbols.push({
            symbol: row.symbol,
            name: row.stockName,
            score: Math.round(adjustedScore * 100) / 100,
            originalScore: Math.round(originalScore * 100) / 100,
            poolBoost: boostInfo,
          });
          g.totalScore += adjustedScore;
        }

        // Check existing auto-assigned
        const engine = getEngine();
        const existingAutoAssign = engine.strategyRegistry.list().filter(
          (s) => (s.parameters as Record<string, unknown> | undefined)?.source === "fitness-auto-assign",
        );

        const allStrategies = Array.from(groups.entries())
          .map(([sid, g]) => ({
            strategyId: sid,
            stockCount: g.symbols.length,
            avgScore: Math.round((g.totalScore / g.symbols.length) * 100) / 100,
            totalScore: Math.round(g.totalScore * 100) / 100,
            stocks: g.symbols.sort((a, b) => b.score - a.score),
          }))
          .sort((a, b) => b.totalScore - a.totalScore);

        // Mark which strategies are within top_n
        const strategies = allStrategies.map((s, idx) => ({
          ...s,
          selected: idx < topN,
        }));
        const selectedStrategies = strategies.filter((s) => s.selected);
        const selectedStockCount = selectedStrategies.reduce((acc, s) => acc + s.stockCount, 0);

        json(res, {
          ok: true,
          batch_id: usedBatchId,
          top_n: topN,
          total_stocks: rows.length,
          selected_stocks: selectedStockCount,
          total_strategies: allStrategies.length,
          selected_strategies: selectedStrategies.length,
          existing_auto_assign_count: existingAutoAssign.length,
          pool_boost_info: {
            enabled: poolBoost.size > 0,
            boosted_stocks: boostedCount,
            max_pool_score: maxPoolScore,
            pool_combos: poolBoost.size > 0
              ? [...new Set(Array.from(poolBoost.values()).map((e) => `${e.poolLabel}×${e.strategyType}`))].slice(0, 10)
              : [],
          },
          strategies,
        });
      } catch (err) {
        json(res, { ok: false, error: String(err) }, 500);
      }
    }),
  });
}
