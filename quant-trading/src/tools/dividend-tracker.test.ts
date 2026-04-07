import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createDividendTrackerTool } from "./dividend-tracker.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createDividendTrackerTool(api, {} as never);
}

describe("dividend_tracker tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("dividend_tracker");
    expect(tool.description).toContain("分红");
    expect(tool.description).toContain("除权除息");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports upcoming, history, calendar, high_yield actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("upcoming");
    expect(actionProp.enum).toContain("history");
    expect(actionProp.enum).toContain("calendar");
    expect(actionProp.enum).toContain("high_yield");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "upcoming" })) as Record<string, unknown>;
    expect(result.note).toContain("分红追踪引擎未就绪");
  });

  it("returns upcoming dividend data", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "upcoming",
        dividends: [
          { code: "600519", name: "贵州茅台", ex_date: "2026-04-15", cash_dividend: 2.5 },
        ],
        count: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({ action: "upcoming", days: 30 })) as Record<
      string,
      unknown
    >;

    expect(result.dividends).toBeDefined();
    expect(result.count).toBe(1);
  });

  it("returns dividend history for a stock", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "history",
        symbol: "600519",
        dividends: [
          { report_date: "2025-12-31", cash_per_share: 2.5, ex_date: "2026-04-15" },
        ],
        total_records: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "history",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.symbol).toBe("600519");
    expect(result.dividends).toBeDefined();
    expect(mock).toHaveBeenCalledWith(
      "engines/dividend_tracker.py",
      expect.objectContaining({ action: "history", symbol: "600519" }),
      expect.anything(),
    );
  });

  it("adds display hint for large dividend lists", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    const dividends = Array.from({ length: 15 }, (_, i) => ({
      code: `60051${i}`,
      name: `股票${i}`,
    }));
    mock.mockResolvedValueOnce({ ok: true, data: { dividends, count: 15 } });

    const tool = makeTool();
    const result = (await tool.execute({ action: "upcoming" })) as Record<string, unknown>;
    expect(result.displayHint).toContain("分红");
  });
});
