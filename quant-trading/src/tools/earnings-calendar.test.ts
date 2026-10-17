import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

import { createEarningsCalendarTool } from "./earnings-calendar.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createEarningsCalendarTool(api, {} as never);
}

describe("earnings_calendar tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("earnings_calendar");
    expect(tool.description).toContain("财报");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports all expected actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("upcoming");
    expect(actionProp.enum).toContain("stock_forecast");
    expect(actionProp.enum).toContain("surprise_history");
    expect(actionProp.enum).toContain("analyst_ratings");
  });

  it("returns error when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "upcoming" })) as Record<string, unknown>;
    expect(result.error).toBeDefined();
  });

  it("passes symbol for stock_forecast", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "stock_forecast",
        symbol: "600519",
        forecasts: [{ year: "2026", eps_forecast: 52.3 }],
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "stock_forecast",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.symbol).toBe("600519");
    expect(result.forecasts).toBeDefined();
  });

  it("returns upcoming earnings list", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "upcoming",
        earnings: [{ symbol: "000001", name: "平安银行", disclosure_date: "2026-04-20" }],
        count: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "upcoming",
      days: 7,
    })) as Record<string, unknown>;

    expect(result.earnings).toBeDefined();
    expect(result.count).toBe(1);
  });
});
