/**
 * Unit tests for options_greeks_dashboard tool.
 */
import { describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({
    ok: true,
    data: {
      portfolioGreeks: {
        delta: 12.5,
        gamma: 1.2,
        theta: -2.8,
        vega: 8.3,
        rho: 0.5,
        charm: 0.02,
        vanna: 0.01,
        vomma: 0.005,
        cashDelta: 125000,
        cashGamma: 12000,
        cashTheta: -28000,
        cashVega: 83000,
        totalMarketValue: 500000,
        totalUnrealizedPnl: 15000,
      },
      positions: [
        {
          symbol: "10005646",
          underlying: "510050",
          optionType: "call",
          strike: 3.0,
          expiry: "2026-06-25",
          dte: 79,
          quantity: 10,
          delta: 0.55,
          gamma: 0.012,
          theta: -0.003,
          vega: 0.045,
          rho: 0.001,
          charm: 0.0001,
          vanna: 0.0002,
          vomma: 0.0001,
          iv: 0.235,
          cashDelta: 55000,
          cashGamma: 6000,
          cashTheta: -3000,
          cashVega: 4500,
          marketValue: 250000,
          unrealizedPnl: 8000,
          moneyness: 1.033,
        },
      ],
      positionCount: 1,
    },
  }),
}));

const { createOptionsGreeksDashboardTool } = await import("../tools/options-greeks-dashboard.js");

function makeApi() {
  return {
    logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() },
  } as any;
}

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
      dividendYield: 0,
    },
  } as QuantTradingConfig;
}

describe("options_greeks_dashboard tool", () => {
  it("has correct tool name and required action parameter", () => {
    const tool = createOptionsGreeksDashboardTool(makeApi(), makeConfig(), () => null);
    expect(tool.name).toBe("options_greeks_dashboard");
    expect(tool.parameters.properties.action).toBeDefined();
    expect(tool.parameters.properties.action.enum).toContain("dashboard");
    expect(tool.parameters.properties.action.enum).toContain("stress_test");
    expect(tool.parameters.properties.action.enum).toContain("var");
    expect(tool.parameters.properties.action.enum).toContain("pretrade");
    expect(tool.parameters.properties.action.enum).toContain("hedge");
  });

  it("returns empty message when no option positions", async () => {
    const engine = {
      getPositions: () => [],
    } as any;
    const tool = createOptionsGreeksDashboardTool(makeApi(), makeConfig(), () => engine);
    const result = await tool.execute({ action: "dashboard" });
    expect(result).toHaveProperty("message");
    expect(result.count).toBe(0);
  });

  it("dashboard action returns portfolio greeks when positions exist", async () => {
    const positions = [
      {
        symbol: "10005646",
        quantity: 10,
        optionType: "call",
        strike: 3.0,
        expiry: "2026-06-25",
        underlying: "510050",
        currentPrice: 0.25,
        avgCost: 0.22,
        greeks: {
          delta: 0.55,
          gamma: 0.012,
          theta: -0.003,
          vega: 0.045,
          rho: 0.001,
          impliedVol: 0.235,
          calculatedAt: Date.now(),
        },
      },
    ];
    const engine = {
      getPositions: () => positions,
    } as any;
    const tool = createOptionsGreeksDashboardTool(makeApi(), makeConfig(), () => engine);
    const result = await tool.execute({ action: "dashboard" });
    expect(result).toHaveProperty("portfolioGreeks");
    expect(result).toHaveProperty("positionCount");
    expect(result.portfolioGreeks).toHaveProperty("cashDelta");
  });

  it("pretrade action validates required parameters", async () => {
    const engine = { getPositions: () => [] } as any;
    const tool = createOptionsGreeksDashboardTool(makeApi(), makeConfig(), () => engine);
    const result = await tool.execute({ action: "pretrade" });
    expect(result).toHaveProperty("error");
  });
});
