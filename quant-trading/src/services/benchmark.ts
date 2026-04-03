/**
 * BenchmarkService — fetches and caches benchmark index data for comparison.
 *
 * Supports common benchmarks:
 *  - CSI 300 (沪深300, symbol: 000300)
 *  - CSI 500 (中证500, symbol: 000905)
 *  - SSE Composite (上证综指, symbol: 000001)
 *  - S&P 500 (标普500, symbol: SPY) — US market
 *
 * Provides normalized return series for overlay on equity charts.
 */
import { createDataProvider } from "../data/market-data-provider.js";
import type { EquitySnapshot } from "../services/equity-history.js";
import type { QuantTradingConfig } from "../types.js";

// ── Types ────────────────────────────────────────────────────

export interface BenchmarkInfo {
  id: string;
  name: string;
  symbol: string;
  market: "cn_stock" | "us_stock";
}

export interface BenchmarkDataPoint {
  date: string;
  close: number;
  /** Normalized return from start (1.0 = no change) */
  normalizedReturn: number;
}

export interface BenchmarkComparison {
  benchmark: BenchmarkInfo;
  data: BenchmarkDataPoint[];
  /** Strategy return series (normalized) aligned to benchmark dates */
  strategyReturns: Array<{ date: string; normalizedReturn: number }>;
  /** Summary stats */
  stats: {
    benchmarkReturn: number;
    benchmarkReturnPct: number;
    strategyReturn: number;
    strategyReturnPct: number;
    alpha: number;
    /** Strategy return - benchmark return in percentage points */
    excessReturn: number;
    /** Information ratio (excess return / tracking error) — simplified */
    informationRatio: number;
  };
}

// ── Available benchmarks ─────────────────────────────────────

export const BENCHMARKS: BenchmarkInfo[] = [
  { id: "csi300", name: "沪深300", symbol: "000300", market: "cn_stock" },
  { id: "csi500", name: "中证500", symbol: "000905", market: "cn_stock" },
  { id: "sse", name: "上证综指", symbol: "000001", market: "cn_stock" },
  { id: "spy", name: "S&P 500", symbol: "SPY", market: "us_stock" },
];

// ── Service ──────────────────────────────────────────────────

export class BenchmarkService {
  private config: QuantTradingConfig;
  private cache: Map<string, { data: BenchmarkDataPoint[]; fetchedAt: number }> = new Map();
  private cacheTtlMs: number;
  private provider: ReturnType<typeof createDataProvider> | null = null;

  constructor(config: QuantTradingConfig, cacheTtlMs = 3600_000 /* 1 hour */) {
    this.config = config;
    this.cacheTtlMs = cacheTtlMs;
  }

  /** List all available benchmarks. */
  listBenchmarks(): BenchmarkInfo[] {
    return BENCHMARKS;
  }

  /** Get benchmark by ID. */
  getBenchmark(id: string): BenchmarkInfo | undefined {
    return BENCHMARKS.find((b) => b.id === id);
  }

  /**
   * Fetch benchmark data for a date range.
   * Uses cache if available and not expired.
   */
  async fetchBenchmarkData(
    benchmarkId: string,
    startDate: string,
    endDate: string,
  ): Promise<BenchmarkDataPoint[]> {
    const info = this.getBenchmark(benchmarkId);
    if (!info) {
      throw new Error(`Unknown benchmark: ${benchmarkId}`);
    }

    const cacheKey = `${benchmarkId}:${startDate}:${endDate}`;
    const cached = this.cache.get(cacheKey);
    if (cached && Date.now() - cached.fetchedAt < this.cacheTtlMs) {
      return cached.data;
    }

    if (!this.provider) this.provider = createDataProvider(this.config);
    const bars = await this.provider.getKlines(info.symbol, "1d", startDate, endDate);

    if (!bars || bars.length === 0) {
      return [];
    }

    const firstClose = bars[0]!.close;
    const data: BenchmarkDataPoint[] = bars.map((bar) => ({
      date: new Date(bar.timestamp).toISOString().slice(0, 10),
      close: bar.close,
      normalizedReturn: firstClose > 0 ? bar.close / firstClose : 1,
    }));

    this.cache.set(cacheKey, { data, fetchedAt: Date.now() });
    return data;
  }

  /**
   * Compare strategy equity against a benchmark.
   * Aligns dates and computes normalized returns from the earliest common date.
   */
  async compare(
    equitySnapshots: EquitySnapshot[],
    benchmarkId: string,
  ): Promise<BenchmarkComparison> {
    const info = this.getBenchmark(benchmarkId);
    if (!info) {
      throw new Error(`Unknown benchmark: ${benchmarkId}`);
    }

    if (equitySnapshots.length < 2) {
      throw new Error("Need at least 2 equity snapshots for comparison");
    }

    const sorted = [...equitySnapshots].sort((a, b) => a.date.localeCompare(b.date));
    const startDate = sorted[0]!.date;
    const endDate = sorted[sorted.length - 1]!.date;

    const benchData = await this.fetchBenchmarkData(benchmarkId, startDate, endDate);

    if (benchData.length < 2) {
      throw new Error(`Insufficient benchmark data for ${info.name}`);
    }

    // Build date-indexed maps
    const benchByDate = new Map(benchData.map((d) => [d.date, d]));
    const equityByDate = new Map(sorted.map((s) => [s.date, s]));

    // Find common dates
    const commonDates = sorted.map((s) => s.date).filter((d) => benchByDate.has(d));

    if (commonDates.length < 2) {
      throw new Error("Insufficient overlapping dates between strategy and benchmark");
    }

    const firstEquity = equityByDate.get(commonDates[0]!)!.equity;
    const lastEquity = equityByDate.get(commonDates[commonDates.length - 1]!)!.equity;

    const firstBench = benchByDate.get(commonDates[0]!)!;
    const lastBench = benchByDate.get(commonDates[commonDates.length - 1]!)!;

    // Normalize strategy returns
    const strategyReturns = commonDates.map((date) => ({
      date,
      normalizedReturn: firstEquity > 0 ? equityByDate.get(date)!.equity / firstEquity : 1,
    }));

    // Rebase benchmark to common start
    const rebasedBench = commonDates.map((date) => ({
      ...benchByDate.get(date)!,
      normalizedReturn: firstBench.close > 0 ? benchByDate.get(date)!.close / firstBench.close : 1,
    }));

    // Compute stats
    const strategyReturnPct =
      firstEquity > 0 ? ((lastEquity - firstEquity) / firstEquity) * 100 : 0;
    const benchReturnPct =
      firstBench.close > 0 ? ((lastBench.close - firstBench.close) / firstBench.close) * 100 : 0;
    const excessReturn = strategyReturnPct - benchReturnPct;

    // Simplified information ratio: excess return / tracking error (std dev of daily excess)
    const dailyExcess: number[] = [];
    for (let i = 1; i < commonDates.length; i++) {
      const sPrev = equityByDate.get(commonDates[i - 1]!)!.equity;
      const sNow = equityByDate.get(commonDates[i]!)!.equity;
      const bPrev = benchByDate.get(commonDates[i - 1]!)!.close;
      const bNow = benchByDate.get(commonDates[i]!)!.close;
      const sRet = sPrev > 0 ? (sNow - sPrev) / sPrev : 0;
      const bRet = bPrev > 0 ? (bNow - bPrev) / bPrev : 0;
      dailyExcess.push(sRet - bRet);
    }
    const meanExcess =
      dailyExcess.length > 0 ? dailyExcess.reduce((a, b) => a + b, 0) / dailyExcess.length : 0;
    const trackingError =
      dailyExcess.length > 1
        ? Math.sqrt(
            dailyExcess.reduce((sum, d) => sum + (d - meanExcess) ** 2, 0) /
              (dailyExcess.length - 1),
          )
        : 0;
    const informationRatio = trackingError > 0 ? (meanExcess / trackingError) * Math.sqrt(252) : 0;

    return {
      benchmark: info,
      data: rebasedBench,
      strategyReturns,
      stats: {
        benchmarkReturn: lastBench.close - firstBench.close,
        benchmarkReturnPct: benchReturnPct,
        strategyReturn: lastEquity - firstEquity,
        strategyReturnPct,
        alpha: excessReturn,
        excessReturn,
        informationRatio,
      },
    };
  }

  /** Clear the benchmark data cache. */
  clearCache(): void {
    this.cache.clear();
  }
}
