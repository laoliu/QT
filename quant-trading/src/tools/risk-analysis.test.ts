/**
 * Unit tests for the risk_analysis AI tool.
 *
 * Mocks runPythonScript to test parameter parsing, validation, and delegation.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { QuantTradingConfig } from "../types.js";

vi.mock("../bridge/python-bridge.js", () => ({
  runPythonScript: vi.fn(),
}));

import { runPythonScript } from "../bridge/python-bridge.js";
import { createRiskAnalysisTool } from "./risk-analysis.js";

const mockRun = vi.mocked(runPythonScript);
const fakeApi = {} as never;

const config: QuantTradingConfig = {
  dataDir: "/tmp/oc-quant-test",
  pythonPath: "python3",
} as QuantTradingConfig;

beforeEach(() => {
  vi.resetAllMocks();
});

const sampleCurve = JSON.stringify([
  { date: "2024-01-01", value: 100000 },
  { date: "2024-01-02", value: 101000 },
  { date: "2024-01-03", value: 99500 },
  { date: "2024-01-04", value: 102000 },
]);

describe("risk_analysis tool", () => {
  it("has correct name and required parameters", () => {
    const tool = createRiskAnalysisTool(fakeApi, config);
    expect(tool.name).toBe("risk_analysis");
    expect(tool.parameters.required).toContain("action");
  });

  describe("action=portfolio_risk", () => {
    it("parses equity curve and calls python engine", async () => {
      const riskResult = {
        sharpeRatio: 1.5,
        maxDrawdown: 0.015,
        var95: 0.02,
      };
      mockRun.mockResolvedValue({ ok: true, data: riskResult });

      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = await tool.execute({
        action: "portfolio_risk",
        equityCurve: sampleCurve,
        initialCash: 100000,
      });

      expect(result).toMatchObject({ action: "portfolio_risk", sharpeRatio: 1.5 });
      expect(mockRun).toHaveBeenCalledWith(
        "engines/report_engine.py",
        expect.objectContaining({
          equityCurve: expect.arrayContaining([expect.objectContaining({ date: "2024-01-01" })]),
          initialCash: 100000,
          riskFreeRate: 0.03,
        }),
        config,
      );
    });

    it("returns error when equity curve is invalid JSON", async () => {
      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = (await tool.execute({
        action: "portfolio_risk",
        equityCurve: "not json",
      })) as { error: string };
      expect(result.error).toContain("equityCurve 格式错误");
    });

    it("returns error when equity curve has fewer than 2 points", async () => {
      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = (await tool.execute({
        action: "portfolio_risk",
        equityCurve: JSON.stringify([{ date: "2024-01-01", value: 100000 }]),
      })) as { error: string };
      expect(result.error).toContain("至少 2 个");
    });

    it("includes benchmark curve when provided", async () => {
      mockRun.mockResolvedValue({ ok: true, data: { alpha: 0.1, beta: 0.9 } });
      const benchmark = JSON.stringify([
        { date: "2024-01-01", value: 3000 },
        { date: "2024-01-02", value: 3010 },
      ]);

      const tool = createRiskAnalysisTool(fakeApi, config);
      await tool.execute({
        action: "portfolio_risk",
        equityCurve: sampleCurve,
        benchmarkCurve: benchmark,
      });

      expect(mockRun).toHaveBeenCalledWith(
        "engines/report_engine.py",
        expect.objectContaining({
          benchmarkCurve: expect.arrayContaining([expect.objectContaining({ value: 3000 })]),
        }),
        config,
      );
    });

    it("ignores malformed benchmark without failing", async () => {
      mockRun.mockResolvedValue({ ok: true, data: { sharpeRatio: 1.0 } });

      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = await tool.execute({
        action: "portfolio_risk",
        equityCurve: sampleCurve,
        benchmarkCurve: "bad json",
      });
      expect(result).toHaveProperty("sharpeRatio", 1.0);
    });
  });

  describe("action=backtest_risk", () => {
    it("delegates to python engine same as portfolio_risk", async () => {
      mockRun.mockResolvedValue({ ok: true, data: { maxDrawdown: 0.05 } });

      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = await tool.execute({
        action: "backtest_risk",
        equityCurve: sampleCurve,
      });
      expect(result).toMatchObject({ action: "backtest_risk", maxDrawdown: 0.05 });
    });
  });

  describe("action=position_risk", () => {
    it("requires symbol parameter", async () => {
      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = (await tool.execute({ action: "position_risk" })) as { error: string };
      expect(result.error).toContain("标的代码");
    });

    it("returns position risk info with symbol (fallback when python unavailable)", async () => {
      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = (await tool.execute({
        action: "position_risk",
        symbol: "600519",
        period: 120,
      })) as { positionRisk: { symbol: string; period: string } };
      expect(result.positionRisk.symbol).toBe("600519");
      expect(result.positionRisk.period).toContain("120");
    });

    it("returns python risk data when available", async () => {
      mockRun.mockResolvedValue({
        ok: true,
        data: { var95: 0.05, maxDrawdown: 0.12, volatility: 0.25 },
      });
      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = (await tool.execute({
        action: "position_risk",
        symbol: "000001",
        period: 60,
      })) as Record<string, unknown>;
      expect(result).toMatchObject({
        action: "position_risk",
        symbol: "000001",
        period: 60,
        var95: 0.05,
        maxDrawdown: 0.12,
      });
    });
  });

  describe("error handling", () => {
    it("returns engine error when python fails", async () => {
      mockRun.mockResolvedValue({ ok: false, error: "numpy not installed" });

      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = (await tool.execute({
        action: "portfolio_risk",
        equityCurve: sampleCurve,
      })) as { error: string; hint: string };
      expect(result.error).toContain("numpy not installed");
      expect(result.hint).toBeDefined();
    });

    it("returns error for unknown action", async () => {
      const tool = createRiskAnalysisTool(fakeApi, config);
      const result = (await tool.execute({ action: "unknown_action" })) as { error: string };
      expect(result.error).toContain("未知操作");
    });
  });
});
