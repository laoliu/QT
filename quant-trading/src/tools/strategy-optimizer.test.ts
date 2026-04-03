/**
 * Tests for strategy-optimizer tool — mocks runPythonScript to verify
 * parameter forwarding, response formatting, and error handling.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createStrategyOptimizerTool } from "./strategy-optimizer.js";

const mockRun = vi.mocked(runPythonScript);
const mockApi = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
const config = { pythonPath: "python3" } as never;

function makeTool() {
  return createStrategyOptimizerTool(mockApi, config);
}

beforeEach(() => {
  mockRun.mockReset();
});

// ── Optimize action ──────────────────────────────────────────────

describe("strategy_optimizer / optimize", () => {
  it("returns error when strategyId is missing", async () => {
    const tool = makeTool();
    const result = await tool.execute({ action: "optimize" });
    expect(result).toHaveProperty("error");
  });

  it("calls param_optimizer.py with correct args", async () => {
    mockRun.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        strategyId: "macd",
        bestParams: { fast_period: 10, slow_period: 30, signal_period: 9 },
        bestMetric: 1.5,
        metric: "sharpeRatio",
        method: "grid",
        totalTrials: 12,
        totalCombinations: 12,
        topResults: [
          {
            params: { fast_period: 10, slow_period: 30, signal_period: 9 },
            totalReturn: 15.2,
            sharpeRatio: 1.5,
            maxDrawdown: 8.3,
            winRate: 55.0,
            totalTrades: 24,
          },
        ],
      },
    });

    const tool = makeTool();
    const result = await tool.execute({
      action: "optimize",
      strategyId: "macd",
      symbols: "600519",
      method: "grid",
      metric: "sharpeRatio",
    });

    expect(mockRun).toHaveBeenCalledWith(
      "engines/param_optimizer.py",
      expect.objectContaining({
        action: "optimize",
        strategyId: "macd",
        symbols: ["600519"],
        method: "grid",
        metric: "sharpeRatio",
      }),
      config,
    );

    expect(result).toHaveProperty("summary");
    const summary = (result as Record<string, unknown>).summary as Record<string, unknown>;
    expect(summary.bestParams).toEqual({ fast_period: 10, slow_period: 30, signal_period: 9 });
    expect(summary.bestMetricValue).toBe(1.5);
  });

  it("parses paramRanges JSON string", async () => {
    mockRun.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        bestParams: { period: 14 },
        bestMetric: 0.8,
        metric: "sharpeRatio",
        method: "grid",
        totalTrials: 5,
        topResults: [],
      },
    });

    const tool = makeTool();
    await tool.execute({
      action: "optimize",
      strategyId: "rsi",
      paramRanges: '{"period":{"min":10,"max":20,"step":5}}',
    });

    expect(mockRun).toHaveBeenCalledWith(
      "engines/param_optimizer.py",
      expect.objectContaining({
        paramRanges: { period: { min: 10, max: 20, step: 5 } },
      }),
      config,
    );
  });

  it("returns error on invalid paramRanges JSON", async () => {
    const tool = makeTool();
    const result = await tool.execute({
      action: "optimize",
      strategyId: "rsi",
      paramRanges: "not-json",
    });
    expect(result).toHaveProperty("error");
    expect((result as unknown as Record<string, string>).error).toContain("paramRanges");
  });

  it("handles Python script failure", async () => {
    mockRun.mockResolvedValue({ ok: false, error: "backtrader not installed" });

    const tool = makeTool();
    const result = await tool.execute({ action: "optimize", strategyId: "macd" });
    expect(result).toHaveProperty("error");
  });

  it("handles script returning ok:false in data", async () => {
    mockRun.mockResolvedValue({
      ok: true,
      data: { ok: false, error: "Unknown strategy" },
    });

    const tool = makeTool();
    const result = await tool.execute({ action: "optimize", strategyId: "unknown" });
    expect(result).toHaveProperty("error");
    expect((result as unknown as Record<string, string>).error).toContain("Unknown strategy");
  });

  it("formats topResults correctly", async () => {
    mockRun.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        bestParams: { fast_period: 10 },
        bestMetric: 1.2,
        metric: "sharpeRatio",
        method: "random",
        totalTrials: 3,
        topResults: [
          {
            params: { fast_period: 10 },
            totalReturn: 20.5,
            sharpeRatio: 1.2,
            maxDrawdown: 5.0,
            winRate: 60.0,
            totalTrades: 15,
          },
          {
            params: { fast_period: 15 },
            totalReturn: 18.0,
            sharpeRatio: 1.0,
            maxDrawdown: 7.0,
            winRate: 55.0,
            totalTrades: 12,
          },
        ],
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({ action: "optimize", strategyId: "macd" })) as Record<
      string,
      unknown
    >;
    const top = result.topResults as Array<Record<string, unknown>>;
    expect(top).toHaveLength(2);
    expect(top[0].rank).toBe(1);
    expect(top[0].totalReturn).toBe("20.5%");
  });

  it("detects market hint from symbols", async () => {
    mockRun.mockResolvedValue({ ok: true, data: { ok: true, bestParams: {}, topResults: [] } });

    const tool = makeTool();
    await tool.execute({ action: "optimize", strategyId: "rsi", symbols: "AAPL" });

    expect(mockRun).toHaveBeenCalledWith(
      "engines/param_optimizer.py",
      expect.objectContaining({ market: "us_stock" }),
      config,
    );
  });
});

// ── Validate signals action ──────────────────────────────────────

describe("strategy_optimizer / validate_signals", () => {
  it("calls signal_validator.py with correct args", async () => {
    mockRun.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        strategyId: "rsi",
        totalSignals: 10,
        buySignals: 5,
        sellSignals: 5,
        accuracy: { "1d": { buy: 60.0, sell: 50.0 }, "3d": { buy: 70.0, sell: 40.0 } },
        avgReturn: { "1d": { buy: 0.5, sell: -0.3 }, "3d": { buy: 1.2, sell: -0.8 } },
        signals: [],
      },
    });

    const tool = makeTool();
    const result = await tool.execute({
      action: "validate_signals",
      strategyId: "rsi",
      symbols: "000300",
      holdingDays: "1,3",
    });

    expect(mockRun).toHaveBeenCalledWith(
      "engines/signal_validator.py",
      expect.objectContaining({
        action: "validate_signals",
        strategyId: "rsi",
        holdingDays: [1, 3],
      }),
      config,
    );

    const r = result as Record<string, unknown>;
    expect(r.summary).toBeDefined();
    const summary = r.summary as Record<string, unknown>;
    expect(summary.totalSignals).toBe(10);
    expect(summary.buySignals).toBe(5);
  });

  it("formats accuracy table", async () => {
    mockRun.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        totalSignals: 4,
        buySignals: 2,
        sellSignals: 2,
        accuracy: { "1d": { buy: 100.0, sell: 50.0 } },
        avgReturn: { "1d": { buy: 2.0, sell: null } },
        signals: [],
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "validate_signals",
      strategyId: "macd",
    })) as Record<string, unknown>;

    const acc = result.accuracy as Array<Record<string, string>>;
    expect(acc).toHaveLength(1);
    expect(acc[0].horizon).toBe("1d");
    expect(acc[0].buyAccuracy).toBe("100%");
    expect(acc[0].sellAccuracy).toBe("50%");

    const avg = result.avgReturn as Array<Record<string, string>>;
    expect(avg[0].buyAvgReturn).toBe("2%");
    expect(avg[0].sellAvgReturn).toBe("N/A");
  });

  it("handles Python failure", async () => {
    mockRun.mockResolvedValue({ ok: false, error: "strategy not found" });

    const tool = makeTool();
    const result = await tool.execute({ action: "validate_signals", strategyId: "bad" });
    expect(result).toHaveProperty("error");
  });

  it("uses default holdingDays when not provided", async () => {
    mockRun.mockResolvedValue({
      ok: true,
      data: {
        ok: true,
        totalSignals: 0,
        buySignals: 0,
        sellSignals: 0,
        accuracy: {},
        avgReturn: {},
        signals: [],
      },
    });

    const tool = makeTool();
    await tool.execute({ action: "validate_signals", strategyId: "rsi" });

    expect(mockRun).toHaveBeenCalledWith(
      "engines/signal_validator.py",
      expect.objectContaining({ holdingDays: [1, 3, 5, 10] }),
      config,
    );
  });
});

// ── Unknown action ───────────────────────────────────────────────

describe("strategy_optimizer / unknown", () => {
  it("returns error for unknown action", async () => {
    const tool = makeTool();
    const result = await tool.execute({ action: "foo", strategyId: "rsi" });
    expect(result).toHaveProperty("error");
  });
});
