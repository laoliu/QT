import { describe, expect, it, vi } from "vitest";

// Mock python-bridge before imports
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createDragonTigerTool } from "./dragon-tiger.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createDragonTigerTool(api, {} as never);
}

describe("dragon_tiger tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("dragon_tiger");
    expect(tool.description).toContain("龙虎榜");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports daily, stock, and institution actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("daily");
    expect(actionProp.enum).toContain("stock");
    expect(actionProp.enum).toContain("institution");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "daily" })) as Record<string, unknown>;
    expect(result.note).toContain("龙虎榜引擎未就绪");
  });

  it("passes date and limit to Python engine", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "daily",
        date: "20260303",
        stocks: [
          { code: "000001", name: "平安银行", changePct: 5.2, netAmount: 100000000 },
          { code: "601318", name: "中国平安", changePct: 3.1, netAmount: 80000000 },
        ],
        count: 2,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "daily",
      date: "20260303",
      limit: 5,
    })) as Record<string, unknown>;

    expect(result.date).toBe("20260303");
    expect(result.stocks).toBeDefined();
    expect(mock).toHaveBeenCalledWith(
      "engines/dragon_tiger.py",
      expect.objectContaining({ action: "daily", date: "20260303", limit: 5 }),
      expect.anything(),
    );
  });

  it("adds display hint for large daily lists", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    const stocks = Array.from({ length: 15 }, (_, i) => ({
      code: `00000${i}`,
      name: `股票${i}`,
    }));
    mock.mockResolvedValueOnce({ ok: true, data: { stocks, count: 15 } });

    const tool = makeTool();
    const result = (await tool.execute({ action: "daily" })) as Record<string, unknown>;
    expect(result.displayHint).toContain("龙虎榜");
  });

  it("queries stock-specific dragon tiger history", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "stock",
        symbol: "600519",
        records: [{ appearances: 3, totalBuy: 500000000 }],
        count: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "stock",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.symbol).toBe("600519");
    expect(result.records).toBeDefined();
  });
});
