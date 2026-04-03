/**
 * Tests for the portfolio AI tool.
 */
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import type { QuantTradingConfig } from "../types.js";
import { createPortfolioTool } from "./portfolio.js";

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

describe("portfolio tool", () => {
  let tmpDir: string;
  let config: QuantTradingConfig;

  beforeAll(async () => {
    tmpDir = path.join(os.tmpdir(), `quant-test-${Date.now()}`);
    await fs.mkdir(tmpDir, { recursive: true });
    config = { dataDir: tmpDir };
  });

  afterAll(async () => {
    await fs.rm(tmpDir, { recursive: true, force: true });
  });

  it("action=show should return default portfolio", async () => {
    const tool = createPortfolioTool(fakeApi(), config);
    const result = (await tool.execute({ action: "show" })) as Record<string, unknown>;
    expect(result.name).toBe("默认组合");
    expect(result.positions).toEqual([]);
  });

  it("action=buy should add a position", async () => {
    const tool = createPortfolioTool(fakeApi(), config);
    const result = (await tool.execute({
      action: "buy",
      symbol: "600519",
      name: "贵州茅台",
      quantity: 50,
      price: 18,
    })) as Record<string, unknown>;
    expect(result.success).toBe(true);
    expect(result.message as string).toContain("600519");
  });

  it("action=show should reflect the buy", async () => {
    const tool = createPortfolioTool(fakeApi(), config);
    const result = (await tool.execute({ action: "show" })) as Record<string, unknown>;
    const positions = result.positions as Array<Record<string, unknown>>;
    expect(positions.length).toBe(1);
    expect(positions[0]!.symbol).toBe("600519");
    expect(positions[0]!.quantity).toBe(50);
  });

  it("action=sell should reduce position", async () => {
    const tool = createPortfolioTool(fakeApi(), config);
    const result = (await tool.execute({
      action: "sell",
      symbol: "600519",
      quantity: 20,
      price: 19,
    })) as Record<string, unknown>;
    expect(result.success).toBe(true);

    const show = (await tool.execute({ action: "show" })) as Record<string, unknown>;
    const positions = show.positions as Array<Record<string, unknown>>;
    expect(positions[0]!.quantity).toBe(30);
  });

  it("action=sell too many shares should error", async () => {
    const tool = createPortfolioTool(fakeApi(), config);
    const result = (await tool.execute({
      action: "sell",
      symbol: "600519",
      quantity: 999,
      price: 19,
    })) as Record<string, unknown>;
    expect(result.error).toBeDefined();
  });

  it("action=buy without enough cash should error", async () => {
    const tool = createPortfolioTool(fakeApi(), config);
    const result = (await tool.execute({
      action: "buy",
      symbol: "000001",
      quantity: 10000,
      price: 999999,
    })) as Record<string, unknown>;
    expect(result.error).toBeDefined();
    expect(result.error as string).toContain("资金不足");
  });

  it("action=summary should show totals", async () => {
    const tool = createPortfolioTool(fakeApi(), config);
    const result = (await tool.execute({ action: "summary" })) as Record<string, unknown>;
    expect(result.positionCount).toBe(1);
  });
});
