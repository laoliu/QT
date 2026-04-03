import { describe, expect, it } from "vitest";
import {
  computeCorrelation,
  computeCorrelationMatrix,
  computeSpreadZScore,
  findCorrelatedPairs,
  pearsonCorrelation,
  toReturns,
} from "./correlation.js";

describe("correlation analysis", () => {
  // ── pearsonCorrelation ──

  it("returns 1 for identical series", () => {
    const a = [1, 2, 3, 4, 5];
    expect(pearsonCorrelation(a, a)).toBeCloseTo(1);
  });

  it("returns -1 for perfectly inversely correlated series", () => {
    const a = [1, 2, 3, 4, 5];
    const b = [5, 4, 3, 2, 1];
    expect(pearsonCorrelation(a, b)).toBeCloseTo(-1);
  });

  it("returns ~0 for uncorrelated series", () => {
    const a = [1, -1, 1, -1, 1, -1, 1, -1];
    const b = [1, 1, -1, -1, 1, 1, -1, -1];
    const corr = pearsonCorrelation(a, b);
    expect(Math.abs(corr)).toBeLessThan(0.3);
  });

  it("returns NaN for single element", () => {
    expect(pearsonCorrelation([1], [2])).toBeNaN();
  });

  it("returns 0 for constant series", () => {
    expect(pearsonCorrelation([5, 5, 5], [1, 2, 3])).toBe(0);
  });

  // ── toReturns ──

  it("converts prices to log returns", () => {
    const prices = [100, 110, 105];
    const returns = toReturns(prices);
    expect(returns).toHaveLength(2);
    expect(returns[0]).toBeCloseTo(Math.log(110 / 100));
    expect(returns[1]).toBeCloseTo(Math.log(105 / 110));
  });

  it("handles zero prices gracefully", () => {
    const returns = toReturns([0, 100, 200]);
    expect(returns[0]).toBe(0); // log(100/0) → 0 (guarded)
    expect(returns[1]).toBeCloseTo(Math.log(2));
  });

  // ── computeCorrelation ──

  it("computes correlation between two price series", () => {
    // Two trending-up series should be highly correlated
    const pricesA = [100, 102, 104, 106, 108, 110, 112];
    const pricesB = [50, 51, 52, 53, 54, 55, 56];
    const result = computeCorrelation(pricesA, pricesB, "A", "B");
    expect(result.symbolA).toBe("A");
    expect(result.symbolB).toBe("B");
    expect(result.correlation).toBeGreaterThan(0.9);
    expect(result.sampleSize).toBe(6); // returns are n-1
  });

  // ── computeCorrelationMatrix ──

  it("builds a correlation matrix", () => {
    const priceMap = new Map<string, number[]>();
    priceMap.set("A", [100, 110, 100, 110, 100]);
    priceMap.set("B", [50, 55, 50, 55, 50]);
    // C moves inversely — when A goes up, C goes down
    priceMap.set("C", [100, 90, 100, 90, 100]);

    const { symbols, matrix } = computeCorrelationMatrix(priceMap);
    expect(symbols).toHaveLength(3);

    // A-A should be 1
    const aa = matrix.find((e) => e.row === "A" && e.col === "A");
    expect(aa?.value).toBe(1);

    // A-B should be high positive
    const ab = matrix.find((e) => e.row === "A" && e.col === "B");
    expect(ab?.value).toBeGreaterThan(0.9);

    // A-C should be high negative
    const ac = matrix.find((e) => e.row === "A" && e.col === "C");
    expect(ac?.value).toBeLessThan(-0.9);
  });

  // ── findCorrelatedPairs ──

  it("finds highly correlated pairs", () => {
    const priceMap = new Map<string, number[]>();
    // A and B move together
    priceMap.set("A", [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120]);
    priceMap.set("B", [50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60]);
    // C moves independently
    priceMap.set("C", [100, 98, 102, 97, 105, 95, 110, 90, 115, 85, 120]);

    const pairs = findCorrelatedPairs(priceMap, 0.8);
    expect(pairs.length).toBeGreaterThanOrEqual(1);
    expect(pairs[0].symbolA).toBe("A");
    expect(pairs[0].symbolB).toBe("B");
  });

  it("returns empty when no pairs meet threshold", () => {
    const priceMap = new Map<string, number[]>();
    priceMap.set("A", [100, 98, 102, 97, 105, 95, 110, 90, 115, 85, 120]);
    priceMap.set("B", [100, 102, 98, 105, 95, 110, 90, 115, 85, 120, 100]);

    const pairs = findCorrelatedPairs(priceMap, 0.99);
    // Unlikely to meet 0.99 threshold
    expect(pairs.length).toBe(0);
  });

  it("requires minimum sample size", () => {
    const priceMap = new Map<string, number[]>();
    priceMap.set("A", [100, 102, 104]);
    priceMap.set("B", [50, 51, 52]);

    // Only 2 returns → below 10 minimum
    const pairs = findCorrelatedPairs(priceMap, 0.5);
    expect(pairs).toHaveLength(0);
  });

  // ── computeSpreadZScore ──

  it("computes spread z-score for a pair", () => {
    // Create two series that maintain a stable spread
    const pricesA = Array.from({ length: 50 }, (_, i) => 100 + i * 0.5);
    const pricesB = Array.from({ length: 50 }, (_, i) => 50 + i * 0.25);

    const result = computeSpreadZScore(pricesA, pricesB, "A", "B");
    expect(result.symbolA).toBe("A");
    expect(result.symbolB).toBe("B");
    expect(typeof result.zScore).toBe("number");
    expect(typeof result.signal).toBe("string");
  });

  it("returns neutral for insufficient data", () => {
    const result = computeSpreadZScore([100], [50], "A", "B");
    expect(result.signal).toBe("neutral");
    expect(result.zScore).toBe(0);
  });

  it("signals long_spread when z-score is very negative", () => {
    // Construct a scenario where spread collapses
    const pricesA = Array.from({ length: 40 }, () => 100);
    const pricesB = Array.from({ length: 40 }, () => 50);
    // Last few: A drops sharply relative to B
    pricesA.push(80);
    pricesB.push(50);

    const result = computeSpreadZScore(pricesA, pricesB, "A", "B", { entryZ: 1.5 });
    // z-score should be negative (spread dropped)
    if (result.zScore <= -1.5) {
      expect(result.signal).toBe("long_spread");
    }
  });

  it("respects lookback window", () => {
    const pricesA = Array.from({ length: 100 }, (_, i) => 100 + Math.sin(i * 0.1) * 10);
    const pricesB = Array.from({ length: 100 }, (_, i) => 50 + Math.cos(i * 0.1) * 5);

    const full = computeSpreadZScore(pricesA, pricesB, "A", "B");
    const windowed = computeSpreadZScore(pricesA, pricesB, "A", "B", { lookback: 20 });
    // Different lookback should produce different mean/std
    expect(full.meanSpread).not.toBe(windowed.meanSpread);
  });
});
