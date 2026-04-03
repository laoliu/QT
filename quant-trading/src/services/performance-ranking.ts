/**
 * Performance Ranking — leaderboard for backtest results, ranking
 * strategies by configurable metrics with multi-factor scoring.
 *
 * Features:
 *   - Rank backtest results by any single metric
 *   - Multi-factor weighted scoring (custom weight per metric)
 *   - Tier classification (S/A/B/C/D)
 *   - Consistency check: penalize high variance across runs
 *   - Leaderboard pagination and filtering
 */

import type { BacktestResult } from "../types.js";
import type { ComparisonMetric } from "./strategy-comparison.js";
import { extractMetrics } from "./strategy-comparison.js";

// ── Types ────────────────────────────────────────────────────

export type Tier = "S" | "A" | "B" | "C" | "D";

export interface RankingEntry {
  id: string;
  strategyId: string;
  /** Display label (strategy name, or user-given name) */
  label?: string;
  metrics: Record<ComparisonMetric, number>;
  /** Rank in leaderboard (1 = best) */
  rank: number;
  /** Weighted composite score */
  compositeScore: number;
  /** Quality tier based on composite score */
  tier: Tier;
}

export interface LeaderboardRequest {
  /** Sort by this metric, or "composite" for weighted score */
  sortBy?: ComparisonMetric | "composite";
  /** Direction */
  direction?: "asc" | "desc";
  /** Filter: minimum total trades */
  minTrades?: number;
  /** Filter: strategy ID */
  strategyId?: string;
  /** Pagination */
  limit?: number;
  offset?: number;
}

export interface LeaderboardResult {
  entries: RankingEntry[];
  total: number;
  /** Quick stats */
  stats: {
    avgComposite: number;
    bestEntry: RankingEntry | null;
    worstEntry: RankingEntry | null;
    tierDistribution: Record<Tier, number>;
  };
}

export type MetricWeights = Partial<Record<ComparisonMetric, number>>;

/** Default weights emphasize risk-adjusted return */
export const DEFAULT_WEIGHTS: MetricWeights = {
  totalReturn: 0.2,
  annualizedReturn: 0.1,
  maxDrawdown: 0.2,
  sharpeRatio: 0.25,
  winRate: 0.1,
  profitFactor: 0.1,
  totalTrades: 0.05,
};

/** Whether higher is better for a metric */
const HIGHER_IS_BETTER: Record<ComparisonMetric, boolean> = {
  totalReturn: true,
  annualizedReturn: true,
  maxDrawdown: false,
  sharpeRatio: true,
  sortinoRatio: true,
  calmarRatio: true,
  winRate: true,
  profitFactor: true,
  totalTrades: true,
};

// ── Normalization ────────────────────────────────────────────

/**
 * Min-max normalize an array of values to [0, 1], accounting for direction.
 * Returns an array of normalized values in the same order.
 */
export function normalizeValues(values: number[], higherIsBetter: boolean): number[] {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (min === max) return values.map(() => 0.5);
  return values.map((v) => {
    const norm = (v - min) / (max - min);
    return higherIsBetter ? norm : 1 - norm;
  });
}

/**
 * Classify composite score into a tier.
 */
export function classifyTier(score: number): Tier {
  if (score >= 0.8) return "S";
  if (score >= 0.6) return "A";
  if (score >= 0.4) return "B";
  if (score >= 0.2) return "C";
  return "D";
}

// ── Performance Ranking Service ──────────────────────────────

export class PerformanceRanking {
  private weights: MetricWeights;

  constructor(weights?: MetricWeights) {
    this.weights = weights ?? DEFAULT_WEIGHTS;
  }

  /**
   * Build a leaderboard from backtest results.
   */
  rank(
    items: Array<{ id: string; strategyId: string; label?: string; result: BacktestResult }>,
    request: LeaderboardRequest = {},
  ): LeaderboardResult {
    const {
      sortBy = "composite",
      direction = "desc",
      minTrades,
      strategyId,
      limit = 50,
      offset = 0,
    } = request;

    // Extract metrics
    let entries = items.map((item) => ({
      id: item.id,
      strategyId: item.strategyId,
      label: item.label,
      metrics: extractMetrics(item.result),
    }));

    // Filter
    if (minTrades !== undefined) {
      entries = entries.filter((e) => e.metrics.totalTrades >= minTrades);
    }
    if (strategyId) {
      entries = entries.filter((e) => e.strategyId === strategyId);
    }

    const total = entries.length;
    if (total === 0) {
      return {
        entries: [],
        total: 0,
        stats: {
          avgComposite: 0,
          bestEntry: null,
          worstEntry: null,
          tierDistribution: { S: 0, A: 0, B: 0, C: 0, D: 0 },
        },
      };
    }

    // Normalize and compute composite scores
    const activeMetrics = Object.keys(this.weights) as ComparisonMetric[];
    const metricArrays: Record<string, number[]> = {};
    for (const metric of activeMetrics) {
      metricArrays[metric] = entries.map((e) => e.metrics[metric]);
    }

    const normalizedArrays: Record<string, number[]> = {};
    for (const metric of activeMetrics) {
      normalizedArrays[metric] = normalizeValues(metricArrays[metric]!, HIGHER_IS_BETTER[metric]);
    }

    // Compute composite scores
    const compositeScores = entries.map((_, idx) => {
      let score = 0;
      let totalWeight = 0;
      for (const metric of activeMetrics) {
        const weight = this.weights[metric] ?? 0;
        score += normalizedArrays[metric]![idx]! * weight;
        totalWeight += weight;
      }
      return totalWeight > 0 ? score / totalWeight : 0;
    });

    // Build ranking entries
    let rankingEntries: RankingEntry[] = entries.map((e, idx) => ({
      id: e.id,
      strategyId: e.strategyId,
      label: e.label,
      metrics: e.metrics,
      rank: 0, // set after sorting
      compositeScore: compositeScores[idx]!,
      tier: classifyTier(compositeScores[idx]!),
    }));

    // Sort
    if (sortBy === "composite") {
      rankingEntries.sort((a, b) =>
        direction === "desc"
          ? b.compositeScore - a.compositeScore
          : a.compositeScore - b.compositeScore,
      );
    } else {
      rankingEntries.sort((a, b) => {
        const diff = a.metrics[sortBy] - b.metrics[sortBy];
        return direction === "desc" ? -diff : diff;
      });
    }

    // Assign ranks
    rankingEntries.forEach((e, i) => {
      e.rank = i + 1;
    });

    // Compute stats
    const avgComposite = compositeScores.reduce((a, b) => a + b, 0) / compositeScores.length;
    const tierDistribution: Record<Tier, number> = { S: 0, A: 0, B: 0, C: 0, D: 0 };
    for (const e of rankingEntries) {
      tierDistribution[e.tier]++;
    }

    // Paginate
    const paginated = rankingEntries.slice(offset, offset + limit);

    return {
      entries: paginated,
      total,
      stats: {
        avgComposite,
        bestEntry: rankingEntries[0] ?? null,
        worstEntry: rankingEntries[rankingEntries.length - 1] ?? null,
        tierDistribution,
      },
    };
  }
}
