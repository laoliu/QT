import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createMacroDataTool } from "./macro-data.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createMacroDataTool(api, {} as never);
}

describe("macro_data tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("macro_data");
    expect(tool.description).toContain("宏观");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports all expected actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("indicators");
    expect(actionProp.enum).toContain("history");
    expect(actionProp.enum).toContain("calendar");
    expect(actionProp.enum).toContain("regime");
  });

  it("returns error when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "regime" })) as Record<string, unknown>;
    expect(result.error).toBeDefined();
  });

  it("passes indicator param to Python engine", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "history",
        indicator: "cpi",
        data: [{ date: "2026-01", value: 2.1 }],
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "history",
      indicator: "cpi",
    })) as Record<string, unknown>;

    expect(result.indicator).toBe("cpi");
    expect(result.data).toBeDefined();
  });

  it("returns regime analysis result", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "regime",
        regime: "recovery",
        pmi: 51.2,
        cpi_yoy: 1.8,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({ action: "regime" })) as Record<string, unknown>;
    expect(result.regime).toBe("recovery");
  });
});
