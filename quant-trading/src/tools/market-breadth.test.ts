import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createMarketBreadthTool } from "./market-breadth.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createMarketBreadthTool(api, {} as never);
}

describe("market_breadth tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("market_breadth");
    expect(tool.description).toContain("涨跌");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports overview, advance_decline, new_highs_lows, breadth_trend actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("overview");
    expect(actionProp.enum).toContain("advance_decline");
    expect(actionProp.enum).toContain("new_highs_lows");
    expect(actionProp.enum).toContain("breadth_trend");
  });

  it("returns fallback when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "overview" })) as Record<string, unknown>;
    expect(result.note).toContain("市场宽度引擎未就绪");
  });

  it("returns market breadth overview data", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "overview",
        breadth: {
          advancing: 2500,
          declining: 2000,
          flat: 300,
          ad_ratio: 1.25,
          limit_up: 45,
          limit_down: 10,
        },
        sentiment_score: 62,
        sentiment_label: "偏多",
        market_turnover: { total_amount_yi: 12000 },
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({ action: "overview" })) as Record<string, unknown>;

    expect(result.breadth).toBeDefined();
    expect(result.sentiment_score).toBe(62);
    expect(result.sentiment_label).toBe("偏多");
  });

  it("passes days parameter for advance_decline", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: { action: "advance_decline", history: [] },
    });

    const tool = makeTool();
    await tool.execute({ action: "advance_decline", days: 10 });

    expect(mock).toHaveBeenCalledWith(
      "engines/market_breadth.py",
      expect.objectContaining({ action: "advance_decline", days: 10 }),
      expect.anything(),
    );
  });
});
