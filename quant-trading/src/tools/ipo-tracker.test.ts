import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createIpoTrackerTool } from "./ipo-tracker.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createIpoTrackerTool(api, {} as never);
}

describe("ipo_tracker tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("ipo_tracker");
    expect(tool.description).toContain("新股");
    expect(tool.description).toContain("打新");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports upcoming, recent, performance, subscription actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("upcoming");
    expect(actionProp.enum).toContain("recent");
    expect(actionProp.enum).toContain("performance");
    expect(actionProp.enum).toContain("subscription");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "upcoming" })) as Record<string, unknown>;
    expect(result.note).toContain("新股追踪引擎未就绪");
  });

  it("returns upcoming IPO data", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "upcoming",
        ipos: [
          {
            code: "301999",
            name: "测试新股",
            subscription_date: "2026-04-10",
            issue_price: 25.5,
            pe_ratio: 22.5,
          },
        ],
        count: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({ action: "upcoming" })) as Record<string, unknown>;

    expect(result.ipos).toBeDefined();
    expect(result.count).toBe(1);
  });

  it("returns recent IPO performance data", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "performance",
        stocks: [
          { code: "301999", name: "测试新股", ipo_return: 45.5, days_listed: 10 },
        ],
        summary: { avg_return: 45.5, positive_count: 1, negative_count: 0 },
        count: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "performance",
      days: 60,
    })) as Record<string, unknown>;

    expect(result.summary).toBeDefined();
    expect(result.stocks).toBeDefined();
  });

  it("adds display hint for multiple IPOs", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    const ipos = Array.from({ length: 8 }, (_, i) => ({
      code: `30100${i}`,
      name: `新股${i}`,
    }));
    mock.mockResolvedValueOnce({ ok: true, data: { ipos, count: 8 } });

    const tool = makeTool();
    const result = (await tool.execute({ action: "upcoming" })) as Record<string, unknown>;
    expect(result.displayHint).toContain("新股");
  });
});
