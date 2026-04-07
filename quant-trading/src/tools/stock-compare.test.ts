import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createStockCompareTool } from "./stock-compare.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createStockCompareTool(api, {} as never);
}

describe("stock_compare tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("stock_compare");
    expect(tool.description).toContain("对比");
    expect(tool.parameters.required).toContain("symbols");
  });

  it("supports compare action", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("compare");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ symbols: "600519,000858" })) as Record<string, unknown>;
    expect(result.note).toContain("股票对比引擎未就绪");
  });

  it("passes symbols to Python engine", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "compare",
        stocks: [
          { symbol: "600519", name: "贵州茅台", fundamentals: { pe_ttm: 30 } },
          { symbol: "000858", name: "五粮液", fundamentals: { pe_ttm: 20 } },
        ],
        rankings: { pe_ttm: [{ symbol: "000858", rank: 1 }] },
        count: 2,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "compare",
      symbols: "600519,000858",
    })) as Record<string, unknown>;

    expect(result.stocks).toBeDefined();
    expect(result.count).toBe(2);
    expect(mock).toHaveBeenCalledWith(
      "engines/stock_compare.py",
      expect.objectContaining({ symbols: "600519,000858" }),
      expect.anything(),
    );
  });

  it("adds display hint for comparison results", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: { stocks: [{}, {}, {}], count: 3 },
    });

    const tool = makeTool();
    const result = (await tool.execute({ symbols: "600519,000858,603288" })) as Record<
      string,
      unknown
    >;
    expect(result.displayHint).toContain("3只股票");
  });
});
