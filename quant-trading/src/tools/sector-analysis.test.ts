import { describe, expect, it, vi } from "vitest";

// Mock python-bridge before imports
vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

// Mock plugin API
vi.mock("openclaw/plugin-sdk", () => ({}));

import { createSectorAnalysisTool } from "./sector-analysis.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createSectorAnalysisTool(api, {} as never);
}

describe("sector_analysis tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("sector_analysis");
    expect(tool.description).toContain("板块");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports ranking, detail, and history actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("ranking");
    expect(actionProp.enum).toContain("detail");
    expect(actionProp.enum).toContain("history");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "ranking" })) as Record<string, unknown>;
    expect(result.note).toContain("板块分析引擎未就绪");
  });

  it("passes action and sector to Python engine", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "detail",
        sector: "半导体",
        stockCount: 50,
        stocks: [{ code: "688981", name: "中芯国际" }],
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "detail",
      sector: "半导体",
      limit: 10,
    })) as Record<string, unknown>;

    expect(result.sector).toBe("半导体");
    expect(result.stocks).toBeDefined();
    expect(mock).toHaveBeenCalledWith(
      "engines/sector_analysis.py",
      expect.objectContaining({ action: "detail", sector: "半导体" }),
      expect.anything(),
    );
  });

  it("adds display hint for large ranking results", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    const sectors = Array.from({ length: 15 }, (_, i) => ({
      name: `板块${i}`,
      changePct: i,
    }));
    mock.mockResolvedValueOnce({ ok: true, data: { sectors, count: 15 } });

    const tool = makeTool();
    const result = (await tool.execute({ action: "ranking" })) as Record<string, unknown>;
    expect(result.displayHint).toContain("板块数据较多");
  });
});
