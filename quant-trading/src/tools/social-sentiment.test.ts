import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

import { createSocialSentimentTool } from "./social-sentiment.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createSocialSentimentTool(api, {} as never);
}

describe("social_sentiment tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("social_sentiment");
    expect(tool.description).toContain("舆情");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports all expected actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("hot_stocks");
    expect(actionProp.enum).toContain("stock_sentiment");
    expect(actionProp.enum).toContain("sentiment_history");
    expect(actionProp.enum).toContain("retail_flow");
  });

  it("returns error when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "hot_stocks" })) as Record<string, unknown>;
    expect(result.error).toBeDefined();
  });

  it("returns hot stocks list", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "hot_stocks",
        hot_stocks: [
          { symbol: "600519", name: "贵州茅台", rank: 1, price: 1720, change_pct: 2.5 },
          { symbol: "000858", name: "五粮液", rank: 2, price: 150, change_pct: 1.8 },
        ],
        count: 2,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "hot_stocks",
    })) as Record<string, unknown>;

    expect(result.hot_stocks).toBeDefined();
    expect(result.count).toBe(2);
  });

  it("returns stock sentiment analysis", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "stock_sentiment",
        symbol: "600519",
        sentiment_score: 78,
        hot_rank: 5,
        comment_summary: { bullish: 65, bearish: 20, neutral: 15 },
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "stock_sentiment",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.symbol).toBe("600519");
    expect(result.sentiment_score).toBe(78);
  });

  it("returns retail flow data", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "retail_flow",
        symbol: "600519",
        flows: [{ date: "2026-03-10", retail_net: -5000000 }],
        count: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "retail_flow",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.flows).toBeDefined();
    expect(result.count).toBe(1);
  });
});
