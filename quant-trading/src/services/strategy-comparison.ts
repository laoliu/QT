/**
 * Strategy Comparison Service — compare multiple strategies on the same dataset
 * and rank by configurable metrics.
 *
 * Features:
 *   - Run multiple backtests in parallel (same symbols/dates)
 *   - Side-by-side metric comparison with best/worst/avg
 *   - Metric rankings (sort by any metric)
 *   - Comparison from existing backtest records (via BacktestStore)
 */

import { runBacktest } from "../engine/strategy-registry.js";
import type { BacktestRequest, BacktestResult, QuantTradingConfig } from "../types.js";
import type { BacktestStore, BacktestComparison } from "./backtest-store.js";

// ── Types ────────────────────────────────────────────────────

export type ComparisonMetric =
  | "totalReturn"
  | "annualizedReturn"
  | "maxDrawdown"
  | "sharpeRatio"
  | "sortinoRatio"
  | "calmarRatio"
  | "winRate"
  | "profitFactor"
  | "totalTrades";

export interface StrategyComparisonRequest {
  /** Strategy IDs to compare */
  strategyIds: string[];
  /** Shared backtest parameters */
  symbols: string[];
  startDate: string;
  endDate: string;
  initialCash?: number;
  /** Optional per-strategy parameter overrides */
  parameterOverrides?: Record<string, Record<string, unknown>>;
}

export interface StrategyComparisonEntry {
  strategyId: string;
  result: BacktestResult;
  metrics: Record<ComparisonMetric, number>;
  /** Rank for each metric (1 = best) */
  ranks: Record<ComparisonMetric, number>;
}

export interface StrategyComparisonResult {
  entries: StrategyComparisonEntry[];
  /** Per-metric aggregation */
  summary: Record<ComparisonMetric, MetricSummary>;
  /** Ranking by composite score (avg of ranks) */
  overallRanking: Array<{ strategyId: string; compositeScore: number; rank: number }>;
}

export interface MetricSummary {
  best: { strategyId: string; value: number };
  worst: { strategyId: string; value: number };
  avg: number;
  values: Array<{ strategyId: string; value: number }>;
}

// ── Metric extraction ────────────────────────────────────────

const METRICS: ComparisonMetric[] = [
  "totalReturn",
  "annualizedReturn",
  "maxDrawdown",
  "sharpeRatio",
  "sortinoRatio",
  "calmarRatio",
  "winRate",
  "profitFactor",
  "totalTrades",
];

/** Whether higher values are better for a metric */
const HIGHER_IS_BETTER: Record<ComparisonMetric, boolean> = {
  totalReturn: true,
  annualizedReturn: true,
  maxDrawdown: false, // lower drawdown is better
  sharpeRatio: true,
  sortinoRatio: true,
  calmarRatio: true,
  winRate: true,
  profitFactor: true,
  totalTrades: true, // more trades = more opportunities (preference)
};

export function extractMetrics(result: BacktestResult): Record<ComparisonMetric, number> {
  return {
    totalReturn: result.totalReturn,
    annualizedReturn: result.annualizedReturn,
    maxDrawdown: result.maxDrawdown,
    sharpeRatio: result.sharpeRatio,
    sortinoRatio: result.sortinoRatio ?? 0,
    calmarRatio: result.calmarRatio ?? 0,
    winRate: result.winRate,
    profitFactor: result.profitFactor ?? 0,
    totalTrades: result.totalTrades,
  };
}

/**
 * Compute per-metric ranks (1 = best). Ties share the same rank.
 */
export function computeRanks(
  entries: Array<{ strategyId: string; metrics: Record<ComparisonMetric, number> }>,
  metricList: ComparisonMetric[] = METRICS,
): Map<string, Record<ComparisonMetric, number>> {
  const ranks = new Map<string, Record<ComparisonMetric, number>>();
  for (const entry of entries) {
    ranks.set(entry.strategyId, {} as Record<ComparisonMetric, number>);
  }

  for (const metric of metricList) {
    const higherBetter = HIGHER_IS_BETTER[metric];
    const sorted = [...entries].sort((a, b) => {
      const diff = a.metrics[metric] - b.metrics[metric];
      return higherBetter ? -diff : diff;
    });
    let currentRank = 1;
    for (let i = 0; i < sorted.length; i++) {
      if (i > 0 && sorted[i]!.metrics[metric] !== sorted[i - 1]!.metrics[metric]) {
        currentRank = i + 1;
      }
      ranks.get(sorted[i]!.strategyId)![metric] = currentRank;
    }
  }

  return ranks;
}

/**
 * Compute metric summary (best/worst/avg) for one metric.
 */
export function computeMetricSummary(
  entries: Array<{ strategyId: string; value: number }>,
  higherIsBetter: boolean,
): MetricSummary {
  if (entries.length === 0) {
    return {
      best: { strategyId: "", value: 0 },
      worst: { strategyId: "", value: 0 },
      avg: 0,
      values: [],
    };
  }
  const sorted = [...entries].sort((a, b) =>
    higherIsBetter ? b.value - a.value : a.value - b.value,
  );
  const sum = entries.reduce((acc, e) => acc + e.value, 0);
  return {
    best: sorted[0]!,
    worst: sorted[sorted.length - 1]!,
    avg: sum / entries.length,
    values: entries,
  };
}

// ── Strategy Comparison Service ──────────────────────────────

export class StrategyComparisonService {
  constructor(
    private config: QuantTradingConfig,
    private backtestStore?: BacktestStore,
  ) {}

  /**
   * Run backtests for multiple strategies on the same data and compare.
   */
  async compare(req: StrategyComparisonRequest): Promise<StrategyComparisonResult> {
    // Run all backtests
    const results: Array<{ strategyId: string; result: BacktestResult }> = [];

    for (const strategyId of req.strategyIds) {
      const backtestReq: BacktestRequest = {
        strategyId,
        symbols: req.symbols,
        startDate: req.startDate,
        endDate: req.endDate,
        initialCash: req.initialCash ?? 100_000,
        parameters: req.parameterOverrides?.[strategyId],
      };
      const result = await runBacktest(backtestReq, this.config);
      results.push({ strategyId, result });
    }

    return this.buildComparison(results);
  }

  /**
   * Compare existing backtest records from the store.
   */
  compareExisting(ids: string[]): BacktestComparison | null {
    if (!this.backtestStore) return null;
    return this.backtestStore.compare(ids);
  }

  /**
   * Build a comparison result from pre-computed backtest results.
   */
  buildComparison(
    results: Array<{ strategyId: string; result: BacktestResult }>,
  ): StrategyComparisonResult {
    // Extract metrics
    const entriesWithMetrics = results.map(({ strategyId, result }) => ({
      strategyId,
      result,
      metrics: extractMetrics(result),
    }));

    // Compute ranks
    const ranksMap = computeRanks(entriesWithMetrics);

    // Build entries with ranks
    const entries: StrategyComparisonEntry[] = entriesWithMetrics.map((e) => ({
      strategyId: e.strategyId,
      result: e.result,
      metrics: e.metrics,
      ranks: ranksMap.get(e.strategyId)!,
    }));

    // Build per-metric summaries
    const summary = {} as Record<ComparisonMetric, MetricSummary>;
    for (const metric of METRICS) {
      const values = entriesWithMetrics.map((e) => ({
        strategyId: e.strategyId,
        value: e.metrics[metric],
      }));
      summary[metric] = computeMetricSummary(values, HIGHER_IS_BETTER[metric]);
    }

    // Compute overall ranking by average rank
    const overallRanking = entries
      .map((e) => {
        const rankValues = Object.values(e.ranks);
        const compositeScore = rankValues.reduce((a, b) => a + b, 0) / rankValues.length;
        return { strategyId: e.strategyId, compositeScore };
      })
      .sort((a, b) => a.compositeScore - b.compositeScore)
      .map((e, i) => ({ ...e, rank: i + 1 }));

    return { entries, summary, overallRanking };
  }
}
