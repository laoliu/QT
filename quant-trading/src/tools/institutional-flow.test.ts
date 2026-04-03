import { describe, expect, it, vi } from "vitest";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn().mockResolvedValue({ ok: false, error: "python not available" }),
}));

vi.mock("openclaw/plugin-sdk", () => ({}));

import { createInstitutionalFlowTool } from "./institutional-flow.js";

function makeTool() {
  const api = { logger: { info: vi.fn(), warn: vi.fn(), error: vi.fn() } } as never;
  return createInstitutionalFlowTool(api, {} as never);
}

describe("institutional_flow tool", () => {
  it("has correct name and required fields", () => {
    const tool = makeTool();
    expect(tool.name).toBe("institutional_flow");
    expect(tool.description).toContain("机构");
    expect(tool.parameters.required).toContain("action");
  });

  it("supports all expected actions", () => {
    const tool = makeTool();
    const actionProp = tool.parameters.properties.action;
    expect(actionProp.enum).toContain("fund_holdings");
    expect(actionProp.enum).toContain("holding_changes");
    expect(actionProp.enum).toContain("block_trades");
    expect(actionProp.enum).toContain("insider_trades");
    expect(actionProp.enum).toContain("smart_money_signal");
  });

  it("returns error when Python unavailable", async () => {
    const tool = makeTool();
    const result = (await tool.execute({ action: "block_trades" })) as Record<string, unknown>;
    expect(result.error).toBeDefined();
  });

  it("returns block trade data", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "block_trades",
        trades: [
          { symbol: "600519", name: "贵州茅台", price: 1720.0, volume: 50000, premium: 1.2 },
        ],
        count: 1,
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "block_trades",
    })) as Record<string, unknown>;

    expect(result.trades).toBeDefined();
    expect(result.count).toBe(1);
  });

  it("returns smart money composite signal", async () => {
    const { runPythonScript } = await import("../bridge/python-bridge.js");
    const mock = vi.mocked(runPythonScript);
    mock.mockResolvedValueOnce({
      ok: true,
      data: {
        action: "smart_money_signal",
        symbol: "600519",
        composite_score: 72,
        signal: "bullish",
        components: {
          institutional_trend: 80,
          block_premium: 65,
          insider_direction: 70,
        },
      },
    });

    const tool = makeTool();
    const result = (await tool.execute({
      action: "smart_money_signal",
      symbol: "600519",
    })) as Record<string, unknown>;

    expect(result.composite_score).toBe(72);
    expect(result.signal).toBe("bullish");
  });
});
