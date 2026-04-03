/**
 * Parameter Optimization — grid search and walk-forward optimization
 * to find optimal strategy parameters.
 *
 * Features:
 *   - Grid search: exhaustive parameter space exploration
 *   - Walk-forward: in-sample optimization with out-of-sample validation
 *   - Configurable target metric (return, Sharpe, etc.)
 *   - Parallelism control for large search spaces
 */

import { getStrategy } from "../engine/strategy-registry.js";
import { runBacktest } from "../engine/strategy-registry.js";
import type {
  BacktestRequest,
  BacktestResult,
  QuantTradingConfig,
  StrategyDefinition,
  StrategyParameter,
} from "../types.js";
import type { ComparisonMetric } from "./strategy-comparison.js";

// ── Types ────────────────────────────────────────────────────

export interface OptimizationRequest {
  /** Strategy ID to optimize */
  strategyId: string;
  /** Parameters to optimize (others stay at default) */
  targetParams: string[];
  /** Backtest config */
  symbols: string[];
  startDate: string;
  endDate: string;
  initialCash?: number;
  /** Metric to optimize for (default: sharpeRatio) */
  targetMetric?: ComparisonMetric;
  /** Max number of parameter combinations to evaluate (default: 500) */
  maxCombinations?: number;
}

export interface ParameterRange {
  name: string;
  type: "number" | "string" | "boolean";
  values: unknown[];
}

export interface OptimizationRun {
  params: Record<string, unknown>;
  result: BacktestResult;
  targetMetricValue: number;
}

export interface OptimizationResult {
  strategyId: string;
  targetMetric: ComparisonMetric;
  /** Best parameter set */
  bestParams: Record<string, unknown>;
  bestMetricValue: number;
  /** Full result for best params */
  bestResult: BacktestResult;
  /** All runs sorted by target metric (best first) */
  allRuns: OptimizationRun[];
  /** Total combinations evaluated */
  totalEvaluated: number;
  /** Total combinations possible (before maxCombinations cap) */
  totalPossible: number;
  /** Stats */
  avgMetricValue: number;
  worstMetricValue: number;
}

export interface WalkForwardPeriod {
  inSampleStart: string;
  inSampleEnd: string;
  outOfSampleStart: string;
  outOfSampleEnd: string;
}

export interface WalkForwardResult {
  strategyId: string;
  targetMetric: ComparisonMetric;
  /** Per-window results */
  windows: Array<{
    period: WalkForwardPeriod;
    bestInSampleParams: Record<string, unknown>;
    inSampleMetric: number;
    outOfSampleMetric: number;
    outOfSampleResult: BacktestResult;
  }>;
  /** Avg out-of-sample metric */
  avgOutOfSampleMetric: number;
  /** Consistency ratio: % of windows where OOS > 0 */
  consistencyRatio: number;
}

// ── Parameter space generation ───────────────────────────────

/**
 * Generate parameter ranges from a strategy definition.
 * Uses min/max/step for numeric params, enumerates boolean.
 */
export function generateParameterRanges(
  strategyDef: StrategyDefinition,
  targetParams: string[],
): ParameterRange[] {
  const ranges: ParameterRange[] = [];
  for (const paramName of targetParams) {
    const param = strategyDef.parameters.find((p) => p.name === paramName);
    if (!param) continue;
    ranges.push(generateSingleRange(param));
  }
  return ranges;
}

export function generateSingleRange(param: StrategyParameter): ParameterRange {
  if (param.type === "boolean") {
    return { name: param.name, type: "boolean", values: [true, false] };
  }
  if (param.type === "number") {
    const min = param.min ?? (typeof param.default === "number" ? param.default * 0.5 : 1);
    const max = param.max ?? (typeof param.default === "number" ? param.default * 2 : 100);
    const rawStep = (max - min) / 10;
    // For integer-like ranges (both bounds are integers), use at least step=1
    // For fractional ranges, preserve the fractional step
    const isIntegerRange = Number.isInteger(min) && Number.isInteger(max) && rawStep >= 1;
    const step = param.step ?? (isIntegerRange ? Math.round(rawStep) : rawStep);
    const values: number[] = [];
    for (let v = min; v <= max; v += step) {
      values.push(Math.round(v * 1000) / 1000); // round to 3 dp
    }
    // Ensure max is included if not already
    if (values.length > 0 && values[values.length - 1]! < max) {
      values.push(Math.round(max * 1000) / 1000);
    }
    return { name: param.name, type: "number", values };
  }
  // String — only default value
  return { name: param.name, type: "string", values: [param.default] };
}

/**
 * Generate the cartesian product of parameter ranges.
 */
export function cartesianProduct(ranges: ParameterRange[]): Array<Record<string, unknown>> {
  if (ranges.length === 0) return [{}];
  const result: Array<Record<string, unknown>> = [];

  function recurse(idx: number, current: Record<string, unknown>) {
    if (idx === ranges.length) {
      result.push({ ...current });
      return;
    }
    const range = ranges[idx]!;
    for (const value of range.values) {
      current[range.name] = value;
      recurse(idx + 1, current);
    }
  }

  recurse(0, {});
  return result;
}

// ── Metric access ────────────────────────────────────────────

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

export function getMetricValue(result: BacktestResult, metric: ComparisonMetric): number {
  switch (metric) {
    case "totalReturn":
      return result.totalReturn;
    case "annualizedReturn":
      return result.annualizedReturn;
    case "maxDrawdown":
      return result.maxDrawdown;
    case "sharpeRatio":
      return result.sharpeRatio;
    case "sortinoRatio":
      return result.sortinoRatio ?? 0;
    case "calmarRatio":
      return result.calmarRatio ?? 0;
    case "winRate":
      return result.winRate;
    case "profitFactor":
      return result.profitFactor ?? 0;
    case "totalTrades":
      return result.totalTrades;
  }
}

function isBetter(a: number, b: number, metric: ComparisonMetric): boolean {
  return HIGHER_IS_BETTER[metric] ? a > b : a < b;
}

// ── Optimization Service ─────────────────────────────────────

export class ParameterOptimizer {
  constructor(private config: QuantTradingConfig) {}

  /**
   * Grid search: try all parameter combinations (capped by maxCombinations)
   * and return results sorted by target metric.
   */
  async gridSearch(req: OptimizationRequest): Promise<OptimizationResult> {
    const stratDef = getStrategy(req.strategyId);
    if (!stratDef) {
      throw new Error(`Unknown strategy: ${req.strategyId}`);
    }

    const targetMetric = req.targetMetric ?? "sharpeRatio";
    const maxCombinations = req.maxCombinations ?? 500;

    // Generate parameter space
    const ranges = generateParameterRanges(stratDef, req.targetParams);
    let combos = cartesianProduct(ranges);
    const totalPossible = combos.length;

    // Cap at maxCombinations (sample evenly)
    if (combos.length > maxCombinations) {
      const step = Math.ceil(combos.length / maxCombinations);
      combos = combos.filter((_, i) => i % step === 0).slice(0, maxCombinations);
    }

    // Add defaults for non-targeted parameters
    const defaults: Record<string, unknown> = {};
    for (const p of stratDef.parameters) {
      if (!req.targetParams.includes(p.name)) {
        defaults[p.name] = p.default;
      }
    }

    // Run backtests
    const runs: OptimizationRun[] = [];
    for (const paramSet of combos) {
      const params = { ...defaults, ...paramSet };
      const backtestReq: BacktestRequest = {
        strategyId: req.strategyId,
        symbols: req.symbols,
        startDate: req.startDate,
        endDate: req.endDate,
        initialCash: req.initialCash ?? 100_000,
        parameters: params,
      };
      const result = await runBacktest(backtestReq, this.config);
      const metricValue = getMetricValue(result, targetMetric);
      runs.push({ params, result, targetMetricValue: metricValue });
    }

    // Sort by target metric (best first)
    runs.sort((a, b) =>
      isBetter(a.targetMetricValue, b.targetMetricValue, targetMetric)
        ? -1
        : a.targetMetricValue === b.targetMetricValue
          ? 0
          : 1,
    );

    const best = runs[0]!;
    const avg = runs.reduce((s, r) => s + r.targetMetricValue, 0) / runs.length;
    const worst = runs[runs.length - 1]!;

    return {
      strategyId: req.strategyId,
      targetMetric,
      bestParams: best.params,
      bestMetricValue: best.targetMetricValue,
      bestResult: best.result,
      allRuns: runs,
      totalEvaluated: runs.length,
      totalPossible,
      avgMetricValue: avg,
      worstMetricValue: worst.targetMetricValue,
    };
  }

  /**
   * Walk-forward optimization: split data into rolling windows,
   * optimize in-sample, validate out-of-sample.
   */
  async walkForward(
    req: OptimizationRequest,
    opts: { windowCount?: number; inSampleRatio?: number } = {},
  ): Promise<WalkForwardResult> {
    const windowCount = opts.windowCount ?? 3;
    const inSampleRatio = opts.inSampleRatio ?? 0.7;
    const targetMetric = req.targetMetric ?? "sharpeRatio";

    // Parse dates
    const start = new Date(req.startDate);
    const end = new Date(req.endDate);
    const totalDays = (end.getTime() - start.getTime()) / (1000 * 60 * 60 * 24);
    const windowDays = Math.floor(totalDays / windowCount);
    const inSampleDays = Math.floor(windowDays * inSampleRatio);

    // Generate windows
    const periods: WalkForwardPeriod[] = [];
    for (let i = 0; i < windowCount; i++) {
      const windowStart = new Date(start.getTime() + i * windowDays * 86400_000);
      const isEnd = new Date(windowStart.getTime() + inSampleDays * 86400_000);
      const oosStart = new Date(isEnd.getTime() + 86400_000);
      const oosEnd = new Date(windowStart.getTime() + windowDays * 86400_000);

      if (oosEnd > end) continue;

      periods.push({
        inSampleStart: windowStart.toISOString().slice(0, 10),
        inSampleEnd: isEnd.toISOString().slice(0, 10),
        outOfSampleStart: oosStart.toISOString().slice(0, 10),
        outOfSampleEnd: oosEnd.toISOString().slice(0, 10),
      });
    }

    const windows: WalkForwardResult["windows"] = [];

    for (const period of periods) {
      // Optimize in-sample
      const isResult = await this.gridSearch({
        ...req,
        startDate: period.inSampleStart,
        endDate: period.inSampleEnd,
        maxCombinations: req.maxCombinations ?? 100,
      });

      // Validate out-of-sample
      const oosReq: BacktestRequest = {
        strategyId: req.strategyId,
        symbols: req.symbols,
        startDate: period.outOfSampleStart,
        endDate: period.outOfSampleEnd,
        initialCash: req.initialCash ?? 100_000,
        parameters: isResult.bestParams,
      };
      const oosResult = await runBacktest(oosReq, this.config);
      const oosMetric = getMetricValue(oosResult, targetMetric);

      windows.push({
        period,
        bestInSampleParams: isResult.bestParams,
        inSampleMetric: isResult.bestMetricValue,
        outOfSampleMetric: oosMetric,
        outOfSampleResult: oosResult,
      });
    }

    const oosMetrics = windows.map((w) => w.outOfSampleMetric);
    const avgOos =
      oosMetrics.length > 0 ? oosMetrics.reduce((a, b) => a + b, 0) / oosMetrics.length : 0;
    // Consistency: count OOS windows that beat the baseline direction
    // For "higher is better" metrics: positive = good
    // For "lower is better" metrics (maxDrawdown): being lower (< IS) = good
    const higherBetter = HIGHER_IS_BETTER[targetMetric] ?? true;
    const positiveOos = higherBetter
      ? oosMetrics.filter((m) => m > 0).length
      : oosMetrics.filter((m, i) => m < (windows[i]?.inSampleMetric ?? 0)).length;
    const consistency = oosMetrics.length > 0 ? positiveOos / oosMetrics.length : 0;

    return {
      strategyId: req.strategyId,
      targetMetric,
      windows,
      avgOutOfSampleMetric: avgOos,
      consistencyRatio: consistency,
    };
  }
}
