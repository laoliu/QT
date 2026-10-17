import { describe, expect, it, vi } from "vitest";

// Mock python-bridge before imports
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

import { createFundFlowTool } from "./fund-flow.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createFundFlowTool(api, {} as never);
}

describe("fund_flow tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("fund_flow");
    expect(tool.description).toContain("资金流向");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports rank, stock, and sector actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("rank");
    expect(actionProp.enum).toContain("stock");
    expect(actionProp.enum).toContain("sector");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "rank" })) as Record<string, unknown>;
    expect(result.note).toContain("资金流向引擎未就绪");
  });

  it("passes stock symbol to Python engine", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "stock",
        symbol: "600519",
        flows: [{ date: "2026-03-03", mainNetIn: 50000000 }],
        count: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "stock",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.symbol).toBe("600519");
    expect(result.flows).toBeDefined();
  });

  it("adds display hint for large results", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    const stocks = Array.from({ length: 20 }, (_, i) => ({
      code: `00000${i}`,
      name: `股票${i}`,
      mainNetIn: i * 1000,
    }));
    mock.mockResolvedValueOnce({ ok: true, data: { stocks, count: 20 } });

    const tool = makeTool();
    const result = (await tool.execute({ action: "rank" })) as Record<string, unknown>;
    expect(result.displayHint).toContain("资金流向");
  });
});
