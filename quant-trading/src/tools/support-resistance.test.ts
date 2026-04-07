import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createSupportResistanceTool } from "./support-resistance.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createSupportResistanceTool(api, {} as never);
}

describe("support_resistance tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("support_resistance");
    expect(tool.description).toContain("支撑");
    expect(tool.description).toContain("压力");
    expect(tool.parameters.required).toContain("symbol");
  });

  it("supports levels and pivot actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("levels");
    expect(actionProp.enum).toContain("pivot");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({
      action: "levels",
      symbol: "600519",
    })) as Record<string, unknown>;
    expect(result.note).toContain("支撑压力位引擎未就绪");
  });

  it("passes symbol and lookback to Python engine", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "levels",
        symbol: "600519",
        current_price: 1700,
        supports: [{ price: 1680, method: "swing", label: "前低" }],
        resistances: [{ price: 1720, method: "moving_average", label: "MA20" }],
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "levels",
      symbol: "600519",
      lookback: 90,
    })) as Record<string, unknown>;

    expect(result.supports).toBeDefined();
    expect(result.resistances).toBeDefined();
    expect(result.current_price).toBe(1700);
    expect(mock).toHaveBeenCalledWith(
      "engines/support_resistance.py",
      expect.objectContaining({ symbol: "600519", lookback: 90 }),
      expect.anything(),
    );
  });

  it("returns pivot points", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "pivot",
        symbol: "600519",
        standard: { PP: 1700, R1: 1720, S1: 1680 },
        fibonacci: { PP: 1700 },
        camarilla: { R1: 1710 },
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "pivot",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.standard).toBeDefined();
    expect(result.fibonacci).toBeDefined();
    expect(result.camarilla).toBeDefined();
  });
});
