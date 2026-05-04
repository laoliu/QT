import { createDataProvider } from "../data/market-data-provider.js";
import { runBacktest } from "../engine/strategy-registry.js";
import * as fs from "node:fs";
import * as path from "node:path";
/**
 * Batch Backtest Service — run N strategy backtests in parallel with
 * concurrency control and aggregate results into a comparison table.
 */
import type { BacktestRequest, BacktestResult, QuantTradingConfig } from "../types.js";

export interface BatchBacktestItem {
  /** Label for this run (strategy ID, composition name, etc.). */
  label: string;
  /** Backtest request. */
  request: BacktestRequest;
}

export interface BatchBacktestResultItem {
  label: string;
  strategyId: string;
  ok: boolean;
  error?: string;
  totalReturn?: number;
  annualizedReturn?: number;
  maxDrawdown?: number;
  sharpeRatio?: number;
  winRate?: number;
  profitFactor?: number;
  totalTrades?: number;
  finalValue?: number;
  initialCash?: number;
}

export interface BatchBacktestSummary {
  totalRuns: number;
  successCount: number;
  failCount: number;
  results: BatchBacktestResultItem[];
  /** Results sorted by composite score (descending). */
  ranked: BatchBacktestResultItem[];
  bestStrategy: string | null;
  bestCompositeScore: number;
}

const DEFAULT_CONCURRENCY = 4;

/**
 * Run multiple backtests in parallel (bounded concurrency).
 */
export async function runBatchBacktest(
  items: BatchBacktestItem[],
  config: QuantTradingConfig,
  options?: { maxConcurrency?: number },
): Promise<BatchBacktestSummary> {
  const maxConcurrency = options?.maxConcurrency ?? DEFAULT_CONCURRENCY;
  const results: BatchBacktestResultItem[] = [];

  // Share one data provider across all strategy runs so kline cache is reused
  const sharedProvider = createDataProvider(config);

  // Bounded concurrency via a simple semaphore
  let running = 0;
  let idx = 0;
  const queue: Array<() => void> = [];

  function release() {
    running--;
    if (queue.length > 0) {
      const next = queue.shift()!;
      next();
    }
  }

  function acquire(): Promise<void> {
    if (running < maxConcurrency) {
      running++;
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      queue.push(() => {
        running++;
        resolve();
      });
    });
  }

  const promises = items.map(async (item) => {
    await acquire();
    try {
      const result = await runBacktest(item.request, config, sharedProvider);
      results.push({
        label: item.label,
        strategyId: item.request.strategyId,
        ok: true,
        totalReturn: result.totalReturn,
        annualizedReturn: result.annualizedReturn,
        maxDrawdown: result.maxDrawdown,
        sharpeRatio: result.sharpeRatio,
        winRate: result.winRate,
        profitFactor: result.profitFactor,
        totalTrades: result.totalTrades,
        finalValue: result.finalValue,
        initialCash: result.initialCash,
      });
    } catch (err) {
      results.push({
        label: item.label,
        strategyId: item.request.strategyId,
        ok: false,
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      release();
    }
  });

  await Promise.all(promises);

  // Composite scoring & ranking
  const scored = results
    .filter((r) => r.ok)
    .map((r) => ({
      ...r,
      compositeScore: computeCompositeScore(r),
    }))
    .sort((a, b) => b.compositeScore - a.compositeScore);

  const successCount = results.filter((r) => r.ok).length;

  return {
    totalRuns: items.length,
    successCount,
    failCount: items.length - successCount,
    results,
    ranked: scored,
    bestStrategy: scored.length > 0 ? scored[0]!.label : null,
    bestCompositeScore: scored.length > 0 ? scored[0]!.compositeScore : 0,
  };
}

/**
 * Composite score: weighted blend of key backtest metrics.
 * Higher = better.
 *
 * Formula: baseScore × activityFactor
 *   baseScore = 年化收益×0.5 + 夏普比率×10 + 胜率×0.3 + min(盈亏比,4)×4 − 最大回撤×1.0
 *   activityFactor = clamp(trades / 15, 0.05, 1.0)
 *
 * The activity factor ensures strategies with few/no trades are penalized:
 *   0 trades → 0.05 (nearly zeroed), 5 → 0.33, 10 → 0.67, 15+ → 1.0
 */
function computeCompositeScore(r: BatchBacktestResultItem): number {
  const annualizedReturn = r.annualizedReturn ?? 0;
  const sharpe = r.sharpeRatio ?? 0;
  const winRate = r.winRate ?? 0;
  const profitFactor = Math.min(r.profitFactor ?? 0, 4);
  const maxDrawdown = r.maxDrawdown ?? 0;
  const trades = r.totalTrades ?? 0;

  const baseScore =
    annualizedReturn * 0.5 +
    sharpe * 10 +
    winRate * 0.3 +
    profitFactor * 4 -
    maxDrawdown * 1.0;

  const activityFactor = Math.min(1.0, Math.max(0.05, trades / 15));

  return round2(baseScore * activityFactor);
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

// ── Persistence ────────────────────────────────────────────────────

export interface BatchBacktestRecord {
  id: string;
  timestamp: string;
  symbols: string[];
  startDate: string;
  endDate: string;
  totalRuns: number;
  successCount: number;
  bestStrategy: string | null;
  bestCompositeScore: number;
  ranked: BatchBacktestResultItem[];
}

function getBatchHistoryDir(config: QuantTradingConfig): string {
  const dataDir = config.dataDir ?? path.join(
    process.env["HOME"] ?? process.env["USERPROFILE"] ?? ".",
    ".openclaw",
    "quant-data",
  );
  const dir = path.join(dataDir, "batch-backtest-history");
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  return dir;
}

/**
 * Save batch backtest results to disk.
 */
export function saveBatchBacktestResult(
  summary: BatchBacktestSummary,
  meta: { symbols: string[]; startDate: string; endDate: string },
  config: QuantTradingConfig,
): BatchBacktestRecord {
  const dir = getBatchHistoryDir(config);
  const id = `batch-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 5)}`;
  const record: BatchBacktestRecord = {
    id,
    timestamp: new Date().toISOString(),
    symbols: meta.symbols,
    startDate: meta.startDate,
    endDate: meta.endDate,
    totalRuns: summary.totalRuns,
    successCount: summary.successCount,
    bestStrategy: summary.bestStrategy,
    bestCompositeScore: summary.bestCompositeScore,
    ranked: summary.ranked,
  };
  const filePath = path.join(dir, `${id}.json`);
  fs.writeFileSync(filePath, JSON.stringify(record, null, 2), "utf-8");
  return record;
}

/**
 * List all saved batch backtest history (most recent first).
 */
export function listBatchBacktestHistory(config: QuantTradingConfig, limit = 50): BatchBacktestRecord[] {
  const dir = getBatchHistoryDir(config);
  if (!fs.existsSync(dir)) return [];
  const files = fs.readdirSync(dir)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .reverse()
    .slice(0, limit);
  const records: BatchBacktestRecord[] = [];
  for (const f of files) {
    try {
      const raw = fs.readFileSync(path.join(dir, f), "utf-8");
      records.push(JSON.parse(raw) as BatchBacktestRecord);
    } catch { /* skip corrupted */ }
  }
  return records;
}

/**
 * Get a single batch backtest record by ID.
 */
export function getBatchBacktestById(id: string, config: QuantTradingConfig): BatchBacktestRecord | null {
  const dir = getBatchHistoryDir(config);
  const filePath = path.join(dir, `${id}.json`);
  if (!fs.existsSync(filePath)) return null;
  try {
    const raw = fs.readFileSync(filePath, "utf-8");
    return JSON.parse(raw) as BatchBacktestRecord;
  } catch { return null; }
}

/**
 * Delete a batch backtest record by ID.
 */
export function deleteBatchBacktest(id: string, config: QuantTradingConfig): boolean {
  const dir = getBatchHistoryDir(config);
  const filePath = path.join(dir, `${id}.json`);
  if (!fs.existsSync(filePath)) return false;
  fs.unlinkSync(filePath);
  return true;
}
