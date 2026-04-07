import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createTradingPlanTool } from "./trading-plan.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createTradingPlanTool(api, {} as never);
}

describe("trading_plan tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("trading_plan");
    expect(tool.description).toContain("交易计划");
    expect(tool.description).toContain("止损");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports generate and batch actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("generate");
    expect(actionProp.enum).toContain("batch");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({
      action: "generate",
      symbol: "600519",
    })) as Record<string, unknown>;
    expect(result.note).toContain("交易计划引擎未就绪");
  });

  it("generates a trading plan for a single stock", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "generate",
        plan: {
          symbol: "600519",
          name: "贵州茅台",
          current_price: 1700,
          trend: { direction: "上升趋势", ma20: 1680 },
          key_levels: {
            supports: [1680, 1660],
            resistances: [1720, 1750],
          },
          entry_zone: { low: 1675, high: 1685 },
          stop_loss: 1640,
          targets: { target_1: 1760, target_2: 1800 },
        },
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "generate",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.plan).toBeDefined();
    const plan = result.plan as Record<string, unknown>;
    expect(plan.symbol).toBe("600519");
    expect(plan.stop_loss).toBeDefined();
    expect(plan.targets).toBeDefined();
  });

  it("generates batch plans for multiple stocks", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "batch",
        plans: [
          { symbol: "600519", name: "贵州茅台", current_price: 1700 },
          { symbol: "000858", name: "五粮液", current_price: 150 },
        ],
        count: 2,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "batch",
      symbols: "600519,000858",
    })) as Record<string, unknown>;

    expect(result.plans).toBeDefined();
    expect(result.count).toBe(2);
    expect(mock).toHaveBeenCalledWith(
      "engines/trading_plan.py",
      expect.objectContaining({ action: "batch", symbols: "600519,000858" }),
      expect.anything(),
    );
  });

  it("accepts optional entry_price parameter", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: { action: "generate", plan: { symbol: "600519", entry_zone: { low: 1675, high: 1685 } } },
    });

    const tool = makeTool();
    await tool.execute({
      action: "generate",
      symbol: "600519",
      entry_price: 1680,
    });

    expect(mock).toHaveBeenCalledWith(
      "engines/trading_plan.py",
      expect.objectContaining({ entry_price: 1680 }),
      expect.anything(),
    );
  });
});
