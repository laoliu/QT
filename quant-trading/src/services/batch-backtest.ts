import { createDataProvider } from "../data/market-data-provider.js";
import { runBacktest } from "../engine/strategy-registry.js";
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
 * Higher = better. Matches the daily-picks scoring pattern.
 */
function computeCompositeScore(r: BatchBacktestResultItem): number {
  const totalReturn = r.totalReturn ?? 0;
  const annualizedReturn = r.annualizedReturn ?? 0;
  const sharpe = r.sharpeRatio ?? 0;
  const winRate = r.winRate ?? 0;
  const profitFactor = Math.min(r.profitFactor ?? 0, 4);
  const maxDrawdown = r.maxDrawdown ?? 0;
  const trades = Math.min(r.totalTrades ?? 0, 120);

  return round2(
    totalReturn * 1.0 +
      annualizedReturn * 0.35 +
      sharpe * 12 +
      winRate * 0.28 +
      profitFactor * 4 -
      maxDrawdown * 0.85 +
      trades * 0.03,
  );
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
