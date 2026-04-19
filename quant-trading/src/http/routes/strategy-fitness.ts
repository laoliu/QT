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
import { json, parseObjectBody } from "./_helpers.js";
import type { BacktestResult, QuantTradingConfig } from "../../types.js";
import type { QuantStorage, StockStrategyFitnessRow, GlobalStrategyRankingRow } from "../../services/sqlite-storage.js";
import * as fs from "node:fs/promises";
import * as path from "node:path";
import * as os from "node:os";
import { Worker } from "node:worker_threads";
import { fileURLToPath, pathToFileURL } from "node:url";

import { getPythonDir } from "../../bridge/python-dir.js";

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
): Promise<FitnessTaskResult[]> {
  const provider = new CachingProvider(createDataProvider(config));
  const results: FitnessTaskResult[] = [];
  const total = builtinStrategies.length * symbols.length;
  let completed = 0;

  // Phase 1: Prefetch all klines in parallel batches (each symbol fetched only once)
  // Use a per-symbol timeout to avoid hanging on slow/failed remote fetches
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
  console.log(`[Builtin Fitness] 预加载 ${symbols.length} 只股票K线数据...`);
  const prefetchBatchSize = 16;
  let prefetched = 0;
  for (let i = 0; i < symbols.length; i += prefetchBatchSize) {
    if (signal?.aborted) break;
    const batch = symbols.slice(i, i + prefetchBatchSize);
    await Promise.all(batch.map((s) => timedGetKlines(s)));
    prefetched += batch.length;
    if (prefetched % 50 === 0 || prefetched === symbols.length) {
      console.log(`[Builtin Fitness] 已预加载 ${prefetched}/${symbols.length} 只股票K线`);
    }
  }
  console.log(`[Builtin Fitness] K线预加载完成, 开始回测 ${builtinStrategies.length} 个策略`);

  // Materialize cached bars into a map for workers
  const allBars = new Map<string, import("../../types.js").KlineBar[]>();
  for (const sym of symbols) {
    const bars = await provider.getKlines(sym, "1d", startDate, endDate, "cn_stock").catch(() => []);
    if (bars.length > 0) allBars.set(sym, bars);
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
                    totalReturn: r.totalReturn ?? 0,
                    annualizedReturn: r.annualizedReturn ?? 0,
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
            }, config, provider);
            if (result.totalTrades === 0) return null;
            const cs = compositeScore({
              totalReturn: result.totalReturn ?? 0, annualizedReturn: result.annualizedReturn ?? 0,
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
          batchId = (pyData.batch_id as string) ?? "";
          alphaStrategyCount = (pyData.total_strategies as number) ?? 0;
          totalElapsed = (pyData.elapsed_seconds as number) ?? 0;

          if (pyData._phase === "alpha_only" || pyData._phase === "metadata_only") {
            // Python returned intermediate result with universe for TS to continue
            validSymbols = (pyData.valid_symbols as string[]) ?? [];
            startDate = (pyData.start_date as string) ?? "";
            endDate = (pyData.end_date as string) ?? "";
            alphaResults = (pyData.alpha_results as Array<Record<string, unknown>>) ?? [];
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
          json(res, { ok: false, error: "无有效股票，无法运行Builtin策略" });
        }
      } catch (err) {
        json(
          res,
          { ok: false, error: `适配分析失败: ${err instanceof Error ? err.message : String(err)}` },
          500,
        );
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
}
