import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
/**
 * Tests for the portfolio_backtest AI tool.
 */
import { describe, expect, it } from "vitest";
import { createPortfolioBacktestTool } from "./portfolio-backtest.js";

function fakeApi(): OpenClawPluginApi {
  return {
    id: "quant-trading",
    name: "quant-trading",
    source: "test",
    config: {},
    pluginConfig: {},
    runtime: { version: "test" } as never,
    logger: { info() {}, warn() {}, error() {}, debug() {} },
    registerTool() {},
    registerChannel() {},
    registerGatewayMethod() {},
    registerCli() {},
    registerService() {},
    registerProvider() {},
    registerHook() {},
    registerHttpRoute() {},
    registerCommand() {},
    on() {},
    resolvePath: (p: string) => p,
  } as unknown as OpenClawPluginApi;
}

describe("portfolio_backtest tool", () => {
  const tool = createPortfolioBacktestTool(fakeApi(), { dataProvider: "demo" });

  it("should have correct name and description", () => {
    expect(tool.name).toBe("portfolio_backtest");
    expect(tool.description).toContain("持仓");
    expect(tool.description).toContain("回测");
  });

  it("should define required parameters", () => {
    const params = tool.parameters;
    expect(params.properties.action).toBeDefined();
    expect(params.properties.strategyIds).toBeDefined();
    expect(params.properties.symbols).toBeDefined();
    expect(params.properties.startDate).toBeDefined();
    expect(params.properties.endDate).toBeDefined();
    expect(params.properties.initialCash).toBeDefined();
    expect(params.properties.weightMode).toBeDefined();
    expect(params.required).toContain("action");
  });

  it("action=run without symbols or portfolio should return error", async () => {
    const result = await tool.execute({
      action: "run",
      strategyIds: "dual-ma-crossover",
    });

    // Should error since no portfolio exists and no symbols given
    expect(result).toHaveProperty("error");
  });

  it("action=run with symbols should execute backtest", async () => {
    const result = await tool.execute({
      action: "run",
      symbols: "000300",
      strategyIds: "dual-ma-crossover",
      startDate: "2024-01-01",
      endDate: "2024-06-30",
      initialCash: 100_000,
    });

    expect(result).toHaveProperty("portfolio");
    expect(result).toHaveProperty("results");
    const res = result as {
      portfolio: { positions: unknown[] };
      results: { strategyId: string; totalReturn: string };
    };
    expect(res.results.strategyId).toBe("dual-ma-crossover");
    expect(res.results.totalReturn).toBeDefined();
  });

  it("action=compare should compare multiple strategies", async () => {
    const result = await tool.execute({
      action: "compare",
      symbols: "000300",
      strategyIds: "dual-ma-crossover,momentum-breakout",
      startDate: "2024-01-01",
      endDate: "2024-06-30",
      initialCash: 100_000,
    });

    expect(result).toHaveProperty("comparison");
    const res = result as { comparison: unknown[]; bestStrategy: string | null };
    expect(res.comparison.length).toBeGreaterThanOrEqual(1);
    expect(res.bestStrategy).toBeDefined();
  });
});
