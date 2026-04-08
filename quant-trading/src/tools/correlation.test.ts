import { describe, expect, it, vi } from "vitest";
import { createCorrelationTool } from "./correlation.js";

const fakeApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
const fakeConfig = {} as never;

/**
 * Helper: build a mock getKlines that returns preset close arrays per symbol.
 */
function mockKlines(map: Record<string, number[]>) {
  return async (symbol: string) => {
    const arr = map[symbol];
    if (!arr) return null;
    return { klines: arr.map((c) => ({ close: c })) };
  };
}

describe("correlation_analysis tool", () => {
  // ── pair ──

  it("computes pair correlation", async () => {
    const kfn = mockKlines({
      A: [10, 11, 12, 13, 14],
      B: [20, 22, 24, 26, 28],
    });
    const tool = createCorrelationTool(fakeApi, fakeConfig, kfn);
    const raw = await tool.execute({ action: "pair", symbolA: "A", symbolB: "B" });
    const res = JSON.parse(raw as string);
    expect(res.correlation).toBeCloseTo(1, 1);
    expect(res.interpretation).toBe("强相关");
  });

  it("returns error when missing symbol params", async () => {
    const tool = createCorrelationTool(fakeApi, fakeConfig, async () => null);
    const raw = await tool.execute({ action: "pair" });
    expect(JSON.parse(raw as string).error).toBeDefined();
  });

  it("returns error when klines unavailable", async () => {
    const tool = createCorrelationTool(fakeApi, fakeConfig, async () => null);
    const raw = await tool.execute({ action: "pair", symbolA: "A", symbolB: "B" });
    expect(JSON.parse(raw as string).error).toContain("K线");
  });

  // ── matrix ──

  it("computes correlation matrix", async () => {
    const kfn = mockKlines({
      A: [10, 11, 12, 13, 14],
      B: [20, 22, 24, 26, 28],
      C: [5, 4, 3, 2, 1],
    });
    const tool = createCorrelationTool(fakeApi, fakeConfig, kfn);
    const raw = await tool.execute({ action: "matrix", symbols: "A,B,C" });
    const res = JSON.parse(raw as string);
    expect(res.symbols.length).toBe(3);
    expect(res.correlations.length).toBeGreaterThan(0);
  });

  it("returns error when fewer than 2 symbols for matrix", async () => {
    const tool = createCorrelationTool(fakeApi, fakeConfig, async () => null);
    const raw = await tool.execute({ action: "matrix", symbols: "A" });
    expect(JSON.parse(raw as string).error).toContain("至少");
  });

  // ── find_pairs ──

  it("finds correlated pairs", async () => {
    // Use enough data with proportional prices so log-return correlation is high
    const baseA = Array.from({ length: 30 }, (_, i) => 100 + i * 2 + Math.sin(i) * 3);
    const baseB = baseA.map((v) => v * 1.5); // perfectly proportional
    const baseC = Array.from({ length: 30 }, (_, i) => 50 + (i % 3 === 0 ? 10 : -5));
    const kfn = mockKlines({ A: baseA, B: baseB, C: baseC });
    const tool = createCorrelationTool(fakeApi, fakeConfig, kfn);
    const raw = await tool.execute({ action: "find_pairs", symbols: "A,B,C", minCorrelation: 0.8 });
    const res = JSON.parse(raw as string);
    // A-B should be highly correlated (proportional prices → identical log-returns)
    expect(res.count).toBeGreaterThan(0);
  });

  // ── spread ──

  it("computes spread z-score", async () => {
    const kfn = mockKlines({
      A: [10, 11, 12, 11, 10, 11, 12],
      B: [20, 22, 24, 22, 20, 22, 24],
    });
    const tool = createCorrelationTool(fakeApi, fakeConfig, kfn);
    const raw = await tool.execute({ action: "spread", symbolA: "A", symbolB: "B" });
    const res = JSON.parse(raw as string);
    expect(res.zScore).toBeDefined();
    expect(typeof res.currentSpread).toBe("number");
  });

  // ── unknown action ──

  it("returns error for unknown action", async () => {
    const tool = createCorrelationTool(fakeApi, fakeConfig, async () => null);
    const raw = await tool.execute({ action: "nope" });
    expect(JSON.parse(raw as string).error).toContain("δ֪");
  });
});
