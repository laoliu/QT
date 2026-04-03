import { describe, it, expect } from "vitest";
import type { BacktestResult, StrategyDefinition, StrategyParameter } from "../types.js";
import {
  generateParameterRanges,
  generateSingleRange,
  cartesianProduct,
  getMetricValue,
  type ParameterRange,
} from "./parameter-optimizer.js";

// ── Helpers ──────────────────────────────────────────────────

const numParam = (name: string, opts: Partial<StrategyParameter> = {}): StrategyParameter => ({
  name,
  type: "number",
  default: 10,
  ...opts,
});

const boolParam = (name: string): StrategyParameter => ({
  name,
  type: "boolean",
  default: true,
});

const strParam = (name: string): StrategyParameter => ({
  name,
  type: "string",
  default: "default",
});

function fakeResult(overrides: Partial<BacktestResult> = {}): BacktestResult {
  return {
    strategyId: "test",
    symbols: ["000300"],
    startDate: "2024-01-01",
    endDate: "2024-12-31",
    initialCash: 100_000,
    finalValue: 110_000,
    totalReturn: 0.1,
    annualizedReturn: 0.1,
    maxDrawdown: 0.05,
    sharpeRatio: 1.5,
    winRate: 0.6,
    totalTrades: 50,
    signals: [],
    equityCurve: [],
    ...overrides,
  };
}

// ── generateSingleRange ──────────────────────────────────────

describe("generateSingleRange", () => {
  it("generates boolean range", () => {
    const range = generateSingleRange(boolParam("useStop"));
    expect(range.name).toBe("useStop");
    expect(range.type).toBe("boolean");
    expect(range.values).toEqual([true, false]);
  });

  it("generates numeric range with min/max/step", () => {
    const range = generateSingleRange(numParam("period", { min: 5, max: 25, step: 5 }));
    expect(range.name).toBe("period");
    expect(range.type).toBe("number");
    expect(range.values).toEqual([5, 10, 15, 20, 25]);
  });

  it("includes max if not aligned to step", () => {
    const range = generateSingleRange(numParam("period", { min: 5, max: 27, step: 10 }));
    expect(range.values).toContain(27);
  });

  it("generates string range with only default", () => {
    const range = generateSingleRange(strParam("mode"));
    expect(range.values).toEqual(["default"]);
  });

  it("auto-calculates step when not specified", () => {
    const range = generateSingleRange(numParam("period", { min: 0, max: 100 }));
    expect(range.values.length).toBeGreaterThan(1);
    expect(range.values.length).toBeLessThanOrEqual(15);
  });

  it("uses default-based range when min/max not specified", () => {
    const range = generateSingleRange(numParam("period", { default: 20 }));
    expect(range.values.length).toBeGreaterThan(0);
    // Range should be roughly 0.5x to 2x the default
    expect(range.values[0] as number).toBeLessThanOrEqual(20);
    expect(range.values[range.values.length - 1] as number).toBeGreaterThanOrEqual(20);
  });
});

// ── generateParameterRanges ──────────────────────────────────

describe("generateParameterRanges", () => {
  const stratDef: StrategyDefinition = {
    id: "test",
    name: "Test",
    description: "",
    version: "1.0",
    engine: "builtin",
    entryFile: "test.ts",
    parameters: [
      numParam("fast", { min: 5, max: 25, step: 5 }),
      numParam("slow", { min: 20, max: 60, step: 10 }),
      boolParam("useStop"),
    ],
  };

  it("generates ranges only for targeted params", () => {
    const ranges = generateParameterRanges(stratDef, ["fast"]);
    expect(ranges).toHaveLength(1);
    expect(ranges[0]!.name).toBe("fast");
  });

  it("generates ranges for multiple params", () => {
    const ranges = generateParameterRanges(stratDef, ["fast", "slow"]);
    expect(ranges).toHaveLength(2);
  });

  it("skips unknown params", () => {
    const ranges = generateParameterRanges(stratDef, ["unknown"]);
    expect(ranges).toHaveLength(0);
  });
});

// ── cartesianProduct ─────────────────────────────────────────

describe("cartesianProduct", () => {
  it("returns empty object array for no ranges", () => {
    const result = cartesianProduct([]);
    expect(result).toEqual([{}]);
  });

  it("single range", () => {
    const ranges: ParameterRange[] = [{ name: "x", type: "number", values: [1, 2, 3] }];
    const result = cartesianProduct(ranges);
    expect(result).toEqual([{ x: 1 }, { x: 2 }, { x: 3 }]);
  });

  it("two ranges produce correct product", () => {
    const ranges: ParameterRange[] = [
      { name: "a", type: "number", values: [1, 2] },
      { name: "b", type: "boolean", values: [true, false] },
    ];
    const result = cartesianProduct(ranges);
    expect(result).toHaveLength(4);
    expect(result).toContainEqual({ a: 1, b: true });
    expect(result).toContainEqual({ a: 1, b: false });
    expect(result).toContainEqual({ a: 2, b: true });
    expect(result).toContainEqual({ a: 2, b: false });
  });

  it("three ranges produce correct count", () => {
    const ranges: ParameterRange[] = [
      { name: "x", type: "number", values: [1, 2, 3] },
      { name: "y", type: "number", values: [10, 20] },
      { name: "z", type: "boolean", values: [true, false] },
    ];
    const result = cartesianProduct(ranges);
    expect(result).toHaveLength(3 * 2 * 2);
  });
});

// ── getMetricValue ───────────────────────────────────────────

describe("getMetricValue", () => {
  const result = fakeResult({
    totalReturn: 0.15,
    annualizedReturn: 0.12,
    maxDrawdown: 0.08,
    sharpeRatio: 2.0,
    sortinoRatio: 2.5,
    calmarRatio: 1.5,
    winRate: 0.65,
    profitFactor: 1.8,
    totalTrades: 100,
  });

  it("extracts totalReturn", () => {
    expect(getMetricValue(result, "totalReturn")).toBe(0.15);
  });

  it("extracts sharpeRatio", () => {
    expect(getMetricValue(result, "sharpeRatio")).toBe(2.0);
  });

  it("extracts maxDrawdown", () => {
    expect(getMetricValue(result, "maxDrawdown")).toBe(0.08);
  });

  it("extracts sortinoRatio", () => {
    expect(getMetricValue(result, "sortinoRatio")).toBe(2.5);
  });

  it("defaults optional metrics to 0", () => {
    const sparse = fakeResult();
    expect(getMetricValue(sparse, "sortinoRatio")).toBe(0);
    expect(getMetricValue(sparse, "calmarRatio")).toBe(0);
    expect(getMetricValue(sparse, "profitFactor")).toBe(0);
  });
});

// ── Edge case: generateSingleRange fractional parameters ──────

describe("generateSingleRange edge cases", () => {
  it("generates fractional steps for float ranges", () => {
    const range = generateSingleRange(numParam("stdDev", { min: 0.5, max: 4.0, default: 2 }));
    expect(range.type).toBe("number");
    // Should include values < 1.0 (fractional step, not step=1)
    const values = range.values as number[];
    expect(values.length).toBeGreaterThan(2);
    expect(values[0]).toBeCloseTo(0.5);
    expect(values[values.length - 1]).toBeCloseTo(4.0);
    // At least one value between 0.5 and 1.0
    expect(values.some((v) => v > 0.5 && v < 1.0)).toBe(true);
  });

  it("uses integer step for integer ranges", () => {
    const range = generateSingleRange(numParam("period", { min: 5, max: 50, default: 20 }));
    const values = range.values as number[];
    // Step should be about 4-5 (integer), not 4.5
    expect(values.every((v) => Number.isInteger(v))).toBe(true);
  });
});
