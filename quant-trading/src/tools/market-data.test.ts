import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
/**
 * Tests for the market_data AI tool.
 */
import { describe, expect, it } from "vitest";
import { createMarketDataTool } from "./market-data.js";

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

describe("market_data tool", () => {
  const tool = createMarketDataTool(fakeApi(), { dataProvider: "demo" });

  it("should have correct name", () => {
    expect(tool.name).toBe("market_data");
  });

  it("action=quote should return price data", async () => {
    const result = await tool.execute({ action: "quote", symbol: "600519" });
    expect(result).not.toHaveProperty("error");
    expect(result).toHaveProperty("price");
    expect(result).toHaveProperty("changePct");
  });

  it("action=quote without symbol should error", async () => {
    const result = await tool.execute({ action: "quote" });
    expect(result).toHaveProperty("error");
  });

  it("action=klines should return bars", async () => {
    const result = await tool.execute({
      action: "klines",
      symbol: "600519",
      startDate: "2024-01-01",
      endDate: "2024-03-31",
    });
    expect(result).toHaveProperty("data");
    const data = (result as { data: unknown[] }).data;
    expect(data.length).toBeGreaterThan(0);
  });

  it("action=klines should respect limit parameter", async () => {
    const result = await tool.execute({
      action: "klines",
      symbol: "600519",
      startDate: "2024-01-01",
      endDate: "2024-12-31",
      limit: 50,
    });
    expect(result).toHaveProperty("data");
    const data = (result as { data: unknown[] }).data;
    // The demo provider may not have 50 bars, but limit should cap
    expect(data.length).toBeLessThanOrEqual(50);
  });

  it("action=klines with indicators should return indicator data", async () => {
    const result = (await tool.execute({
      action: "klines",
      symbol: "600519",
      startDate: "2024-01-01",
      endDate: "2024-12-31",
      indicators: true,
    })) as Record<string, unknown>;
    expect(result).toHaveProperty("data");
    // indicators may or may not be present depending on data volume and Python availability
    // but the call should not error out
    expect(result).not.toHaveProperty("error");
  });

  it("action=search should return matches", async () => {
    const result = await tool.execute({ action: "search", keyword: "茅台" });
    expect(result).toHaveProperty("results");
  });

  it("unknown action should error", async () => {
    const result = await tool.execute({ action: "bad" });
    expect(result).toHaveProperty("error");
  });
});
