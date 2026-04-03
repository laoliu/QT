/**
 * Unit tests for options_strategy AI tool.
 *
 * Mocks runPythonScript to test action routing and response formatting
 * without real Python execution.
 */
import { describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";

const mockRunPython = vi.fn();
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: (...args: unknown[]) => mockRunPython(...args),
}));

const { createOptionsStrategyTool } = await import("./options-strategy.js");

const fakeApi = {
  logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
} as never;

function makeConfig(): QuantTradingConfig {
  return {
    dataDir: "/tmp/oc-quant-test",
    dataProvider: "demo",
    broker: "paper",
    initialCash: 1_000_000,
    pythonPath: "python3",
    backtestEngine: "builtin",
    optionsConfig: {
      riskFreeRate: 0.02,
    },
  } as QuantTradingConfig;
}

describe("options_strategy tool", () => {
  const tool = createOptionsStrategyTool(fakeApi, makeConfig());

  it("has correct metadata", () => {
    expect(tool.name).toBe("options_strategy");
    expect(tool.description).toBeTruthy();
    expect(tool.parameters.properties.action).toBeDefined();
  });

  it("list action - returns strategy templates", async () => {
    const templates = [
      { id: "long_call", name: "买入看涨", description: "看多标的" },
      { id: "bull_call_spread", name: "牛市看涨价差", description: "温和看多" },
    ];
    mockRunPython.mockResolvedValueOnce({ ok: true, data: templates });

    const result: any = await tool.execute({ action: "list" });
    expect(result.strategies).toBeDefined();
  });

  it("recommend action - passes camelCase parameters to engine", async () => {
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: [
        {
          id: "long_call",
          name: "买入看涨",
          description: "看多标的时使用",
          score: 5,
          outlook: "bullish",
          risk: "limited",
          numLegs: 1,
        },
      ],
    });

    const result: any = await tool.execute({
      action: "recommend",
      underlying: "510050",
      outlook: "bullish",
      riskTolerance: "moderate",
      spotPrice: 3.1,
    });

    // Verify camelCase keys are passed to Python (not snake_case)
    expect(mockRunPython).toHaveBeenCalledWith(
      "engines/option_strategy_engine.py",
      expect.objectContaining({
        action: "recommend",
        underlying: "510050",
        riskTolerance: "moderate",
        underlyingPrice: 3.1,
      }),
      expect.anything(),
    );
    // Verify Python return shape is correctly mapped
    expect(result.recommendations).toBeDefined();
    expect(result.recommendations[0].strategy).toBe("long_call");
    expect(result.recommendations[0].name).toBe("买入看涨");
    expect(result.recommendations[0].reason).toBe("看多标的时使用");
  });

  it("payoff action - sends camelCase params and computes payoff curve", async () => {
    // Simulate a large payoff curve
    const curve = Array.from({ length: 500 }, (_, i) => ({
      price: 2.5 + i * 0.01,
      pnl: (2.5 + i * 0.01 - 3.0) * 10000 - 2000,
    }));
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: {
        strategyId: "long_call",
        legs: [{ symbol: "10005646", side: "buy", quantity: 1, premium: 0.2 }],
        netPremium: 0.2,
        maxProfit: Infinity,
        maxLoss: -2000,
        breakeven: [3.2],
        payoffCurve: curve,
      },
    });

    const result: any = await tool.execute({
      action: "payoff",
      legs: JSON.stringify([{ symbol: "10005646", side: "buy", quantity: 1, premium: 0.2 }]),
      spotPrice: 3.1,
      strategyId: "long_call",
    });

    // Verify camelCase keys (templateId, underlyingPrice) not snake_case
    expect(mockRunPython).toHaveBeenCalledWith(
      "engines/option_strategy_engine.py",
      expect.objectContaining({
        action: "payoff",
        templateId: "long_call",
        underlyingPrice: 3.1,
      }),
      expect.anything(),
    );
    // Verify no snake_case keys
    const callArgs = mockRunPython.mock.calls[mockRunPython.mock.calls.length - 1][1];
    expect(callArgs.strategy_id).toBeUndefined();
    expect(callArgs.spot_price).toBeUndefined();

    expect(result).toBeDefined();
    // Payoff curve should be sampled down if too long
    if (result.payoffCurve) {
      expect(result.payoffCurve.length).toBeLessThanOrEqual(150);
    }
  });

  it("scenario action - sends templateId for auto-leg construction", async () => {
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: {
        scenarioGrid: [{ spotMove: 0.05, ivMove: 0.0, daysForward: 0, pnl: 500 }],
      },
    });

    const result = await tool.execute({
      action: "scenario",
      strategyId: "long_call",
      spotPrice: 3.1,
      daysToExpiry: 30,
    });

    // Verify camelCase params sent
    expect(mockRunPython).toHaveBeenCalledWith(
      "engines/option_strategy_engine.py",
      expect.objectContaining({
        action: "scenario",
        templateId: "long_call",
        underlyingPrice: 3.1,
      }),
      expect.anything(),
    );
    expect(result).toBeDefined();
  });

  it("compare action - sends strategy IDs as camelCase params", async () => {
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: [
        { name: "long_call", netPremium: -200, maxProfit: 5000, maxLoss: -200 },
        { name: "bull_call_spread", netPremium: -100, maxProfit: 400, maxLoss: -100 },
      ],
    });

    const result = await tool.execute({
      action: "compare",
      strategies: "long_call, bull_call_spread",
      spotPrice: 3.1,
    });

    // Verify camelCase params and strategies as array
    expect(mockRunPython).toHaveBeenCalledWith(
      "engines/option_strategy_engine.py",
      expect.objectContaining({
        action: "compare",
        strategies: ["long_call", "bull_call_spread"],
        underlyingPrice: 3.1,
      }),
      expect.anything(),
    );
    expect(result).toBeDefined();
  });

  it("optimal_strike action - sends camelCase params", async () => {
    mockRunPython.mockResolvedValueOnce({
      ok: true,
      data: {
        strike: 3.1,
        delta: 0.3,
        targetDelta: 0.3,
        criteria: "delta",
      },
    });

    const result = await tool.execute({
      action: "optimal_strike",
      strategyId: "long_call",
      spotPrice: 3.1,
    });

    expect(mockRunPython).toHaveBeenCalledWith(
      "engines/option_strategy_engine.py",
      expect.objectContaining({
        action: "optimal_strike",
        templateId: "long_call",
        underlyingPrice: 3.1,
      }),
      expect.anything(),
    );
    expect(result).toBeDefined();
  });

  it("returns error for unknown action", async () => {
    const result: any = await tool.execute({ action: "nonexistent" });
    expect(result.error).toBeTruthy();
  });

  it("recommend without outlook returns error", async () => {
    const result: any = await tool.execute({ action: "recommend" });
    expect(result.error).toContain("outlook");
  });

  it("recommend with python failure returns error", async () => {
    mockRunPython.mockResolvedValueOnce({ ok: false, error: "Python crashed" });
    const result: any = await tool.execute({ action: "recommend", outlook: "bullish" });
    expect(result.error).toBeTruthy();
  });

  it("payoff with invalid JSON legs returns error", async () => {
    const result: any = await tool.execute({ action: "payoff", legs: "not-json{[" });
    expect(result.error).toContain("legs");
  });

  it("payoff without strategyId or legs returns error", async () => {
    const result: any = await tool.execute({ action: "payoff" });
    expect(result.error).toContain("strategyId");
  });

  it("payoff with python returning empty data returns error", async () => {
    mockRunPython.mockResolvedValueOnce({ ok: true, data: null });
    const result: any = await tool.execute({ action: "payoff", strategyId: "long_call" });
    expect(result.error).toContain("数据为空");
  });

  it("scenario without strategyId returns error", async () => {
    const result: any = await tool.execute({ action: "scenario" });
    expect(result.error).toContain("strategyId");
  });

  it("compare without strategies returns error", async () => {
    const result: any = await tool.execute({ action: "compare" });
    expect(result.error).toContain("strategies");
  });

  it("optimal_strike without strategyId returns error", async () => {
    const result: any = await tool.execute({ action: "optimal_strike" });
    expect(result.error).toContain("strategyId");
  });
});
