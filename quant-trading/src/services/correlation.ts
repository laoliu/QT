/**
 * Correlation & Pair Analysis Service (相关性 / 配对分析)
 *
 * Provides:
 *   - Pairwise correlation between two symbols
 *   - Correlation matrix for a group of symbols
 *   - Pair candidate screening (high-correlation pairs)
 *   - Spread z-score for mean-reversion signals
 */

export interface CorrelationResult {
  symbolA: string;
  symbolB: string;
  correlation: number;
  sampleSize: number;
}

export interface CorrelationMatrixEntry {
  row: string;
  col: string;
  value: number;
}

export interface SpreadAnalysis {
  symbolA: string;
  symbolB: string;
  currentSpread: number;
  meanSpread: number;
  stdSpread: number;
  zScore: number;
  /** Suggested action based on z-score */
  signal: "long_spread" | "short_spread" | "neutral";
}

/**
 * Compute Pearson correlation between two price series.
 * Both arrays must be the same length; returns NaN if length < 2.
 */
export function pearsonCorrelation(a: number[], b: number[]): number {
  const n = Math.min(a.length, b.length);
  if (n < 2) return NaN;

  let sumA = 0;
  let sumB = 0;
  for (let i = 0; i < n; i++) {
    sumA += a[i];
    sumB += b[i];
  }
  const meanA = sumA / n;
  const meanB = sumB / n;

  let cov = 0;
  let varA = 0;
  let varB = 0;
  for (let i = 0; i < n; i++) {
    const dA = a[i] - meanA;
    const dB = b[i] - meanB;
    cov += dA * dB;
    varA += dA * dA;
    varB += dB * dB;
  }

  const denom = Math.sqrt(varA * varB);
  if (denom === 0) return 0;
  return cov / denom;
}

/**
 * Convert raw prices to daily returns (log returns).
 */
export function toReturns(prices: number[]): number[] {
  const returns: number[] = [];
  for (let i = 1; i < prices.length; i++) {
    if (prices[i - 1] > 0) {
      returns.push(Math.log(prices[i] / prices[i - 1]));
    } else {
      returns.push(0);
    }
  }
  return returns;
}

/**
 * Compute pairwise correlation between two price arrays using their returns.
 */
export function computeCorrelation(
  pricesA: number[],
  pricesB: number[],
  symbolA: string,
  symbolB: string,
): CorrelationResult {
  const returnsA = toReturns(pricesA);
  const returnsB = toReturns(pricesB);
  const n = Math.min(returnsA.length, returnsB.length);
  const correlation = pearsonCorrelation(returnsA.slice(0, n), returnsB.slice(0, n));
  return { symbolA, symbolB, correlation, sampleSize: n };
}

/**
 * Build a correlation matrix for multiple symbols.
 *
 * @param priceMap - Map from symbol to array of prices (aligned by date)
 */
export function computeCorrelationMatrix(priceMap: Map<string, number[]>): {
  symbols: string[];
  matrix: CorrelationMatrixEntry[];
} {
  const symbols = [...priceMap.keys()];
  const returnsMap = new Map<string, number[]>();
  for (const [sym, prices] of priceMap) {
    returnsMap.set(sym, toReturns(prices));
  }

  const entries: CorrelationMatrixEntry[] = [];
  for (let i = 0; i < symbols.length; i++) {
    for (let j = i; j < symbols.length; j++) {
      const ra = returnsMap.get(symbols[i])!;
      const rb = returnsMap.get(symbols[j])!;
      const n = Math.min(ra.length, rb.length);
      const value = i === j ? 1 : pearsonCorrelation(ra.slice(0, n), rb.slice(0, n));
      entries.push({ row: symbols[i], col: symbols[j], value });
      if (i !== j) {
        entries.push({ row: symbols[j], col: symbols[i], value });
      }
    }
  }
  return { symbols, matrix: entries };
}

/**
 * Find highly correlated pairs from a group of symbols.
 *
 * @param priceMap - Map from symbol to price array (aligned dates)
 * @param minCorrelation - Minimum absolute correlation (default 0.7)
 */
export function findCorrelatedPairs(
  priceMap: Map<string, number[]>,
  minCorrelation = 0.7,
): CorrelationResult[] {
  const symbols = [...priceMap.keys()];
  const returnsMap = new Map<string, number[]>();
  for (const [sym, prices] of priceMap) {
    returnsMap.set(sym, toReturns(prices));
  }

  const results: CorrelationResult[] = [];
  for (let i = 0; i < symbols.length; i++) {
    for (let j = i + 1; j < symbols.length; j++) {
      const ra = returnsMap.get(symbols[i])!;
      const rb = returnsMap.get(symbols[j])!;
      const n = Math.min(ra.length, rb.length);
      if (n < 10) continue; // Need enough data
      const corr = pearsonCorrelation(ra.slice(0, n), rb.slice(0, n));
      if (Math.abs(corr) >= minCorrelation) {
        results.push({
          symbolA: symbols[i],
          symbolB: symbols[j],
          correlation: corr,
          sampleSize: n,
        });
      }
    }
  }
  // Sort by absolute correlation descending
  results.sort((a, b) => Math.abs(b.correlation) - Math.abs(a.correlation));
  return results;
}

/**
 * Compute spread z-score for a pair of price series.
 * Uses simple price ratio spread (log(A/B)).
 *
 * @param lookback - Rolling window for mean/std (default: all data)
 * @param entryZ - Z-score threshold for signal (default 2.0)
 */
export function computeSpreadZScore(
  pricesA: number[],
  pricesB: number[],
  symbolA: string,
  symbolB: string,
  options: { lookback?: number; entryZ?: number } = {},
): SpreadAnalysis {
  const { lookback, entryZ = 2.0 } = options;
  const n = Math.min(pricesA.length, pricesB.length);

  // Compute log spread
  const spreads: number[] = [];
  for (let i = 0; i < n; i++) {
    if (pricesA[i] > 0 && pricesB[i] > 0) {
      spreads.push(Math.log(pricesA[i] / pricesB[i]));
    }
  }

  if (spreads.length < 2) {
    return {
      symbolA,
      symbolB,
      currentSpread: 0,
      meanSpread: 0,
      stdSpread: 0,
      zScore: 0,
      signal: "neutral",
    };
  }

  // Use lookback window or all data
  const window = lookback && lookback < spreads.length ? spreads.slice(-lookback) : spreads;
  const currentSpread = spreads[spreads.length - 1];

  let sum = 0;
  for (const s of window) sum += s;
  const mean = sum / window.length;

  let varSum = 0;
  for (const s of window) varSum += (s - mean) ** 2;
  const std = Math.sqrt(varSum / window.length);

  const zScore = std > 0 ? (currentSpread - mean) / std : 0;

  let signal: SpreadAnalysis["signal"] = "neutral";
  if (zScore >= entryZ)
    signal = "short_spread"; // Spread too high → expect reversion down
  else if (zScore <= -entryZ) signal = "long_spread"; // Spread too low → expect reversion up

  return { symbolA, symbolB, currentSpread, meanSpread: mean, stdSpread: std, zScore, signal };
}
