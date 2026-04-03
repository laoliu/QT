import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
/**
 * Tests for the backtest AI tool.
 */
import { describe, expect, it } from "vitest";
import { createBacktestTool } from "./backtest.js";

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

describe("backtest tool", () => {
  const tool = createBacktestTool(fakeApi(), { dataProvider: "demo" });

  it("should have correct name and description", () => {
    expect(tool.name).toBe("backtest");
    expect(tool.description).toContain("回测");
  });

  it("action=list should return strategy list", async () => {
    const result = await tool.execute({ action: "list" });
    expect(result).toHaveProperty("strategies");
    const strategies = (result as { strategies: unknown[] }).strategies;
    expect(strategies.length).toBeGreaterThanOrEqual(3);
  });

  it("action=run should execute a backtest", async () => {
    const result = await tool.execute({
      action: "run",
      strategyId: "dual-ma-crossover",
      symbols: "000300",
      startDate: "2024-01-01",
      endDate: "2024-06-30",
      initialCash: 100_000,
    });

    expect(result).toHaveProperty("summary");
    const summary = (result as { summary: Record<string, unknown> }).summary;
    expect(summary.strategy).toBe("dual-ma-crossover");
    expect(summary.totalReturn).toBeDefined();
    expect(summary.annualizedReturn).toBeDefined();
    expect(summary.maxDrawdown).toBeDefined();
    expect(summary.sharpeRatio).toBeDefined();
  });

  it("action=run without strategyId should return error", async () => {
    const result = await tool.execute({ action: "run" });
    expect(result).toHaveProperty("error");
  });

  it("unknown action should return error", async () => {
    const result = await tool.execute({ action: "unknown" });
    expect(result).toHaveProperty("error");
  });
});
